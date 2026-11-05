"""Paths shared by every part of the monitor, whether run as scripts or as the packaged exe."""
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
