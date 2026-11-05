"""Bring the terminal tab running a given Claude process to the front.

  python focus.py <claude_pid>

Windows Terminal can't be asked "which tab owns PID x", so: attach to that
process's console, briefly set its title to a unique marker, find the tab with
that title via UI Automation, select it, raise the window, restore the title.
Falls back to the classic console window for non-Windows-Terminal hosts.
"""
import ctypes
import sys
import time

import comtypes.client

from common import log_error

k32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
k32.GetConsoleWindow.restype = ctypes.c_void_p

UIA_NAME = 30005
UIA_CLASSNAME = 30012
UIA_SELECTION_ITEM = 10010
SCOPE_CHILDREN, SCOPE_DESCENDANTS = 2, 4
MATCH_SUBSTRING = 2


def raise_window(hwnd):
    if not hwnd:
        return
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)


def select_tab(marker):
    try:  # bundled with the packaged app; generated on first use otherwise
        from comtypes.gen import UIAutomationClient as uia_mod
    except ImportError:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uia_mod

    uia = comtypes.client.CreateObject(uia_mod.CUIAutomation._reg_clsid_, interface=uia_mod.IUIAutomation)
    root = uia.GetRootElement()
    wt_cond = uia.CreatePropertyCondition(UIA_CLASSNAME, "CASCADIA_HOSTING_WINDOW_CLASS")
    name_cond = uia.CreatePropertyConditionEx(UIA_NAME, marker, MATCH_SUBSTRING)

    for _ in range(15):  # the title takes a moment to reach the tab
        windows = root.FindAll(SCOPE_CHILDREN, wt_cond)
        for i in range(windows.Length):
            win = windows.GetElement(i)
            tabs = win.FindAll(SCOPE_DESCENDANTS, name_cond)
            for j in range(tabs.Length):
                tab = tabs.GetElement(j)
                try:
                    pattern = tab.GetCurrentPattern(UIA_SELECTION_ITEM)
                    pattern.QueryInterface(uia_mod.IUIAutomationSelectionItemPattern).Select()
                except Exception:
                    continue
                return win.CurrentNativeWindowHandle
        time.sleep(0.08)
    return None


def focus(pid):
    k32.FreeConsole()
    if not k32.AttachConsole(pid):
        return
    buf = ctypes.create_unicode_buffer(1024)
    k32.GetConsoleTitleW(buf, 1024)
    old_title = buf.value
    console_hwnd = k32.GetConsoleWindow()
    marker = f"cm-focus-{pid}-{int(time.time() * 1000) % 100000}"
    hwnd = None
    try:
        k32.SetConsoleTitleW(marker)
        hwnd = select_tab(marker)
    finally:
        k32.SetConsoleTitleW(old_title)
        k32.FreeConsole()
    # Classic console windows are their own visible window.
    if not hwnd and console_hwnd and user32.IsWindowVisible(ctypes.c_void_p(console_hwnd)):
        hwnd = console_hwnd
    raise_window(ctypes.c_void_p(hwnd) if hwnd else None)


def run(args):
    try:
        focus(int(args[0]))
    except Exception:
        log_error("focus")


if __name__ == "__main__":
    run(sys.argv[1:])
