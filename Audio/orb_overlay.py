"""
Audio/orb_overlay.py

A floating glowing "listening orb" overlay, matching a Siri/Gemini-style
rotating rainbow ring with a soft blur glow and dark glass center.

Runs its own PyQt6 QApplication on a dedicated background thread. All
public methods (show/hide/update_level) are thread-safe — call them
freely from mic callback threads, asyncio tasks, or anywhere else.

CAVEAT: PyQt6 officially expects QApplication to run on the main thread.
Running it on a background thread (as done here, to avoid restructuring
your existing wake-word/asyncio main loop) works reliably on Windows in
practice, but isn't universally guaranteed by Qt across platforms. If you
ever see Qt-thread-related errors, the fix is inverting the structure —
running QApplication on the real main thread and moving TARZ's own loop
onto a worker thread instead. Flagging this now rather than after you
hit it.
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

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # keeps it off the taskbar / alt-tab list
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - size) // 2
        y = top_margin
        self.setGeometry(x, y, size, size)

        # No QGraphicsBlurEffect here on purpose — it expands the widget's
        # effective drawing bounds beyond its actual pixel buffer, which
        # Windows' layered-window compositor rejects on a transparent
        # always-on-top window (shows up as repeated
        # "UpdateLayeredWindowIndirect failed... The parameter is
        # incorrect" spam). The manual layered rings in paintEvent already
        # produce the soft glow without needing this.
        self.hide()

        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)  # ~60fps

    def _tick(self):
        # Smooth toward the target level instead of snapping — this is
        # what makes the pulse look organic rather than twitchy.
        self._level += (self._target_level - self._level) * 0.25
        self._angle = (self._angle + 2.0) % 360.0
        self.update()

    def set_target_level(self, level: float):
        self._target_level = max(0.0, min(1.0, level))

    def _ring_gradient(self, center: float, alpha: int) -> QConicalGradient:
        gradient = QConicalGradient(center, center, self._angle)
        stops = [
            (0.00, "#3a6df0"),  # blue
            (0.15, "#7b2ff7"),  # indigo
            (0.35, "#e030ff"),  # magenta
            (0.50, "#ff8a65"),  # warm highlight
            (0.65, "#e030ff"),  # magenta
            (0.85, "#7b2ff7"),  # indigo
            (1.00, "#3a6df0"),  # back to blue — seamless loop
        ]
        for pos, hexcolor in stops:
            c = QColor(hexcolor)
            c.setAlpha(alpha)
            gradient.setColorAt(pos, c)
        return gradient

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        s = self._size
        center = s / 2
        min_radius = s * 0.30
        max_radius = s * 0.40
        radius = min_radius + (max_radius - min_radius) * self._level
        rect = QRectF(center - radius, center - radius, radius * 2, radius * 2)

        # Dark glass center fill — matches the reference's dark interior
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(10, 8, 20, 200))
        painter.drawEllipse(rect)

        base_width = s * 0.05 + (s * 0.015 * self._level)

        # Manual glow: several rings, each wider and fainter than the last,
        # layered outward — this is what actually produces the soft color
        # bleed around the ring without smearing the ring itself.
        glow_layers = 5
        for i in range(glow_layers, 0, -1):
            extra_width = i * (s * 0.045)
            alpha = int(70 * (1 - (i - 1) / glow_layers))
            pen = QPen()
            pen.setBrush(self._ring_gradient(center, alpha))
            pen.setWidthF(base_width + extra_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)

        # Crisp bright ring on top — this is the sharp, saturated line the
        # glow layers above are bleeding outward from.
        pen = QPen()
        pen.setBrush(self._ring_gradient(center, 255))
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
                    self.widget.show()
                elif action == "hide":
                    self.widget.hide()
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
