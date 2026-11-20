"""
TARZ voice overlay: a small floating capsule that pops up when the wake word fires.

Qt must own the main thread. Every public method below is thread-safe, so the wake-word,
STT, TTS and agent threads can call them directly.

    ov = VoiceOverlay()
    ov.on_cancel = cancel_event.set          # Esc hotkey / Esc button
    ov.set_state("listening")                # pops up
    ov.set_caption("play lofi on spotify")   # live STT text
    ov.set_state("thinking")
    ov.set_steps([("Open Spotify", "done"), ("Press play", "run"), ("Verify", "todo")])
    ov.set_state("speaking"); ov.set_caption("On it.")
    ov.done()                                # fades out after a moment
    ok = ov.confirm("Send this email to Sam at 9:00 AM?", "Running late, see you at 10.")
"""
import math
import sys
import threading
import time

from PySide6.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt,
                            QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QPainter, QPainterPath,
                           QPen, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

# state -> (main colour, deep colour, label)
STATES = {
    "idle":      ("#9a9ea5", "#3a3d42", "Idle"),
    "listening": ("#ff7a17", "#b8420a", "Listening"),
    "thinking":  ("#7c3aed", "#2e1065", "Thinking"),
    "speaking":  ("#f5f5f5", "#ff7a17", "Speaking"),
    "confirm":   ("#ffb347", "#b8420a", "Risky action"),
}
MONO = ["Geist Mono", "Consolas", "Menlo", "monospace"]
SANS = ["Inter", "Segoe UI", "Helvetica Neue", "sans-serif"]
WIDTH = 548          # window width, includes the shadow margin
MARGIN = 24          # shadow margin around the capsule


def _lerp(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def _closed_spline(pts):
    """Smooth closed curve through pts (Catmull-Rom converted to cubic beziers)."""
    n = len(pts)
    path = QPainterPath(pts[0])
    for i in range(n):
        p0, p1, p2, p3 = pts[(i - 1) %
                             n], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6,
                     p1.y() + (p2.y() - p0.y()) / 6)
        c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6,
                     p2.y() - (p3.y() - p1.y()) / 6)
        path.cubicTo(c1, c2, p2)
    return path


def _win_style(hwnd: int, click_through: bool):
    """Windows only: never take focus, hide from alt-tab, optionally let clicks pass through."""
    if sys.platform != "win32":
        return
    import ctypes
    u = ctypes.windll.user32
    get = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW
    put = getattr(u, "SetWindowLongPtrW", None) or u.SetWindowLongW
    GWL_EXSTYLE, TRANSPARENT, LAYERED, TOOLWINDOW, NOACTIVATE = - \
        20, 0x20, 0x80000, 0x80, 0x8000000
    style = get(hwnd, GWL_EXSTYLE) | LAYERED | TOOLWINDOW | NOACTIVATE
    style = (style | TRANSPARENT) if click_through else (style & ~TRANSPARENT)
    put(hwnd, GWL_EXSTYLE, style)


class Orb(QWidget):
    """Morphing blob with a glow. Colour cross-fades between states."""

    def __init__(self, size: int = 64):
        super().__init__()
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.a, self.b = QColor("#9a9ea5"), QColor("#3a3d42")
        self.ta, self.tb = QColor(self.a), QColor(self.b)
        self.phase, self.level, self.vel, self.target = 0.0, 0.0, 0.0, 0.0

    def set_colors(self, a: str, b: str):
        self.ta, self.tb = QColor(a), QColor(b)

    def step(self, dt: float):
        self.phase += dt * (1.2 + self.level * 2.5)
        self.vel = (self.vel + (self.target - self.level)
                    * 0.30) * 0.62   # spring
        self.level = max(0.0, min(1.0, self.level + self.vel))
        self.a, self.b = _lerp(self.a, self.ta, 0.12), _lerp(
            self.b, self.tb, 0.12)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        R = self.width() * 0.375
        pulse = 0.5 + 0.5 * math.sin(self.phase * 1.5)

        glow = QRadialGradient(c, self.width() * 0.5)
        g = QColor(self.a)
        g.setAlphaF(0.28 + 0.22 * pulse + 0.2 * self.level)
        glow.setColorAt(0.0, g)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, self.width() * 0.5, self.height() * 0.5)

        pts = []
        for i in range(24):
            th = i / 24 * math.tau
            r = R * (1 + 0.045 * math.sin(3 * th + self.phase)
                     + 0.03 * math.sin(5 * th - 1.3 * self.phase)
                     + self.level * 0.14 * math.sin(2 * th + 2 * self.phase))
            pts.append(QPointF(c.x() + r * math.cos(th),
                       c.y() + r * math.sin(th)))
        body = QRadialGradient(
            QPointF(c.x() - R * 0.3, c.y() - R * 0.4), R * 1.6)
        body.setColorAt(0.0, QColor("#ffffff"))
        body.setColorAt(0.38, self.a)
        body.setColorAt(1.0, self.b)
        p.setBrush(body)
        p.drawPath(_closed_spline(pts))


