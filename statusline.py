"""Claude Code statusLine command: saves the 5h / 7d plan limits for the overlay.

Claude Code pipes session JSON (including `rate_limits`) to this script on each
status refresh. It writes limits.json and prints nothing, so the terminal's
status line stays empty.
"""
import json
import os
import sys
import time

DATA_DIR = os.path.join(os.environ["LOCALAPPDATA"], "claude-monitor")


def window(raw):
    if not isinstance(raw, dict):
        return None
    used = raw.get("used_percentage", raw.get("utilization"))
    if used is None:
        return None
    return {"used": float(used), "resets_at": raw.get("resets_at")}


def main():
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    limits = payload.get("rate_limits") or {}
    record = {
        "five_hour": window(limits.get("five_hour")),
        "seven_day": window(limits.get("seven_day")),
        "updated": time.time(),
    }
    if not record["five_hour"] and not record["seven_day"]:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "limits.json")
    tmp = path + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f)
    os.replace(tmp, path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
