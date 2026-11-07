"""Codex CLI sessions for the overlay, read from Codex's own session logs (no hook needed).

Codex writes each session to ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl, one JSON
event per line: task_started / task_complete bracket a turn, turn_aborted is an
interrupt, and token_count events carry the plan limits (primary = 5 h window,
secondary = weekly). A log is matched to a running codex.exe by start order.
Codex creates the log only at the first prompt, so a fresh Codex has no row yet.
Format checked against openai/codex main (codex-rs/protocol, codex-rs/rollout), 7 Nov 2026.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import time
from datetime import datetime

CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
SESSIONS = os.path.join(CODEX_HOME, "sessions")
CODEX_EXES = ("codex.exe",)
QUIET = 15            # a log with no turn events counts as working this long after a write
k32 = ctypes.windll.kernel32
_cache = {}           # path -> (bytes read so far, parsed log)


def process_started(pid):
    """Creation time of `pid` as a unix timestamp, or None."""
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    times = [wt.FILETIME() for _ in range(4)]
    ok = k32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times])
    k32.CloseHandle(handle)
    if not ok:
        return None
    ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    return (ticks - 116444736000000000) / 1e7


def codex_pids(procs):
    """Outermost codex.exe processes (one per open Codex session)."""
    return [pid for pid, (parent, name) in procs.items()
            if name in CODEX_EXES and procs.get(parent, (0, ""))[1] not in CODEX_EXES]


def stamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def recent_logs(since):
    """(mtime, path) of every rollout log written since `since`. Resumed sessions append to
    a log in an older date folder, so the whole tree is walked."""
    found, stack = [], [SESSIONS]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                stack.append(e.path)
            elif e.name.startswith("rollout-") and e.name.endswith(".jsonl"):
                mtime = e.stat().st_mtime
                if mtime >= since:
                    found.append((mtime, e.path))
    return found


def parse(path, mtime, size):
    """The log's state so far. Read whole once, then only the bytes appended since: a long
    tool output can be megabytes, and turn and limit events must not fall out of view."""
    cached = _cache.get(path)
    if cached and cached[0] <= size:
        offset, log = cached
        if offset == size:
            return log
    else:  # new, or rewritten shorter
        offset = 0
        log = {"id": os.path.basename(path)[:-6], "cwd": "", "started": None, "start": None,
               "end": None, "aborted": False, "prompt": "", "limits": None, "main_limits": False,
               "has_turns": False}
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(size - offset)
    complete = data.rfind(b"\n") + 1  # a line still being written is read next time
    for raw in data[:complete].split(b"\n"):
        if b'"session_meta"' in raw[:200] and not offset:
            try:
                first = json.loads(raw)
            except ValueError:
                continue
            meta = first.get("payload") or {}
            log.update(id=meta.get("id") or log["id"], cwd=meta.get("cwd", ""),
                       started=stamp(meta.get("timestamp") or first.get("timestamp")))
            continue
        if b'"event_msg"' not in raw[:200]:  # skip big response items without parsing them
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if line.get("type") != "event_msg":
            continue
        payload = line.get("payload") or {}
        kind, ts = payload.get("type"), stamp(line.get("timestamp"))
        if kind in ("task_started", "turn_started"):
            log.update(start=ts, has_turns=True)
        elif kind in ("task_complete", "turn_complete", "turn_aborted"):
            log.update(end=ts, aborted=kind == "turn_aborted", has_turns=True)
        elif kind == "user_message":  # only in older ("legacy" history) logs
            text = (payload.get("message") or "").strip().splitlines()
            log["prompt"] = text[0][:80] if text else ""
            if not log["has_turns"]:
                log["start"] = ts
        elif kind == "token_count" and payload.get("rate_limits"):
            # Codex has several limit buckets; the main one ("codex", or unnamed in older
            # versions) is the 5h / weekly plan limit. Others only fill in if it never shows.
            raw = payload["rate_limits"]
            bucket = (raw.get("limit_id") or raw.get("limit_name") or "codex").lower()
            if bucket == "codex" or not log["limits"] or not log["main_limits"]:
                log.update(limits=(ts or mtime, raw), main_limits=bucket == "codex")
    log["started"] = log["started"] or os.path.getctime(path)
    _cache[path] = (offset + complete, log)
    return log


def state_of(log, mtime, now):
    start, end = log["start"], log["end"]
    if log["has_turns"]:
        if start and (not end or start > end):
            return "working"
        if end:
            return "idle" if log["aborted"] else "done"
        return "idle"
    return "working" if now - mtime < QUIET else "idle"  # older Codex: no turn events


def match(started, logs):
    """Pair logs with processes: each log goes to the newest unclaimed codex.exe that was
    already running when the session began. A resumed session began before its process,
    so it takes whatever process is left. A best guess: it only decides which terminal
    a click brings forward."""
    free = list(started)  # (start time, pid), oldest first
    pairs, resumed = [], []
    for item in sorted(logs, key=lambda item: item[0]["started"]):
        fits = [p for p in free if p[0] <= item[0]["started"] + 5]
        if fits:
            free.remove(fits[-1])
            pairs.append((fits[-1][1], item))
        else:
            resumed.append(item)
    for item in resumed:
        if free:
            pairs.append((free.pop(0)[1], item))
    return pairs


def sessions(procs, now=None):
    """One overlay record per running Codex session, newest log first."""
    now = now or time.time()
    pids = codex_pids(procs)
    if not pids:
        return []
    started = sorted((process_started(pid) or now, pid) for pid in pids)
    logs = []
    for mtime, path in sorted(recent_logs(started[0][0] - 5), reverse=True)[:len(pids)]:
        try:
            size = os.path.getsize(path)
            logs.append((parse(path, mtime, size), mtime))
        except (OSError, ValueError):
            continue
    records = []
    for pid, (log, mtime) in match(started, logs):
        records.append({
            "session_id": "codex-" + log["id"],
            "agent": "codex",
            "pid": pid,
            "cwd": log["cwd"],
            "state": state_of(log, mtime, now),
            "updated": log["end"] or mtime,
            "prompt": log["prompt"],
            "_limits": log["limits"],
            "_main_limits": log["main_limits"],
        })
    return records


def window(raw, at):
    if not isinstance(raw, dict) or raw.get("used_percent") is None:
        return None
    reset = raw.get("resets_at")
    if reset is None and raw.get("resets_in_seconds") is not None:
        reset = at + raw["resets_in_seconds"]
    return {"used": float(raw["used_percent"]), "resets_at": reset}


def limits(records):
    """The newest plan limits any running session has seen, shaped like limits.json."""
    seen = [r["_limits"] for r in records if r.get("_limits")]
    main = [r["_limits"] for r in records if r.get("_limits") and r.get("_main_limits")]
    seen = main or seen
    if not seen:
        return None
    at, raw = max(seen, key=lambda item: item[0])
    out = {"five_hour": None, "seven_day": None}
    for slot, fallback in (("primary", "five_hour"), ("secondary", "seven_day")):
        win = raw.get(slot)
        minutes = (win or {}).get("window_minutes")
        key = fallback if not minutes else "five_hour" if minutes <= 24 * 60 else "seven_day"
        out[key] = out[key] or window(win, at)
    return out if out["five_hour"] or out["seven_day"] else None
