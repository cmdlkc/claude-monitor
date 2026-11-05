"""Package the monitor as a Windows app:  python build.py

Builds outside Google Drive (so sync doesn't churn on hundreds of Qt files), then
zips the result to release/Claude Monitor.zip. Unzip anywhere and run
Claude Monitor.exe: the first run installs it to %LOCALAPPDATA%\\Programs.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(tempfile.gettempdir(), "claude-monitor-build")
DIST = os.path.join(WORK, "dist")
NAME = "Claude Monitor"

# Qt modules the overlay never touches; dropping them roughly halves the app size.
EXCLUDE = ["PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf",
           "PySide6.QtWebEngineCore", "PySide6.QtMultimedia", "PySide6.QtOpenGL",
           "PySide6.QtSvg", "PySide6.QtDBus", "tkinter", "unittest", "pydoc"]


def uia_modules():
    """Generate comtypes' UI Automation wrappers now; a frozen app can't write them at runtime."""
    import comtypes.client
    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient
    return [UIAutomationClient.__name__, UIAutomationClient.__wrapper_module__.__name__]


def main():
    shutil.rmtree(WORK, ignore_errors=True)
    args = [sys.executable, "-m", "PyInstaller", os.path.join(HERE, "app.py"),
            "--name", NAME, "--windowed", "--noconfirm", "--clean",
            "--icon", os.path.join(HERE, "icon.ico"),
            "--paths", HERE, "--hidden-import", "comtypes.client",
            "--distpath", DIST, "--workpath", os.path.join(WORK, "build"), "--specpath", WORK]
    for mod in uia_modules():
        args += ["--hidden-import", mod]
    for mod in EXCLUDE:
        args += ["--exclude-module", mod]
    subprocess.run(args, check=True)

    release = os.path.join(HERE, "release")
    os.makedirs(release, exist_ok=True)
    zip_base = os.path.join(release, NAME)
    shutil.make_archive(zip_base, "zip", DIST, NAME)
    print("\nApp folder:", os.path.join(DIST, NAME))
    print("Release zip:", zip_base + ".zip")


if __name__ == "__main__":
    main()
