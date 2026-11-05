"""Claude Code hook: records each session's state for the overlay.

Wired to several hook events in ~/.claude/settings.json. Reads the hook
payload from stdin and writes status/<session_id>.json. Never blocks Claude:
any error is swallowed and the script always exits 0 with no output.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time

DATA_DIR = os.path.join(os.environ["LOCALAPPDATA"], "claude-monitor")
STATUS_DIR = os.path.join(DATA_DIR, "status")
HERE = os.path.dirname(os.path.abspath(__file__))
PYTHONW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW

# Tools that stop and wait for the user before they run to completion.
INTERACTIVE_TOOLS = {"AskUserQuestion", "ExitPlanMode"}


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def find_claude_pid():
    """Walk up the process tree to the claude.exe that spawned this hook."""
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    procs = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    ok = k32.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        procs[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile.lower())
        ok = k32.Process32NextW(snap, ctypes.byref(entry))
    k32.CloseHandle(snap)

    pid = os.getpid()
    for _ in range(12):
        parent = procs.get(pid)
        if not parent:
            return None
        pid = parent[0]
        info = procs.get(pid)
        if info and info[1] in ("claude.exe", "claude"):
            return pid
    return None


def new_state(event, payload, current):
    if event in ("SessionStart",):
        return "idle"
    if event == "UserPromptSubmit":
        return "working"
    if event == "PreToolUse":
        return "waiting" if payload.get("tool_name") in INTERACTIVE_TOOLS else None
    if event == "PermissionRequest":
        return "waiting"
    if event in ("PostToolUse", "PostToolUseFailure"):
        return "working" if current == "waiting" else None
    if event == "Notification":
        ntype = payload.get("notification_type", "")
        msg = (payload.get("message") or "").lower()
        # The 60s "still waiting for your input" ping just means it's idle/done.
        if ntype == "idle_prompt" or "waiting for your input" in msg:
            return None
        return "waiting"
    if event in ("Stop", "StopFailure"):
        return "done"
    return None


def spawn(*args):
    subprocess.Popen([PYTHONW, *args], creationflags=DETACHED, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    event = payload.get("hook_event_name", "")
    session = payload.get("session_id")
    if not session:
        return
    if event == "SessionStart":
        spawn(os.path.join(HERE, "overlay.pyw"))  # single-instance: no-op if already open
    os.makedirs(STATUS_DIR, exist_ok=True)
    path = os.path.join(STATUS_DIR, f"{session}.json")

    if event == "SessionEnd":
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return

    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError):
        record = {}

    state = new_state(event, payload, record.get("state"))
    if state is None and record:
        return
    if state is None and event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        state = "working"  # first event seen from a session already mid-task

    record.update({
        "session_id": session,
        "cwd": payload.get("cwd") or record.get("cwd", ""),
        "state": state or record.get("state", "idle"),
        "updated": time.time(),
        "event": event,
    })
    if not record.get("pid"):
        record["pid"] = find_claude_pid()
    if event == "UserPromptSubmit":
        prompt = (payload.get("prompt") or "").strip().splitlines()
        record["prompt"] = prompt[0][:80] if prompt else ""

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f)
    os.replace(tmp, path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
