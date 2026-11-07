"""Paths shared by every part of the monitor, whether run as scripts or as the packaged exe."""
import ctypes
import ctypes.wintypes as wt
import os
import sys

DATA_DIR = os.path.join(os.environ["LOCALAPPDATA"], "claude-monitor")
FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
PYTHONW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")


def command(part, *args):
    """argv that runs one part of the monitor ('overlay', 'hook', 'focus', ...) with no console."""
    if FROZEN:
        return [sys.executable, part, *args]
    return [PYTHONW, os.path.join(HERE, part + ".py"), *args]


def log_error(part):
    """Append the current exception to error.log; the windowed exe has nowhere else to show it."""
    import time
    import traceback
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "error.log"), "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {part}\n{traceback.format_exc()}\n")
    except OSError:
        pass


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def process_table():
    """{pid: (parent pid, lowercase exe name)} for every running process."""
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
    return procs


# Claude Code runs Bash / PowerShell tool commands as direct children of claude.exe; a shell
# still alive after the turn has ended is a background command. MCP servers run as uv/cmd/node.
SHELL_EXES = ("bash.exe", "sh.exe", "powershell.exe", "pwsh.exe")
AGENT_MAX_AGE = 3 * 3600  # forget a subagent whose SubagentStop never arrived


def background_work(pid, record, procs=None, skip=()):
    """True while the Claude session `pid` still runs background shells or subagents."""
    import time
    now = time.time()
    if any(now - started < AGENT_MAX_AGE for started in (record.get("agents") or {}).values()):
        return True
    if not pid:
        return False
    procs = procs if procs is not None else process_table()
    return any(parent == pid and name in SHELL_EXES and child not in skip
               for child, (parent, name) in procs.items())
