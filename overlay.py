"""Always-on-top glass overlay showing every running Claude Code session.

  green  -> Claude finished          (pulses 3x, stays lit until clicked)
  yellow -> Claude is waiting on you (pulses 3x, stays lit until answered)
  blue   -> working
  grey   -> idle

Also shows the 5-hour and 7-day plan limits (fed by statusline.py).
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

from common import DATA_DIR, command

STATUS_DIR = os.path.join(DATA_DIR, "status")
TITLE_DIR = os.path.join(DATA_DIR, "titles")
LIMITS = os.path.join(DATA_DIR, "limits.json")
CONFIG = os.path.join(DATA_DIR, "overlay-config.json")

POLL_MS = 500
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
    "idle": QColor("#8A8A96"),
}
LABELS = {"done": "done", "waiting": "needs you", "working": "working", "idle": "idle"}
BAR_FILL = QColor("#E8833A")  # orange
ORDER = {"waiting": 0, "done": 1, "working": 2, "idle": 3}

k32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
WORK_PERIOD = 1.0  # seconds per blink while working
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
        if title.lower() in ("claude code", "claude", "") or title.startswith("cm-focus-"):
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
        self.move(self.cfg.get("x", screen.right() - WIDTH - 16), self.cfg.get("y", screen.top() + 60))

        self.anim_timer = QTimer(self, timeout=self.update)
        self.anim_timer.start(33)
        self.poll_timer = QTimer(self, timeout=self.poll)
        self.poll_timer.start(POLL_MS)
        self.poll()

    # ------------------------------------------------ data
    def poll(self):
        now = time.time()
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
        sessions.sort(key=lambda s: (ORDER.get(s["state"], 9), s.get("cwd", "")))

        for s in sessions:
            key = self.alert_key(s)
            prev = self.pulse.get(s["session_id"])
            if key and (not prev or prev[0] != key):
                self.pulse[s["session_id"]] = (key, now)
            elif not key:
                self.pulse.pop(s["session_id"], None)

        self.sessions = sessions
        self.limits = read_json(LIMITS)
        self.resize(WIDTH, self.content_height())
        # Animate smoothly only while something is pulsing.
        working = any(s["state"] == "working" for s in sessions)
        self.anim_timer.setInterval(33 if working or self.any_pulsing() else 1000)

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

    def content_height(self):
        return BAR_H + GAP + len(self.sessions) * ROW_H + 2 * GAP + 2 * LIMIT_H + PAD - 2

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
            if s["state"] in ("done", "idle"):
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
        p.drawText(bar, Qt.AlignLeft | Qt.AlignVCenter, "claude")
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
        self.paint_limits(p, y + GAP)

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
        if self.hover == s["session_id"]:
            p.fillRect(r, QColor(255, 255, 255, 18))

        # Marker: a square that blinks hard, like a terminal cursor.
        if state == "working":
            lit = (time.time() % WORK_PERIOD) < WORK_PERIOD * 0.55
        elif alert:
            lit = self.intensity(s["session_id"]) > 0.5
        else:
            lit = True
        m = QRectF(PAD, r.center().y() - MARK / 2, MARK, MARK)
        if state == "done" and not alert:
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

    def paint_limits(self, p, y):
        lim = self.limits or {}
        now = time.time()
        fm = QFontMetrics(self.f_mono)
        x0 = PAD + fm.horizontalAdvance("5h") + 8
        x1 = WIDTH - PAD - fm.horizontalAdvance("100%") - 8
        for i, (key, label) in enumerate((("five_hour", "5h"), ("seven_day", "7d"))):
            row = QRectF(PAD, y + i * LIMIT_H, WIDTH - 2 * PAD, LIMIT_H)
            win = lim.get(key) or {}
            used = win.get("used")
            reset = parse_reset(win.get("resets_at"))
            if used is not None and reset and reset <= now:
                used = 0.0

            p.setFont(self.f_mono)
            p.setPen(QColor(255, 255, 255, 140))
            p.drawText(row, Qt.AlignLeft | Qt.AlignVCenter, label)
            p.setPen(QColor(255, 255, 255, 220))
            p.drawText(row, Qt.AlignRight | Qt.AlignVCenter, "--" if used is None else f"{used:.0f}%")

            track = QRectF(x0, row.center().y() - 1.5, x1 - x0, 3)
            p.fillRect(track, QColor(255, 255, 255, 34))
            if used:
                frac = max(0.0, min(1.0, used / 100))
                p.fillRect(QRectF(track.left(), track.top(), max(1.0, track.width() * frac), 3), BAR_FILL)


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
