"""Always-on-top glass overlay showing every running Claude Code, Antigravity and Codex session.

  green  -> finished          (pulses 3x, stays lit until clicked)
  yellow -> waiting on you    (pulses 3x, stays lit until answered)
  blink  -> working: orange for Claude, blue for Antigravity (agy), white for Codex
  orange, hollow, slow blink -> turn finished but background commands or subagents still run
  grey   -> idle

The title names the active agents ("claude", "claude, agy, codex", ...). Below the rows
are the 5-hour and 7-day plan limits: Claude's (fed by statusline.py) and, while Codex
runs, Codex's (from its session logs), each bar in its agent's colour. Antigravity
keeps no usage data on disk, so it has no limits block.
Drag to move, click a row to acknowledge, right-click for options.
"""
import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                           QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

import codex
from common import DATA_DIR, background_work, command, log_error, process_table

STATUS_DIR = os.path.join(DATA_DIR, "status")
TITLE_DIR = os.path.join(DATA_DIR, "titles")
LIMITS = os.path.join(DATA_DIR, "limits.json")
CONFIG = os.path.join(DATA_DIR, "overlay-config.json")

POLL_MS = 500
PROC_POLL = 2.0  # seconds between process-list scans (agy, Codex)
PULSE_PERIOD = 0.75   # seconds per blink
PULSES = 3

WIDTH = 180
PAD = 9
BAR_H = 18
GAP = 5
ROW_H = 20
MARK = 6
LIMIT_H = 13

COLORS = {
    "done": QColor("#4FD1A1"),     # jade
    "waiting": QColor("#F2B23A"),  # amber
    "working": QColor("#E8833A"),  # orange, blinks while working
    "background": QColor("#E8833A"),
    "idle": QColor("#8A8A96"),
}
LABELS = {"done": "done", "waiting": "needs you", "working": "working", "background": "background", "idle": "idle"}
AGENTS = ("claude", "agy", "codex")  # title order
AGENT_COLORS = {  # working blink and limit bars
    "claude": QColor("#E8833A"),  # orange
    "agy": QColor("#4C8DFF"),     # blue
    "codex": QColor("#F2F2F2"),   # white
}
ORDER = {"waiting": 0, "done": 1, "working": 2, "background": 2, "idle": 3}

k32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
WORK_PERIOD = 1.0  # seconds per blink while working
BACKGROUND_PERIOD = 2.0  # slower blink while only background work runs
SPINNER = set("✳✶✻✽✢·◐◑◒◓* ")
_titles = {}  # pid -> (fetched at, title)


def fit_words(fm, text, width):
    """As many whole words as fit; only a single over-long word gets an ellipsis."""
    words = text.split()
    while len(words) > 1 and fm.horizontalAdvance(" ".join(words)) > width:
        words.pop()
    return fm.elidedText(" ".join(words), Qt.ElideRight, int(width))


def console_title(pid):
    """The tab title Claude Code sets for itself, minus its spinner glyph. Cached 3s."""
    cached = _titles.get(pid)
    if cached and time.time() - cached[0] < 3:
        return cached[1]
    title = ""
    k32.FreeConsole()
    if k32.AttachConsole(pid):
        buf = ctypes.create_unicode_buffer(512)
        k32.GetConsoleTitleW(buf, 512)
        k32.FreeConsole()
        title = buf.value.lstrip("".join(SPINNER)).strip()
        if title.lower() in ("claude code", "claude", "agy", "antigravity", "codex", "") or title.startswith("cm-focus-"):
            title = ""
    _titles[pid] = (time.time(), title)
    return title