class Bars(QWidget):
    PROFILE = (0.45, 0.7, 1.0, 0.8, 0.95, 0.65, 0.5)

    def __init__(self):
        super().__init__()
        self.setFixedSize(46, 28)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.level, self.phase = 0.0, 0.0

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        for i, k in enumerate(self.PROFILE):
            v = max(0.0, min(1.0, self.level * k +
                    0.12 * math.sin(self.phase * 3 + i)))
            h = 4 + 22 * v
            p.drawRoundedRect(
                QRectF(i * 6.5, (self.height() - h) / 2, 3, h), 1.5, 1.5)


class VoiceOverlay(QWidget):
    _sig_state = Signal(str)
    _sig_caption = Signal(str)
    _sig_level = Signal(float)
    _sig_steps = Signal(list)
    _sig_confirm = Signal(str, str)
    _sig_resolve = Signal(bool)
    _sig_done = Signal(int)

    def __init__(self):
        super().__init__()
        self.on_cancel = None          # callable, set by the app
        self._state = "idle"
        # (Event, [bool]) while a confirm is open
        self._pending = None
        self._last_level = 0.0
        self._last_t = time.monotonic()
        self._anim = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(WIDTH)
        self.setWindowOpacity(0.0)

        self.orb, self.bars = Orb(64), Bars()
        self.label = QLabel("Idle")
        self.label.setFont(self._font(MONO, 9, caps=True))
        self.caption = QLabel("")
        self.caption.setWordWrap(True)
        self.caption.setMaximumHeight(46)
        self.caption.setFont(self._font(SANS, 12))
        self.esc = QLabel("Esc")
        self.esc.setFont(self._font(MONO, 9))
        self.esc.setStyleSheet("color:#9a9ea5;border:1px solid rgba(255,255,255,64);"
                               "border-radius:6px;padding:2px 8px;")
        self.label.setStyleSheet("color:#9a9ea5;")
        self.caption.setStyleSheet("color:#ffffff;")

        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self.label)
        text.addWidget(self.caption)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.orb)
        top.addLayout(text, 1)
        top.addWidget(self.bars, 0, Qt.AlignVCenter)
        top.addWidget(self.esc, 0, Qt.AlignVCenter)

        # task checklist (under the bar)
        self.steps_box = QWidget()
        self.steps_lay = QVBoxLayout(self.steps_box)
        self.steps_lay.setContentsMargins(74, 8, 0, 0)
        self.steps_lay.setSpacing(2)
        self.steps_box.hide()

        # approval card (under the bar)
        self.confirm_box = QWidget()
        cl = QVBoxLayout(self.confirm_box)
        cl.setContentsMargins(74, 0, 0, 0)
        cl.setSpacing(8)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet("color:#9a9ea5;")
        self.allow = QPushButton("Allow")
        self.skip = QPushButton("Skip")
        self.allow.setCursor(Qt.PointingHandCursor)
        self.skip.setCursor(Qt.PointingHandCursor)
        self.allow.setStyleSheet("QPushButton{background:#fff;color:#0a0a0a;border:1px solid #fff;"
                                 "border-radius:16px;padding:6px 18px;}")
        self.skip.setStyleSheet("QPushButton{background:transparent;color:#fff;"
                                "border:1px solid rgba(255,255,255,64);border-radius:16px;padding:6px 18px;}")
        self.hint = QLabel("or say \u201cyes\u201d")
        self.hint.setFont(self._font(MONO, 9))
        self.hint.setStyleSheet("color:#9a9ea5;")
        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addWidget(self.allow)
        btns.addWidget(self.skip)
        btns.addStretch(1)
        btns.addWidget(self.hint)
        cl.addWidget(self.detail)
        cl.addLayout(btns)
        self.confirm_box.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(MARGIN + 16, MARGIN +
                                10, MARGIN + 16, MARGIN + 12)
        root.setSpacing(0)
        root.addLayout(top)
        root.addWidget(self.steps_box)
        root.addWidget(self.confirm_box)

        self.allow.clicked.connect(lambda: self._resolve(True))
        self.skip.clicked.connect(lambda: self._resolve(False))
        self._sig_state.connect(self._on_state)
        self._sig_caption.connect(self._on_caption)
        self._sig_level.connect(self._on_level)
        self._sig_steps.connect(self._on_steps)
        self._sig_confirm.connect(self._on_confirm)
        self._sig_resolve.connect(self._resolve)
        self._sig_done.connect(self._on_done)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)
        self._on_state("idle", show=False)

    # ------------------------------------------------------------ public, thread-safe
    def set_state(self, state: str):
        self._sig_state.emit(state)

    def set_caption(self, text: str):
        self._sig_caption.emit(text)

    def set_level(self, level: float):
        """Mic or speaker amplitude 0..1. Optional: bars animate on their own if you never call it."""
        self._sig_level.emit(float(level))

    def set_steps(self, steps):
        """steps = [(text, 'done'|'run'|'todo'), ...]. Empty list hides the checklist."""
        self._sig_steps.emit(list(steps))

    def done(self, delay_ms: int = 2500):
        self._sig_done.emit(delay_ms)

    def answer_confirm(self, ok: bool):
        """Call from STT when the user says yes or no instead of clicking."""
        self._sig_resolve.emit(bool(ok))

    def request_cancel(self):
        """Wire to the Esc hotkey, e.g. keyboard.add_hotkey('esc', ov.request_cancel)."""
        if self.on_cancel:
            self.on_cancel()
        self.set_state("idle")

    def confirm(self, question: str, detail: str = "", timeout: float = 60.0) -> bool:
        """Blocks the CALLING (worker) thread until Allow/Skip/answer_confirm or timeout. Never call from the Qt thread."""
        ev, box = threading.Event(), [False]
        self._pending = (ev, box)
        self._sig_confirm.emit(question, detail)
        if not ev.wait(timeout):
            self._sig_resolve.emit(False)
            return False
        return bool(box[0])

    # ------------------------------------------------------------ slots (Qt thread)
    @staticmethod
    def _font(families, pt, caps=False):
        f = QFont()
        f.setFamilies(families)
        f.setPointSize(pt)
        if caps:
            f.setCapitalization(QFont.AllUppercase)
            f.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
        return f

    def _on_state(self, state: str, show: bool = True):
        if state not in STATES:
            return
        self._state = state
        a, b, name = STATES[state]
        self.orb.set_colors(a, b)
        self.label.setText(name)
        self.label.setStyleSheet(
            f"color:{'#ffb347' if state == 'confirm' else '#9a9ea5'};")
        self.bars.setVisible(state in ("listening", "speaking"))
        self.esc.setVisible(state != "confirm")
        if state == "idle":
            if show and self.isVisible():
                self._hide_timer.start(400)
            return
        self._hide_timer.stop()
        if show:
            self._show()

    def _on_caption(self, text: str):
        self.caption.setText(text)
        self._relayout()

    def _on_level(self, v: float):
        self._last_level, self._last_t = v, time.monotonic()
        self.orb.target = v

    def _on_steps(self, steps):
        while self.steps_lay.count():
            w = self.steps_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        glyph = {"done": ("\u2713", "#ffffff", "#dadbdf"),
                 "run": ("\u25cf", "#ff7a17", "#ffffff"),
                 "todo": ("\u25cb", "#7d8187", "#7d8187")}
        for text, status in steps:
            g, gc, tc = glyph.get(status, glyph["todo"])
            row = QLabel(
                f"<span style='color:{gc}'>{g}</span>&nbsp;&nbsp;{text}")
            row.setStyleSheet(f"color:{tc};")
            row.setTextFormat(Qt.RichText)
            self.steps_lay.addWidget(row)
        self.steps_box.setVisible(bool(steps))
        self._relayout()

    def _on_confirm(self, question: str, detail: str):
        self.steps_box.hide()
        self.caption.setText(question)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.confirm_box.show()
        self._on_state("confirm")
        self._apply_click_through(False)
        self._relayout()

    def _resolve(self, ok: bool):
        if self._pending is None:
            return
        ev, box = self._pending
        self._pending = None
        box[0] = ok
        ev.set()
        self.confirm_box.hide()
        self._on_state("thinking")
        self._apply_click_through(True)
        self._relayout()

    def _on_done(self, delay_ms: int):
        self._on_state("idle", show=False)
        self._hide_timer.start(delay_ms)

    # ------------------------------------------------------------ window handling
    def _relayout(self):
        lay = self.layout()
        h = lay.totalHeightForWidth(
            WIDTH) if lay.hasHeightForWidth() else lay.sizeHint().height()
        self.setFixedHeight(max(h, 110))
        self._reposition()
        self.update()

    def _reposition(self):
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.center().x() - WIDTH // 2,
                  geo.bottom() - self.height() - 8)

    def _apply_click_through(self, on: bool):
        if self.isVisible():
            _win_style(int(self.winId()), on)

    def _fade(self, to: float, ms: int, then=None):
        if self._anim:
            self._anim.stop()
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(ms)
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(to)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        if then:
            self._anim.finished.connect(then)
        self._anim.start()

    def _show(self):
        self._relayout()
        if not self.isVisible():
            self.show()
            self._apply_click_through(self._pending is None)
        self._last_t = time.monotonic()
        self._timer.start()
        self._fade(1.0, 160)

    def _fade_out(self):
        self._fade(0.0, 240, then=self._after_fade)

    def _after_fade(self):
        if self.windowOpacity() <= 0.01 and self._state == "idle":
            self._timer.stop()
            self.hide()

    def _tick(self):
        dt = 0.016
        now = time.monotonic()
        if self._state in ("listening", "speaking") and now - self._last_t > 0.3:
            # no real audio level coming in: breathe so it still feels alive
            self.orb.target = 0.35 + 0.25 * math.sin(self.orb.phase * 2.3)
        elif self._state in ("thinking", "confirm"):
            self.orb.target = 0.12
        elif self._state == "idle":
            self.orb.target = 0.0
        self.orb.step(dt)
        self.bars.level, self.bars.phase = self.orb.level, self.orb.phase
        self.bars.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(MARGIN, MARGIN, self.width() - 2 *
                      MARGIN, self.height() - 2 * MARGIN)
        radius = min(28.0, rect.height() / 2)
        # soft shadow without QGraphicsBlurEffect (it misbehaves on the Windows compositor)
        p.setPen(Qt.NoPen)
        for i in range(10, 0, -1):
            p.setBrush(QColor(0, 0, 0, 7))
            p.drawRoundedRect(rect.adjusted(-i, -i + 6, i,
                              i + 6), radius + i, radius + i)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, QColor(26, 28, 32, 228))
        border = QColor(255, 122, 23, 170) if self._state == "confirm" else QColor(
            255, 255, 255, 46)
        p.setPen(QPen(border, 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


# ------------------------------------------------------------------ demo
if __name__ == "__main__":
    app = QApplication(sys.argv)
    ov = VoiceOverlay()
    ov.on_cancel = lambda: print("cancelled")

    def worker():
        s = time.sleep
        ov.set_state("listening")
        for t in ["play", "play lofi", "play lofi on spotify"]:
            ov.set_caption(t)
            s(0.7)
        ov.set_state("thinking")
        s(1.0)
        steps = [("Open Spotify", "run"),
                 ("Search \u201clofi\u201d", "todo"), ("Press play", "todo")]
        ov.set_steps(steps)
        ov.set_state("speaking")
        ov.set_caption("On it. Starting your lofi stream.")
        for i in range(3):
            s(1.0)
            steps[i] = (steps[i][0], "done")
            if i + 1 < 3:
                steps[i + 1] = (steps[i + 1][0], "run")
            ov.set_steps(steps)
        ov.set_steps([])
        ok = ov.confirm("Send this email to Sam at 9:00 AM?",
                        "\u201cRunning late, see you at 10.\u201d", timeout=8)
        print("approved:", ok)
        ov.set_state("speaking")
        ov.set_caption("Done." if ok else "Okay, skipped.")
        ov.done(2000)
        s(3.5)
        QTimer.singleShot(0, app.quit)

    threading.Thread(target=worker, daemon=True).start()
    sys.exit(app.exec())
