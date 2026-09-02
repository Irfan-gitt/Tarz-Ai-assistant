"""
Audio/orb_overlay.py

A floating glowing "listening orb" overlay, matching a Siri/Gemini-style
rotating rainbow ring — now with outward-radiating ripple pulses while
you're speaking, a counter-rotating secondary ring for depth, and a
smooth fade in/out instead of an instant pop.

Runs its own PyQt6 QApplication on a dedicated background thread. All
public methods (show/hide/update_level) are thread-safe — call them
freely from mic callback threads, asyncio tasks, or anywhere else.

CAVEAT: PyQt6 officially expects QApplication to run on the main thread.
Running it on a background thread (as done here) works reliably on
Windows in practice, but isn't universally guaranteed by Qt across
platforms. If you ever see Qt-thread-related errors, the fix is
inverting the structure — QApplication on the real main thread, TARZ's
own loop on a worker thread instead.

NOTE: deliberately no QGraphicsBlurEffect anywhere in this file — it
expands a widget's drawing bounds past its actual pixel buffer, which
Windows' layered-window compositor rejects on a transparent always-on-
top window (shows up as "UpdateLayeredWindowIndirect failed... The
parameter is incorrect" spam). All glow/fade here is done manually by
varying alpha in paintEvent instead.
"""

import sys
import queue
import threading

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtGui import QPainter, QConicalGradient, QColor, QPen
from PyQt6.QtCore import Qt, QTimer, QRectF


class _OrbWidget(QWidget):
    def __init__(self, size: int, top_margin: int):
        super().__init__()
        self._size = size
        self._level = 0.0
        self._target_level = 0.0
        self._angle = 0.0
        self._angle2 = 0.0

        self._opacity = 0.0        # current fade level, 0..1
        self._target_opacity = 0.0
        self._pending_hide = False  # actually hide the OS window once fade-out completes

        self._ripples: list[float] = []  # each entry is an age 0..1
        self._ripple_cooldown = 0.0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # keeps it off the taskbar / alt-tab list
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # Extra canvas room around the core orb so ripples have space to
        # expand into without getting clipped at the widget edge.
        self._canvas = int(size * 2.2)
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self._canvas) // 2
        y = top_margin
        self.setGeometry(x, y, self._canvas, self._canvas)

        self.hide()

        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)  # ~60fps

    # ─── Animation state ───

    def _tick(self):
        self._level += (self._target_level - self._level) * 0.25

        self._angle = (self._angle + 2.0) % 360.0
        self._angle2 = (self._angle2 - 3.2) % 360.0

        self._opacity += (self._target_opacity - self._opacity) * 0.18
        if self._pending_hide and self._opacity < 0.02:
            self._opacity = 0.0
            super().hide()
            self._pending_hide = False

        if self._level > 0.18:
            self._ripple_cooldown -= 1
            if self._ripple_cooldown <= 0:
                self._ripples.append(0.0)
                self._ripple_cooldown = 14

        self._ripples = [min(1.0, age + 0.018) for age in self._ripples]
        self._ripples = [age for age in self._ripples if age < 1.0]

        self.update()

    def set_target_level(self, level: float):
        self._target_level = max(0.0, min(1.0, level))

    def animate_show(self):
        self._pending_hide = False
        self._target_opacity = 1.0
        if not self.isVisible():
            super().show()

    def animate_hide(self):
        self._target_opacity = 0.0
        self._pending_hide = True

    # ─── Drawing ───

    def _ring_gradient(self, center: float, angle: float, alpha: int) -> QConicalGradient:
        gradient = QConicalGradient(center, center, angle)
        stops = [
            (0.00, "#3a6df0"),
            (0.15, "#7b2ff7"),
            (0.35, "#e030ff"),
            (0.50, "#ff8a65"),
            (0.65, "#e030ff"),
            (0.85, "#7b2ff7"),
            (1.00, "#3a6df0"),
        ]
        for pos, hexcolor in stops:
            c = QColor(hexcolor)
            c.setAlpha(alpha)
            gradient.setColorAt(pos, c)
        return gradient

    def paintEvent(self, event):
        if self._opacity <= 0.01:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        canvas = self._canvas
        center = canvas / 2
        orb_size = self._size

        min_radius = orb_size * 0.30
        max_radius = orb_size * 0.40
        radius = min_radius + (max_radius - min_radius) * self._level
        rect = QRectF(center - radius, center - radius, radius * 2, radius * 2)

        op = self._opacity

        painter.setPen(Qt.PenStyle.NoPen)
        center_color = QColor(10, 8, 20)
        center_color.setAlpha(int(200 * op))
        painter.setBrush(center_color)
        painter.drawEllipse(rect)

        base_width = orb_size * 0.05 + (orb_size * 0.02 * self._level)

        glow_layers = 5
        for i in range(glow_layers, 0, -1):
            extra_width = i * (orb_size * 0.045)
            alpha = int(70 * (1 - (i - 1) / glow_layers) * op)
            pen = QPen()
            pen.setBrush(self._ring_gradient(center, self._angle, alpha))
            pen.setWidthF(base_width + extra_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)

        for age in self._ripples:
            ripple_radius = radius + age * (orb_size * 0.55)
            ripple_alpha = int(150 * (1 - age) * op)
            if ripple_alpha <= 1:
                continue
            ripple_rect = QRectF(
                center - ripple_radius, center - ripple_radius,
                ripple_radius * 2, ripple_radius * 2,
            )
            pen = QPen()
            pen.setBrush(self._ring_gradient(
                center, self._angle, ripple_alpha))
            pen.setWidthF(max(1.5, base_width * (1 - age * 0.6)))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(ripple_rect)

        secondary_radius = radius * 0.72
        secondary_rect = QRectF(
            center - secondary_radius, center - secondary_radius,
            secondary_radius * 2, secondary_radius * 2,
        )
        pen = QPen()
        pen.setBrush(self._ring_gradient(center, self._angle2, int(140 * op)))
        pen.setWidthF(base_width * 0.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(secondary_rect)

        pen = QPen()
        pen.setBrush(self._ring_gradient(center, self._angle, int(255 * op)))
        pen.setWidthF(base_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)


class OrbOverlay:
    def __init__(self, size: int = 64, top_margin: int = 16):
        self._size = size
        self._top_margin = top_margin
        self._queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._ready = threading.Event()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self):
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.widget = _OrbWidget(self._size, self._top_margin)
        self._ready.set()

        poll_timer = QTimer()
        poll_timer.timeout.connect(self._poll_queue)
        poll_timer.start(20)

        self.app.exec()

    def _poll_queue(self):
        try:
            while True:
                action, payload = self._queue.get_nowait()
                if action == "show":
                    self.widget.animate_show()
                elif action == "hide":
                    self.widget.animate_hide()
                elif action == "level":
                    self.widget.set_target_level(payload)
        except queue.Empty:
            pass

    # ─── Public API — safe to call from ANY thread ───

    def show(self):
        self._queue.put(("show", None))

    def hide(self):
        self._queue.put(("hide", None))

    def update_level(self, rms: float):
        self._queue.put(("level", rms))


_orb: OrbOverlay | None = None


def get_orb() -> OrbOverlay:
    """Lazy singleton — the orb window is created on first use, reused after."""
    global _orb
    if _orb is None:
        _orb = OrbOverlay()
    return _orb