# ---------------------------------------------------------------- helpers
def pid_alive(pid):
    if not pid:
        return True
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = wt.DWORD()
    k32.GetExitCodeProcess(handle, ctypes.byref(code))
    k32.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def save_config(cfg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except OSError:
        pass


def fmt_age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m"
    return f"{seconds // 86400}d {seconds % 86400 // 3600}h"


def parse_reset(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e12 else value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def mix(a, b, t):
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def alpha(color, a):
    c = QColor(color)
    c.setAlpha(max(0, min(255, int(a))))
    return c


# ---------------------------------------------------------------- overlay
class Overlay(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)

        self.cfg = read_json(CONFIG, {}) or {}
        self.acked = dict(self.cfg.get("acked", {}))
        self.sessions = []
        self.limits = None
        self.agy_running = False
        self.codex_running = False
        self.codex_sessions = []
        self.codex_limits = None
        self.cleared = {}     # Codex session_id -> updated, hidden by "Clear finished"
        self._proc_scan = 0.0
        self.pulse = {}       # session_id -> (alert key, start time)
        self.row_rects = []   # (QRectF, session_id)
        self.hover = None
        self._drag = None

        self.f_name = QFont("Geist", 8)
        self.f_name.setWeight(QFont.Medium)
        self.f_name_alert = QFont("Geist", 8)
        self.f_name_alert.setWeight(QFont.DemiBold)
        for f in (self.f_name, self.f_name_alert):
            f.setLetterSpacing(QFont.PercentageSpacing, 96)
        self.f_mono = QFont("Geist Mono", 7)

        self.menu = QMenu(self)
        self.menu.setStyleSheet("""
            QMenu { background: #000; color: #fff; border: 1px solid rgba(255,255,255,60);
                    padding: 4px; font: 8pt 'Geist Mono'; }
            QMenu::item { padding: 5px 16px; }
            QMenu::item:selected { background: rgba(255,255,255,30); }
            QMenu::separator { height: 1px; background: rgba(255,255,255,30); margin: 4px 8px; }
        """)
        for label, fn in [("Acknowledge all", self.ack_all), ("Clear finished / idle", self.clear_finished)]:
            act = QAction(label, self)
            act.triggered.connect(fn)
            self.menu.addAction(act)
        self.menu.addSeparator()
        uninstall_act = QAction("Uninstall…", self)
        uninstall_act.triggered.connect(self.uninstall)
        self.menu.addAction(uninstall_act)
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(QApplication.quit)
        self.menu.addAction(quit_act)

        screen = QApplication.primaryScreen().availableGeometry()
        x, y = self.cfg.get("x", screen.right() - WIDTH - 16), self.cfg.get("y", screen.top() + 60)
        # A saved spot on a monitor that is no longer attached would leave the overlay unreachable.
        if not any(s.availableGeometry().contains(x + WIDTH // 2, y + BAR_H // 2)
                   for s in QApplication.screens()):
            x, y = screen.right() - WIDTH - 16, screen.top() + 60
        self.move(x, y)

        self.anim_timer = QTimer(self, timeout=self.update)
        self.anim_timer.start(33)
        self.poll_timer = QTimer(self, timeout=self.poll)
        self.poll_timer.start(POLL_MS)
        self.poll()

    # ------------------------------------------------ data
    def poll(self):
        now = time.time()
        procs = None
        if now - self._proc_scan > PROC_POLL:
            self._proc_scan = now
            procs = process_table()
            self.agy_running = any(name == "agy.exe" for _, name in procs.values())
            self.codex_running = bool(codex.codex_pids(procs))
            try:
                self.codex_sessions = codex.sessions(procs, now)
                # Keep the last limits seen: a log's recent tail may hold no token_count yet.
                self.codex_limits = codex.limits(self.codex_sessions) or self.codex_limits
            except Exception:  # an unexpected log format must not take the overlay down
                log_error("codex")
                self.codex_sessions = []
        sessions = []
        if os.path.isdir(STATUS_DIR):
            for name in os.listdir(STATUS_DIR):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(STATUS_DIR, name)
                s = read_json(path)
                if not s:
                    continue
                stale = not s.get("pid") and now - s.get("updated", 0) > 12 * 3600
                if stale or not pid_alive(s.get("pid")):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    continue
                s["_path"] = path
                s["_folder"] = os.path.basename(s.get("cwd", "").rstrip("\\/")) or "~"
                s["_name"] = (console_title(s["pid"]) if s.get("pid") else "") or s["_folder"]
                sessions.append(s)
        for c in self.codex_sessions:
            if self.cleared.get(c["session_id"]) == c["updated"]:
                continue
            s = dict(c)
            s["_folder"] = os.path.basename(s["cwd"].rstrip("\\/")) or "~"
            s["_name"] = console_title(s["pid"]) or s["_folder"]
            sessions.append(s)
        sessions.sort(key=lambda s: (ORDER.get(s["state"], 9), s.get("cwd", "")))

        for s in sessions:
            key = self.alert_key(s)
            prev = self.pulse.get(s["session_id"])
            if key and (not prev or prev[0] != key):
                self.pulse[s["session_id"]] = (key, now)
            elif not key:
                self.pulse.pop(s["session_id"], None)

        self.sessions = sessions
        if procs is not None:
            for s in sessions:
                if s["state"] == "background" and not background_work(s.get("pid"), s, procs):
                    self.finish_background(s, now)
        self.limits = read_json(LIMITS)
        self.resize(WIDTH, self.content_height())
        # Animate smoothly only while something is pulsing.
        working = any(s["state"] in ("working", "background") for s in sessions)
        self.anim_timer.setInterval(33 if working or self.any_pulsing() else 1000)

    def finish_background(self, s, now):
        """The last background job ended with no hook to report it: mark the session done."""
        record = {k: v for k, v in s.items() if not k.startswith("_")}
        record.update(state="done", updated=now, event="BackgroundDone")
        try:
            tmp = s["_path"] + ".overlay.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(record, f)
            os.replace(tmp, s["_path"])
        except OSError:
            return
        s.update(state="done", updated=now)
        self.pulse[s["session_id"]] = (self.alert_key(s), now)

    def alert_key(self, s):
        if s["state"] == "waiting":
            return ("waiting", s["updated"])
        if s["state"] == "done" and self.acked.get(s["session_id"]) != s["updated"]:
            return ("done", s["updated"])
        return None

    def intensity(self, sid):
        """1.0 = fully lit. Dips PULSES times after an alert starts, then holds at 1."""
        p = self.pulse.get(sid)
        if not p:
            return 1.0
        t = time.time() - p[1]
        if t >= PULSES * PULSE_PERIOD:
            return 1.0
        return 0.5 + 0.5 * math.cos(2 * math.pi * t / PULSE_PERIOD)

    def any_pulsing(self):
        now = time.time()
        return any(now - start < PULSES * PULSE_PERIOD for _, start in self.pulse.values())

    def active_agents(self):
        """Agents with a session or a running process, in title order."""
        found = {s.get("agent", "claude") for s in self.sessions}
        if self.agy_running:
            found.add("agy")
        if self.codex_running:  # Codex writes no log before the first prompt
            found.add("codex")
        return [a for a in AGENTS if a in found] or ["claude"]

    def limit_blocks(self):
        """(agent, limits) to draw: Claude's always, the others' while they are active."""
        blocks = [("claude", self.limits)]
        if "codex" in self.active_agents() and self.codex_limits:
            blocks.append(("codex", self.codex_limits))
        return blocks

    def content_height(self):
        blocks = len(self.limit_blocks())
        return (BAR_H + GAP + len(self.sessions) * ROW_H + 2 * GAP
                + blocks * 2 * LIMIT_H + (blocks - 1) * GAP + PAD - 2)

    # ------------------------------------------------ actions
    def open_terminal(self, sid):
        s = next((s for s in self.sessions if s["session_id"] == sid), None)
        if s and s.get("pid"):
            user32.AllowSetForegroundWindow(-1)  # ASFW_ANY: let the helper raise the terminal
            subprocess.Popen(command("focus", str(s["pid"])), creationflags=0x08000000)

    def ack(self, sid):
        for s in self.sessions:
            if s["session_id"] == sid and s["state"] == "done":
                self.acked[sid] = s["updated"]
        self.persist_acks()

    def ack_all(self):
        for s in self.sessions:
            if s["state"] == "done":
                self.acked[s["session_id"]] = s["updated"]
        self.persist_acks()

    def persist_acks(self):
        live = {s["session_id"] for s in self.sessions}
        self.acked = {k: v for k, v in self.acked.items() if k in live}
        self.cfg["acked"] = self.acked
        save_config(self.cfg)
        self.poll()
        self.update()

    def clear_finished(self):
        for s in self.sessions:
            if s["state"] not in ("done", "idle"):
                continue
            if s.get("agent") == "codex":  # the log is Codex's own: hide the row, never delete it
                self.cleared[s["session_id"]] = s["updated"]
                continue
            try:
                os.remove(s["_path"])
            except OSError:
                pass
        self.poll()
        self.update()

    def uninstall(self):
        answer = QMessageBox.question(None, "Claude Monitor",
                                      "Remove Claude Monitor's hooks and login item, and quit?")
        if answer == QMessageBox.Yes:
            import install
            install.uninstall()
            QApplication.quit()

    # ------------------------------------------------ mouse
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = (e.globalPosition().toPoint(), self.pos(), False)
        elif e.button() == Qt.RightButton:
            self.menu.popup(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._drag:
            start, origin, _ = self._drag
            delta = e.globalPosition().toPoint() - start
            if delta.manhattanLength() > 3:
                self._drag = (start, origin, True)
                self.move(origin + delta)
            return
        hover = next((sid for r, sid in self.row_rects if r.contains(e.position())), None)
        if hover != self.hover:
            self.hover = hover
            self.setCursor(Qt.PointingHandCursor if hover else Qt.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, e):
        if not self._drag:
            return
        moved = self._drag[2]
        self._drag = None
        if moved:
            self.cfg["x"], self.cfg["y"] = self.x(), self.y()
            save_config(self.cfg)
            return
        for r, sid in self.row_rects:
            if r.contains(e.position()):
                self.ack(sid)
                self.open_terminal(sid)

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    # ------------------------------------------------ painting
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        rect = QRectF(self.rect())

        # Sheet: black acetate with a hairline edge that takes the alert colour.
        p.fillRect(rect, QColor(0, 0, 0, 222))
        glow = self.alert_glow()
        edge = alpha(glow[0], 255 if glow[1] > 0.5 else 70) if glow else QColor(255, 255, 255, 36)
        p.setPen(QPen(edge, 1))
        p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))

        # Title strip (also the drag handle).
        bar = QRectF(PAD, 0, WIDTH - 2 * PAD, BAR_H)
        p.setFont(self.f_mono)
        p.setPen(QColor(255, 255, 255, 120))
        p.drawText(bar, Qt.AlignLeft | Qt.AlignVCenter, ", ".join(self.active_agents()))
        p.drawText(bar, Qt.AlignRight | Qt.AlignVCenter, "{%02d}" % len(self.sessions))
        y = BAR_H
        self.dashed_rule(p, y)
        y += GAP

        self.row_rects = []
        for s in self.sessions:
            r = QRectF(1, y, WIDTH - 2, ROW_H)
            self.row_rects.append((r, s["session_id"]))
            self.paint_row(p, r, s)
            y += ROW_H

        y += GAP
        self.dashed_rule(p, y)
        y += GAP
        blocks = self.limit_blocks()
        for agent, lim in blocks:
            self.paint_limits(p, y, lim, AGENT_COLORS[agent], tint=len(blocks) > 1)
            y += 2 * LIMIT_H + GAP

    def dashed_rule(self, p, y):
        pen = QPen(QColor(255, 255, 255, 46), 1)
        pen.setDashPattern([2, 3])
        p.setPen(pen)
        p.drawLine(QPointF(PAD, y + 0.5), QPointF(WIDTH - PAD, y + 0.5))

    def alert_glow(self):
        best = None
        for s in self.sessions:
            key = self.alert_key(s)
            if key and (best is None or key[0] == "waiting"):
                best = (COLORS[key[0]], self.intensity(s["session_id"]))
        return best

    def paint_row(self, p, r, s):
        state = s["state"]
        alert = self.alert_key(s) is not None
        color = COLORS.get(state, COLORS["idle"])
        if state in ("working", "background"):
            color = AGENT_COLORS.get(s.get("agent", "claude"), color)
        if self.hover == s["session_id"]:
            p.fillRect(r, QColor(255, 255, 255, 18))

        # Marker: a square that blinks hard, like a terminal cursor.
        if state == "working":
            lit = (time.time() % WORK_PERIOD) < WORK_PERIOD * 0.55
        elif state == "background":
            lit = (time.time() % BACKGROUND_PERIOD) < BACKGROUND_PERIOD * 0.55
        elif alert:
            lit = self.intensity(s["session_id"]) > 0.5
        else:
            lit = True
        m = QRectF(PAD, r.center().y() - MARK / 2, MARK, MARK)
        if state == "background":
            p.setPen(QPen(alpha(color, 230 if lit else 70), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(m.adjusted(0.5, 0.5, -0.5, -0.5))
        elif state == "done" and not alert:
            p.setPen(QPen(alpha(color, 200), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(m.adjusted(0.5, 0.5, -0.5, -0.5))
        elif state == "idle":
            p.fillRect(m.adjusted(1.5, 1.5, -1.5, -1.5), QColor(255, 255, 255, 90))
        else:
            p.fillRect(m, color if lit else alpha(color, 45))

        left = PAD + MARK + 8
        font = self.f_name_alert if alert else self.f_name
        p.setFont(font)
        p.setPen(QColor(255, 255, 255, 255 if alert else 215 if state != "idle" else 130))
        name = fit_words(QFontMetrics(font), s["_name"], WIDTH - PAD - left)
        p.drawText(QRectF(left, r.top(), WIDTH - PAD - left, r.height()), Qt.AlignLeft | Qt.AlignVCenter, name)

    def paint_limits(self, p, y, lim, fill, tint=False):
        """One agent's 5h / 7d rows; with several blocks the labels take the agent's colour."""
        lim = lim or {}
        now = time.time()
        fm = QFontMetrics(self.f_mono)
        pct_w = fm.horizontalAdvance("100%")
        when_w = fm.horizontalAdvance("Mon 00:00")
        x0 = PAD + fm.horizontalAdvance("5h") + 8
        x1 = WIDTH - PAD - pct_w - when_w - 14
        for i, (key, label) in enumerate((("five_hour", "5h"), ("seven_day", "7d"))):
            row = QRectF(PAD, y + i * LIMIT_H, WIDTH - 2 * PAD, LIMIT_H)
            win = lim.get(key) or {}
            used = win.get("used")
            reset = parse_reset(win.get("resets_at"))
            if used is not None and reset and reset <= now:
                used = 0.0

            p.setFont(self.f_mono)
            p.setPen(alpha(fill, 200) if tint else QColor(255, 255, 255, 140))
            p.drawText(row, Qt.AlignLeft | Qt.AlignVCenter, label)
            p.setPen(QColor(255, 255, 255, 220))
            p.drawText(row, Qt.AlignRight | Qt.AlignVCenter, "--" if used is None else f"{used:.0f}%")
            # Reset time, dim, just left of the percentage: clock time for 5h, day + time for 7d.
            if reset and reset > now:
                when = time.strftime("%H:%M" if key == "five_hour" else "%a %H:%M", time.localtime(reset))
                p.setPen(QColor(255, 255, 255, 95))
                p.drawText(row.adjusted(0, 0, -(pct_w + 6), 0), Qt.AlignRight | Qt.AlignVCenter, when)

            track = QRectF(x0, row.center().y() - 1.5, x1 - x0, 3)
            p.fillRect(track, QColor(255, 255, 255, 34))
            if used:
                frac = max(0.0, min(1.0, used / 100))
                p.fillRect(QRectF(track.left(), track.top(), max(1.0, track.width() * frac), 3), fill)


def single_instance():
    k32.CreateMutexW(None, False, "Local\\ClaudeMonitorOverlay")
    return k32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def run():
    if single_instance():
        app = QApplication(sys.argv)
        w = Overlay()
        w.show()
        sys.exit(app.exec())


if __name__ == "__main__":
    run()
