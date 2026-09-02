"""
Audio/orb_overlay.py

A floating glowing "listening orb" overlay — a lit glass sphere with fine
wavy interference-ripple texture (not colored streaks), a soft diffuse
glow, a subtle iridescent edge fringe, and a live caption line below it
on a frosted-glass pill background.

Runs its own PyQt6 QApplication on a dedicated background thread. All
public methods (show/hide/update_level/update_caption) are thread-safe —
call them freely from mic callback threads, asyncio tasks, or anywhere
else.

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
parameter is incorrect" spam). All glow/fade/blur-look here is done
manually via layered low-alpha shapes instead.
"""

import sys
import math
import queue
import threading

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtGui import (
    QPainter, QRadialGradient, QConicalGradient, QColor, QPen, QBrush,
    QFont, QFontMetrics, QPainterPath,
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF


class _OrbWidget(QWidget):
    def __init__(self, size: int, top_margin: int):
        super().__init__()
        self._size = size
        self._level = 0.0
        self._target_level = 0.0

        self._phase = 0.0        # drives both idle drift and voice-reactive motion
        self._rim_angle = 0.0

        self._opacity = 0.0
        self._target_opacity = 0.0
        self._pending_hide = False

        self._caption_text = ""
        self._caption_opacity = 0.0
        self._caption_target_opacity = 0.0
        self._caption_idle_frames = 0
        self._caption_hold_frames = 240  # ~4s at 60fps before it starts fading

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # extra room for the bigger, softer glow
        self._orb_area = int(size * 2.8)
        self._caption_area_h = 54
        self._total_w = max(self._orb_area, 480)
        self._total_h = self._orb_area + self._caption_area_h

        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self._total_w) // 2
        y = top_margin
        self.setGeometry(x, y, self._total_w, self._total_h)

        self._orb_cx = self._total_w / 2
        self._orb_cy = self._orb_area / 2

        self.hide()

        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    # ─── Animation state ───

    def _tick(self):
        self._level += (self._target_level - self._level) * 0.25

        # Always drifting, even at silence — this is what keeps it feeling
        # alive/"moving" instead of a static image between utterances.
        idle_speed = 0.01
        voice_speed = 0.03 * self._level
        self._phase += idle_speed + voice_speed
        self._rim_angle = (self._rim_angle + 0.6) % 360.0

        self._opacity += (self._target_opacity - self._opacity) * 0.18

        if self._caption_text:
            self._caption_idle_frames += 1
            self._caption_target_opacity = 0.0 if self._caption_idle_frames > self._caption_hold_frames else 1.0
        else:
            self._caption_target_opacity = 0.0
        self._caption_opacity += (self._caption_target_opacity -
                                  self._caption_opacity) * 0.08

        if self._pending_hide and self._opacity < 0.02 and self._caption_opacity < 0.02:
            self._opacity = 0.0
            self._caption_opacity = 0.0
            self._caption_text = ""
            super().hide()
            self._pending_hide = False

        self.update()

    def set_target_level(self, level: float):
        self._target_level = max(0.0, min(1.0, level))

    def set_caption(self, text: str):
        self._caption_text = text.strip()
        self._caption_idle_frames = 0
        if self._caption_text:
            self._caption_target_opacity = 1.0

    def animate_show(self):
        self._pending_hide = False
        self._target_opacity = 1.0
        if not self.isVisible():
            super().show()

    def animate_hide(self):
        self._target_opacity = 0.0
        self._pending_hide = True

    # ─── Drawing helpers ───

    def _glow_color(self, alpha: int) -> QColor:
        c = QColor("#6fa3ff")
        c.setAlpha(alpha)
        return c

    def _wavy_ring_path(self, cx, cy, base_r, amp, freq1, freq2, phase, n=100) -> QPainterPath:
        """A closed ring whose radius wobbles sinusoidally — this is the
        fine interference/ripple texture, not a colored blob or streak."""
        path = QPainterPath()
        for i in range(n + 1):
            theta = (i / n) * 2 * math.pi
            r = base_r + amp * math.sin(freq1 * theta + phase) + \
                amp * 0.35 * math.sin(freq2 * theta - phase * 1.4)
            x = cx + r * math.cos(theta)
            y = cy + r * math.sin(theta)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        return path

    def paintEvent(self, event):
        if self._opacity <= 0.01 and self._caption_opacity <= 0.01:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = self._orb_cx, self._orb_cy
        op = self._opacity

        if op > 0.01:
            min_r = self._size * 0.36
            max_r = self._size * 0.54
            radius = min_r + (max_r - min_r) * self._level
            rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)

            # ── Big, soft, diffuse outer glow — several wide low-alpha
            # rings, spread much further than a tight halo would go.
            glow_layers = 7
            for i in range(glow_layers, 0, -1):
                extra = i * (self._size * (0.09 + 0.05 * self._level))
                alpha = int(26 * (1 - (i - 1) / glow_layers)
                            * (0.6 + 0.5 * self._level) * op)
                pen = QPen()
                pen.setBrush(self._glow_color(alpha))
                pen.setWidthF(self._size * 0.12 + extra)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(rect)

            # ── Sphere body ──
            sphere_path = QPainterPath()
            sphere_path.addEllipse(rect)
            painter.save()
            painter.setClipPath(sphere_path)

            light_cx = cx - radius * 0.30
            light_cy = cy - radius * 0.32
            base_grad = QRadialGradient(
                QPointF(light_cx, light_cy), radius * 1.55)
            for pos, hexcolor in [
                (0.00, "#dceeff"), (0.28, "#7fb0ff"),
                (0.60, "#3d5fe0"), (1.00, "#12185c"),
            ]:
                c = QColor(hexcolor)
                c.setAlpha(int(240 * op))
                base_grad.setColorAt(pos, c)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(base_grad)
            painter.drawEllipse(rect)

            # ── Fine wavy ripple/interference lines — the texture that
            # replaces the old colored streaks.
            ripple_cx = cx - radius * 0.12
            ripple_cy = cy - radius * 0.10
            ring_count = 9
            for i in range(ring_count):
                t = i / (ring_count - 1)
                base_r = radius * (0.18 + 0.80 * t)
                amp = radius * (0.035 + 0.05 * self._level) * (1.0 - t * 0.3)
                path = self._wavy_ring_path(
                    ripple_cx, ripple_cy, base_r, amp,
                    freq1=3 + (i % 3), freq2=5 + (i % 2),
                    phase=self._phase * (1.0 + 0.15 * i) + i,
                )
                alpha = int((26 + 10 * self._level) * (1.0 - t * 0.4) * op)
                pen = QPen()
                pen.setColor(QColor(255, 255, 255, alpha))
                pen.setWidthF(1.4)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)

            # ── Subtle iridescent edge fringe (small sliver, not the
            # whole rim) — the little rainbow glint glass spheres get.
            fringe_grad = QConicalGradient(cx, cy, 200)
            fringe_stops = [
                (0.00, QColor(255, 255, 255, 0)),
                (0.04, QColor(120, 170, 255, int(90 * op))),
                (0.07, QColor(190, 140, 255, int(100 * op))),
                (0.10, QColor(255, 160, 200, int(80 * op))),
                (0.13, QColor(255, 255, 255, 0)),
                (1.00, QColor(255, 255, 255, 0)),
            ]
            for pos, color in fringe_stops:
                fringe_grad.setColorAt(pos, color)
            pen = QPen()
            pen.setBrush(fringe_grad)
            pen.setWidthF(self._size * 0.05)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)

            # ── Highlight — bright small core + soft surrounding halo
            hl_cx = cx - radius * 0.32
            hl_cy = cy - radius * 0.38

            halo_r = radius * 0.48
            halo_grad = QRadialGradient(QPointF(hl_cx, hl_cy), halo_r)
            halo_grad.setColorAt(0.0, QColor(255, 255, 255, int(90 * op)))
            halo_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(halo_grad)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(
                QRectF(hl_cx - halo_r, hl_cy - halo_r, halo_r * 2, halo_r * 2))

            core_r = radius * 0.14
            core_grad = QRadialGradient(QPointF(hl_cx, hl_cy), core_r)
            core_grad.setColorAt(0.0, QColor(255, 255, 255, int(220 * op)))
            core_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(core_grad)
            painter.drawEllipse(
                QRectF(hl_cx - core_r, hl_cy - core_r, core_r * 2, core_r * 2))

            painter.restore()  # drop clip

        # ── Caption: frosted-glass pill behind the text so it's readable
        # against any desktop background, not floating text on nothing.
        if self._caption_opacity > 0.01 and self._caption_text:
            cap_op = self._caption_opacity
            painter.setOpacity(cap_op)

            font = QFont("Segoe UI Semibold", 12)
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
            painter.setFont(font)
            metrics = QFontMetrics(font)

            max_text_w = self._total_w - 80
            elided = metrics.elidedText(
                self._caption_text, Qt.TextElideMode.ElideRight, max_text_w)
            text_w = metrics.horizontalAdvance(elided)

            pill_w = text_w + 44
            pill_h = 34
            pill_x = cx - pill_w / 2
            pill_y = self._orb_area + (self._caption_area_h - pill_h) / 2
            pill_rect = QRectF(pill_x, pill_y, pill_w, pill_h)

            # Soft outer glow behind the pill (fake blur via layered
            # low-alpha rounded rects, same trick used on the sphere).
            for i in range(4, 0, -1):
                grow = i * 3
                alpha = int(18 * (1 - (i - 1) / 4) * cap_op)
                glow_rect = pill_rect.adjusted(-grow, -grow, grow, grow)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(20, 25, 60, alpha))
                painter.drawRoundedRect(
                    glow_rect, pill_h / 2 + grow, pill_h / 2 + grow)

            # Frosted glass pill body
            glass = QColor(25, 28, 48, int(150 * cap_op))
            painter.setBrush(glass)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(pill_rect, pill_h / 2, pill_h / 2)

            # Thin light border for the "glass edge" look
            border_pen = QPen(QColor(255, 255, 255, int(50 * cap_op)))
            border_pen.setWidthF(1.0)
            painter.setPen(border_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(pill_rect, pill_h / 2, pill_h / 2)

            # Text
            painter.setPen(QColor(255, 255, 255, int(235 * cap_op)))
            painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, elided)

            painter.setOpacity(1.0)


class OrbOverlay:
    def __init__(self, size: int = 80, top_margin: int = 16):
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
                elif action == "caption":
                    self.widget.set_caption(payload)
        except queue.Empty:
            pass

    # ─── Public API — safe to call from ANY thread ───

    def show(self):
        self._queue.put(("show", None))

    def hide(self):
        self._queue.put(("hide", None))

    def update_level(self, rms: float):
        self._queue.put(("level", rms))

    def update_caption(self, text: str):
        """Show a line of transcribed text below the orb on a frosted-glass
        pill. Call with each interim/partial transcript as it comes in,
        and again with the final one. Fades out on its own a few seconds
        after the last update — or pass "" to clear it immediately."""
        self._queue.put(("caption", text))


_orb: OrbOverlay | None = None


def get_orb() -> OrbOverlay:
    """Lazy singleton — the orb window is created on first use, reused after."""
    global _orb
    if _orb is None:
        _orb = OrbOverlay()
    return _orb
