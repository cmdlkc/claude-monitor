"""Install / uninstall the Claude monitor.

  python install.py            add hooks to ~/.claude/settings.json (and Antigravity's
                               ~/.gemini/config/hooks.json), start overlay at login, launch it
  python install.py --remove   undo all of that

The packaged app does the same via `Claude Monitor.exe install|uninstall`; installing
from the exe first copies the app to %LOCALAPPDATA%\\Programs\\Claude Monitor.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys

from common import DATA_DIR, FROZEN, HERE, command

SETTINGS = os.path.expanduser("~/.claude/settings.json")
GEMINI_DIR = os.path.expanduser("~/.gemini")
AGY_HOOKS = os.path.join(GEMINI_DIR, "config", "hooks.json")
AGY_HOOK_NAME = "claude-monitor"
AGY_EVENTS = ("SessionStart", "PreInvocation", "Stop")
AGY_WRAPPER = os.path.join(DATA_DIR, "agy-hook.cmd")
APP_DIR = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Claude Monitor")
EXE_NAME = "Claude Monitor.exe"
STARTUP = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup",
                       "Claude Monitor.vbs")

EVENTS = {
    "SessionStart": None, "UserPromptSubmit": None, "PermissionRequest": None,
    "Notification": None, "Stop": None, "StopFailure": None, "SessionEnd": None,
    "SubagentStart": None, "SubagentStop": None,
    "PreToolUse": "AskUserQuestion|ExitPlanMode",
    "PostToolUse": "*", "PostToolUseFailure": "*",
}


def message(text, error=False):
    if FROZEN:
        ctypes.windll.user32.MessageBoxW(None, text, "Claude Monitor", 0x10 if error else 0x40)
    else:
        print(text)


def shell_cmd(argv):
    """argv as a settings.json command string, with forward slashes like the hook docs use."""
    exe, *rest = argv
    if not FROZEN:
        exe = sys.executable  # hooks need python.exe, not pythonw, so stdin/stdout work
    parts = [f'"{exe}"'] + [f'"{a}"' if " " in a or "\\" in a else a for a in rest]
    return " ".join(parts).replace("\\", "/")


def is_ours(command_str):
    c = command_str.lower().replace("\\", "/")
    return ("claude monitor" in c or "claude-monitor" in c) and (
        "hook.py" in c or "statusline.py" in c or c.endswith(" hook") or c.endswith(" statusline"))


def update_settings(add):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            settings = json.load(f)
        shutil.copy(SETTINGS, SETTINGS + ".bak-monitor")
    except FileNotFoundError:
        settings = {}
    hook_cmd = shell_cmd(command("hook"))
    hooks = settings.setdefault("hooks", {})
    for event, matcher in EVENTS.items():
        groups = [g for g in hooks.get(event, [])
                  if not any(is_ours(h.get("command", "")) for h in g.get("hooks", []))]
        if add:
            group = {"hooks": [{"type": "command", "command": hook_cmd, "timeout": 10}]}
            if matcher:
                group = {"matcher": matcher, **group}
            groups.append(group)
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks")

    # Status line feeds the 5h / 7d limits; never clobber someone else's.
    current = settings.get("statusLine", {}).get("command", "")
    ours = is_ours(current)
    note = ""
    if add and (not current or ours):
        settings["statusLine"] = {"type": "command", "command": shell_cmd(command("statusline"))}
    elif add:
        note = "\n\nNote: you already have a statusLine, so 5h/7d limits won't show in the overlay."
    elif ours:
        settings.pop("statusLine", None)
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    return note


def short_path(path):
    """8.3 form of a path, so it has no spaces (falls back to the path itself)."""
    buf = ctypes.create_unicode_buffer(1024)
    return buf.value if ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024) else path


def agy_wrapper_command():
    """agy runs hooks as `cmd /c <command>` but escapes any quotes in it as \\", so the
    command itself must be quote-free: point it at a .cmd that holds the quoted one."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(AGY_WRAPPER, "w", encoding="utf-8") as f:
        f.write(f"@{shell_cmd(command('hook', 'agy'))} %*\n")
    return short_path(AGY_WRAPPER)  # backslashes: cmd reads a leading C:/ as a switch


def update_agy_hooks(add):
    """Register (or remove) our named hook in Antigravity's shared hooks.json."""
    if add and not os.path.isdir(GEMINI_DIR):
        return  # Antigravity isn't installed
    try:
        with open(AGY_HOOKS, encoding="utf-8-sig") as f:
            hooks = json.load(f)
        shutil.copy(AGY_HOOKS, AGY_HOOKS + ".bak-monitor")
    except FileNotFoundError:
        hooks = {}
    hooks.pop(AGY_HOOK_NAME, None)
    if not add and os.path.exists(AGY_WRAPPER):
        os.remove(AGY_WRAPPER)
    if add:
        wrapper = agy_wrapper_command()
        hooks[AGY_HOOK_NAME] = {
            event: [{"type": "command", "timeout": 10, "command": f"{wrapper} {event}"}]
            for event in AGY_EVENTS
        }
    if not hooks and not os.path.exists(AGY_HOOKS):
        return
    os.makedirs(os.path.dirname(AGY_HOOKS), exist_ok=True)
    with open(AGY_HOOKS, "w", encoding="utf-8") as f:
        json.dump(hooks, f, indent=2)


def installed_hook_command():
    """The hook command currently in settings.json if it is ours, else ''."""
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            groups = json.load(f).get("hooks", {}).get("Stop", [])
    except (OSError, ValueError):
        return ""
    return next((h["command"] for g in groups for h in g.get("hooks", [])
                 if is_ours(h.get("command", ""))), "")


def is_installed_here():
    return installed_hook_command() == shell_cmd(command("hook"))


def stop_other_instances():
    """Close running copies of the app so its files can be replaced."""
    subprocess.run(["taskkill", "/F", "/IM", EXE_NAME, "/FI", f"PID ne {os.getpid()}"],
                   creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install():
    if FROZEN and os.path.normcase(HERE) != os.path.normcase(APP_DIR):
        # Copy the app out of wherever it was downloaded / built, then let the copy finish.
        stop_other_instances()
        shutil.copytree(HERE, APP_DIR, dirs_exist_ok=True)
        subprocess.Popen([os.path.join(APP_DIR, EXE_NAME), "install"], creationflags=0x00000008)
        return
    note = update_settings(add=True)
    update_agy_hooks(add=True)
    overlay = command("overlay")
    with open(STARTUP, "w", encoding="utf-8") as f:
        args = " ".join(f'""{a}""' for a in overlay)
        f.write(f'CreateObject("WScript.Shell").Run "{args}", 0, False\n')
    subprocess.Popen(overlay, creationflags=0x00000008)  # DETACHED_PROCESS
    message("Claude Monitor is installed and will start at login.\n\n"
            "Already-open Claude / Antigravity sessions pick up the hooks after a restart. "
            "Codex needs no setup: it shows up as soon as it runs." + note)


def uninstall():
    update_settings(add=False)
    update_agy_hooks(add=False)
    if os.path.exists(STARTUP):
        os.remove(STARTUP)
    stop_other_instances()
    text = "Removed Claude Monitor's Claude / Antigravity hooks, status line and login item."
    if FROZEN:
        text += f"\n\nYou can now delete:\n{APP_DIR}"
    message(text)


def main():
    uninstall() if "--remove" in sys.argv else install()


if __name__ == "__main__":
    main()
