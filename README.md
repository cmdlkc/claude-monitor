# Claude Monitor

A small always-on-top overlay for Windows that shows every running coding agent session as one row with a coloured square, so you can see at a glance which sessions are finished, which are waiting for you, and how much of your plan limits is left.

Works with **Claude Code**, **Antigravity CLI (agy)** and **OpenAI Codex CLI**.

## Install

Open PowerShell and paste:

```powershell
irm https://raw.githubusercontent.com/cdilekci/claude-monitor/main/install.ps1 | iex
```

That downloads the latest release, installs it to `%LOCALAPPDATA%\Programs\Claude Monitor`, sets it to start at login and opens the overlay. Python is not needed. Claude Code sessions that are already open pick up the monitor after a restart (or `/hooks`).

Or download `Claude-Monitor.zip` from [Releases](https://github.com/cdilekci/claude-monitor/releases), unzip it anywhere and run `Claude Monitor.exe`.

## What the squares mean

| Square | Meaning |
| --- | --- |
| Green | Finished (pulses three times, stays lit until you click the row) |
| Yellow | Waiting for you: a question or a permission prompt |
| Blinking | Working: orange for Claude, blue for agy, white for Codex |
| Orange, hollow, slow blink | Claude's turn ended but background commands or subagents still run |
| Grey | Idle |

The top bar names the agents that are active (`claude`, or `claude, agy, codex`). Below the rows are the 5-hour and 7-day plan limits with their reset times: Claude's always, and Codex's (white bars) while Codex runs.

Drag to move. Click a row to acknowledge it and bring that session's terminal tab to the front. Right-click for options (acknowledge all, clear finished, uninstall, quit).

## How it works

Everything stays on your computer; nothing is sent anywhere.

- **Claude Code**: hooks in `~/.claude/settings.json` record each session's state, and the status line command saves the plan limits. If you already have your own `statusLine`, it is left alone and the limits show as `--`.
- **Antigravity (agy)**: hooks in `~/.gemini/config/hooks.json`. agy does not share its quota with other programs, so it has no limits block.
- **Codex**: nothing to set up. The overlay reads Codex's own session logs (`~/.codex/sessions`) for turn state and the plan limits. Codex does not log approval prompts, so a Codex row never turns yellow.

State files live in `%LOCALAPPDATA%\claude-monitor`; problems are logged to `error.log` there.

## Uninstall

Right-click the overlay and choose **Uninstall…**, or run:

```powershell
& "$env:LOCALAPPDATA\Programs\Claude Monitor\Claude Monitor.exe" uninstall
```

This removes the hooks, the status line and the login item. Then delete `%LOCALAPPDATA%\Programs\Claude Monitor`.

## Notes

- Windows 10 or 11 only.
- The app is not code-signed. If Windows Smart App Control is on, it may block it; SmartScreen may ask once before it runs.

## Run from source

Needs Python 3.10+ with `PySide6` and `comtypes`:

```
pip install PySide6 comtypes
python install.py            # install (python install.py --remove to undo)
python build.py              # package as Claude Monitor.exe (needs pyinstaller)
```
