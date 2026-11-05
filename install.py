"""Install / uninstall the Claude monitor.

  python install.py            add hooks to ~/.claude/settings.json, start overlay at login, launch it
  python install.py --remove   undo all of that
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOK = os.path.join(HERE, "hook.py").replace("\\", "/")
OVERLAY = os.path.join(HERE, "overlay.pyw")
PYTHON = sys.executable.replace("\\", "/")
PYTHONW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
STARTUP = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup",
                       "Claude Monitor.vbs")

CMD = f'"{PYTHON}" "{HOOK}"'
STATUS_CMD = f'"{PYTHON}" "{os.path.join(HERE, "statusline.py").replace(chr(92), "/")}"'
EVENTS = {
    "SessionStart": None, "UserPromptSubmit": None, "PermissionRequest": None,
    "Notification": None, "Stop": None, "StopFailure": None, "SessionEnd": None,
    "PreToolUse": "AskUserQuestion|ExitPlanMode",
    "PostToolUse": "*", "PostToolUseFailure": "*",
}


def is_ours(group):
    return any(HOOK in c or "claude-monitor/hook.py" in c
               for c in (h.get("command", "") for h in group.get("hooks", [])))


def update_settings(add):
    with open(SETTINGS, encoding="utf-8") as f:
        settings = json.load(f)
    shutil.copy(SETTINGS, SETTINGS + ".bak-monitor")
    hooks = settings.setdefault("hooks", {})
    for event, matcher in EVENTS.items():
        groups = [g for g in hooks.get(event, []) if not is_ours(g)]
        if add:
            group = {"hooks": [{"type": "command", "command": CMD, "timeout": 10}]}
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
    ours = "claude-monitor" in current.replace(" ", "-").lower() or "statusline.py" in current
    if add and (not current or ours):
        settings["statusLine"] = {"type": "command", "command": STATUS_CMD}
    elif add:
        print("Note: you already have a statusLine, so 5h/7d limits won't show in the overlay.")
    elif ours:
        settings.pop("statusLine", None)
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def main():
    remove = "--remove" in sys.argv
    update_settings(add=not remove)
    if remove:
        if os.path.exists(STARTUP):
            os.remove(STARTUP)
        print("Removed hooks and login item. Close the overlay via right-click > Quit.")
        return
    with open(STARTUP, "w", encoding="utf-8") as f:
        f.write(f'CreateObject("WScript.Shell").Run """{PYTHONW}"" ""{OVERLAY}""", 0, False\n')
    subprocess.Popen([PYTHONW, OVERLAY], creationflags=0x00000008)  # DETACHED_PROCESS
    print("Installed. Hooks added to", SETTINGS)
    print("Overlay launched and set to start at login.")
    print("Already-open Claude sessions pick up the hooks after a restart (or /hooks).")


if __name__ == "__main__":
    main()
