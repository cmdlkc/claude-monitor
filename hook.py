"""Claude Code / Antigravity hook: records each session's state for the overlay.

  hook.py                 Claude Code hook, wired in ~/.claude/settings.json
  hook.py agy <event>     Antigravity CLI hook, wired in ~/.gemini/config/hooks.json

Reads the hook payload from stdin and writes status/<session_id>.json. Never
blocks the agent: any error is swallowed and the script always exits 0 (with
no output for Claude, and `{}` for Antigravity, which expects JSON back).
"""
import json
import os
import subprocess
import sys
import time

from common import DATA_DIR, background_work, command, log_error, process_table

STATUS_DIR = os.path.join(DATA_DIR, "status")
DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW

# Tools that stop and wait for the user before they run to completion.
INTERACTIVE_TOOLS = {"AskUserQuestion", "ExitPlanMode"}


CLAUDE_EXES = ("claude.exe", "claude")
AGY_EXES = ("agy.exe", "antigravity.exe")


def find_agent_pid(names):
    """Walk up the process tree to the agent (claude.exe, agy.exe, ...) that spawned this hook."""
    procs = process_table()
    pid = os.getpid()
    for _ in range(12):
        parent = procs.get(pid)
        if not parent:
            return None
        pid = parent[0]
        info = procs.get(pid)
        if info and info[1] in names:
            # Electron apps nest helpers under a process of the same name; take the outermost.
            while procs.get(info[0], (0, ""))[1] == info[1]:
                pid, info = info[0], procs[info[0]]
            return pid
    return None


def ancestors():
    """This hook's own process chain (it may run inside a shell that claude.exe started)."""
    procs = process_table()
    chain, pid = set(), os.getpid()
    while pid in procs and pid not in chain and len(chain) < 12:
        chain.add(pid)
        pid = procs[pid][0]
    return chain


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
        if payload.get("agent_id"):  # a subagent's tool: a background one must not relight the row
            return "working" if current == "waiting" else None
        # The main agent used a tool, so it is working, whatever an earlier (possibly
        # stray mid-turn) Stop said. Already working: nothing to write.
        return "working" if current != "working" else None
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


def spawn(argv):
    subprocess.Popen(argv, creationflags=DETACHED, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def agy_state(event, payload):
    """Antigravity has no permission / notification events, so it never reports 'waiting'."""
    if event == "SessionStart":
        return "idle"
    if event == "PreInvocation":
        return "working"
    if event == "Stop":
        return "idle" if payload.get("terminationReason") == "USER_CANCELED" else "done"
    return None


def write_record(path, record):
    tmp = path + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f)
    for attempt in range(5):  # the overlay or a sync client may briefly hold the target open
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def main_agy(event):
    """Antigravity CLI (agy) hook. Event name comes from argv; the payload is camelCase."""
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    session = payload.get("conversationId")
    if not session:
        return
    if event == "SessionStart":
        spawn(command("overlay"))
    os.makedirs(STATUS_DIR, exist_ok=True)
    path = os.path.join(STATUS_DIR, f"{session}.json")
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError):
        record = {}

    state = agy_state(event, payload)
    workspaces = payload.get("workspacePaths") or []
    record.update({
        "session_id": session,
        "agent": "agy",
        "cwd": (workspaces[0] if workspaces else "") or record.get("cwd", ""),
        "state": state or record.get("state", "idle"),
        "updated": time.time(),
        "event": event,
    })
    if not record.get("pid"):
        record["pid"] = find_agent_pid(AGY_EXES)
    if event == "PreInvocation":
        prompt = (payload.get("lastUserInput") or "").strip().splitlines()
        if prompt:
            record["prompt"] = prompt[0][:80]
    if event == "SessionStart" and record["pid"]:
        # agy has no SessionEnd: a new conversation in the same process replaces the old one.
        for name in os.listdir(STATUS_DIR):
            other = os.path.join(STATUS_DIR, name)
            if name.endswith(".json") and other != path:
                try:
                    with open(other, encoding="utf-8") as f:
                        same = json.load(f).get("pid") == record["pid"]
                    if same:
                        os.remove(other)
                except (OSError, ValueError):
                    pass
    write_record(path, record)


def main():
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    event = payload.get("hook_event_name", "")
    session = payload.get("session_id")
    if not session:
        return
    if event == "SessionStart":
        spawn(command("overlay"))  # single-instance: no-op if already open
    os.makedirs(STATUS_DIR, exist_ok=True)
    path = os.path.join(STATUS_DIR, f"{session}.json")

    if event == "SessionEnd":
        try:
            os.remove(path)
        except OSError:
            pass
        return

    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError):
        record = {}

    state = new_state(event, payload, record.get("state"))

    # Background subagents keep running after the turn ends; remember which are live.
    agents = record.get("agents") or {}
    agent_id = payload.get("agent_id")
    if event == "SubagentStart" and agent_id:
        agents[agent_id] = time.time()
    elif event == "SubagentStop":
        agents.pop(agent_id, None)
    elif event == "SessionStart":
        agents = {}
    record["agents"] = agents
    if not record.get("pid"):
        record["pid"] = find_agent_pid(CLAUDE_EXES)

    # A finished turn with background work still running is not done yet.
    pid = record.get("pid")
    if state == "done" and background_work(pid, record, skip=ancestors()):
        state = "background"
    if event == "SubagentStop" and record.get("state") == "background" \
            and not background_work(pid, record, skip=ancestors()):
        state = "done"

    if state is None and record:
        if event in ("SubagentStart", "SubagentStop"):
            write_record(path, record)
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
    if event == "UserPromptSubmit":
        prompt = (payload.get("prompt") or "").strip().splitlines()
        record["prompt"] = prompt[0][:80] if prompt else ""

    write_record(path, record)


def run(args=()):
    agy = len(args) >= 2 and args[0] == "agy"
    try:
        main_agy(args[1]) if agy else main()
    except Exception:
        log_error("hook")
    if agy:  # agy expects a JSON object on stdout; {} means "carry on as normal"
        try:
            os.write(1, b"{}\n")
        except OSError:
            pass
    sys.exit(0)


if __name__ == "__main__":
    run(sys.argv[1:])
