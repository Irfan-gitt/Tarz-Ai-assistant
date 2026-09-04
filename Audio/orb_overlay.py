"""
Audio/orb_overlay.py

A small, clean, glowing listening orb — solid soft-blended color, gentle
pulse and drift, no fine line texture. This is deliberately simple:
real assistant orbs (Siri, Gemini, Alexa) are smooth glowing blobs, not
scribbled thread/wave art.

Runs its own PyQt6 QApplication on a dedicated background thread. All
public methods (show/hide/update_level/update_caption) are thread-safe.

CAVEAT: PyQt6 officially expects QApplication to run on the main thread.
Running it on a background thread (as done here) works reliably on
Windows in practice, but isn't universally guaranteed across platforms.

NOTE: no QGraphicsBlurEffect anywhere — it expands a widget's drawing
bounds past its actual pixel buffer, which Windows' layered-window
compositor rejects on a transparent always-on-top window. All glow here
is layered low-alpha shapes instead.
"""

import sys
import math
import queue
import threading

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtGui import QPainter, QRadialGradient, QConicalGradient, QColor, QPen, QFont, QFontMetrics
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF


class _OrbWidget(QWidget):
    def __init__(self, size: int, top_margin: int):
        super().__init__()
        self._size = size
        self._level = 0.0
        self._target_level = 0.0
        self._phase = 0.0

        self._opacity = 0.0
        self._target_opacity = 0.0
        self._pending_hide = False

        self._caption_text = ""
        self._caption_opacity = 0.0
        self._caption_target_opacity = 0.0
        self._caption_idle_frames = 0
        self._caption_hold_frames = 240

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._orb_area = int(size * 2.2)
        self._caption_area_h = 50
        self._total_w = max(self._orb_area, 460)
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

    # ─── Animation ───

    def _tick(self):
        self._level += (self._target_level - self._level) * 0.22
        self._phase += 0.012 + 0.02 * self._level
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

    # ─── Drawing ───

    def paintEvent(self, event):
        if self._opacity <= 0.01 and self._caption_opacity <= 0.01:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = self._orb_cx, self._orb_cy
        op = self._opacity

        if op > 0.01:
            min_r = self._size * 0.34
            max_r = self._size * 0.44
            radius = min_r + (max_r - min_r) * self._level
            rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)

            # Soft outer glow — a few wide, faint rings.
            glow_layers = 5
            for i in range(glow_layers, 0, -1):
                extra = i * (self._size * (0.045 + 0.02 * self._level))
                alpha = int(30 * (1 - (i - 1) / glow_layers)
                            * (0.6 + 0.5 * self._level) * op)
                pen = QPen(QColor(120, 140, 255, alpha))
                pen.setWidthF(self._size * 0.10 + extra)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(rect)

            # Solid body: smooth lit-sphere gradient, light source upper-left.
            light_cx = cx - radius * 0.32
            light_cy = cy - radius * 0.34
            base = QRadialGradient(QPointF(light_cx, light_cy), radius * 1.5)
            for pos, hexcolor in [
                (0.00, "#e8f0ff"), (0.30, "#8fb4ff"),
                (0.62, "#4a68e0"), (1.00, "#1a2568"),
            ]:
                c = QColor(hexcolor)
                c.setAlpha(int(250 * op))
                base.setColorAt(pos, c)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(base)
            painter.drawEllipse(rect)

            # Two large, soft, slowly drifting color blobs for gentle
            # internal motion — blended, not lines or streaks.
            painter.save()
            from PyQt6.QtGui import QPainterPath
            clip = QPainterPath()
            clip.addEllipse(rect)
            painter.setClipPath(clip)

            for hexcolor, speed, phase_off, orbit, blob_r_frac, base_alpha in [
                ("#c9a8ff", 1.0, 0.0, 0.30, 0.85, 90),
                ("#ffffff", 0.6, 2.4, 0.20, 0.55, 70),
                ("#ff9955", 0.45, 4.1, 0.28, 0.70, 85),
            ]:
                ang = self._phase * speed + phase_off
                bx = cx + math.cos(ang) * radius * orbit
                by = cy + math.sin(ang * 0.7) * radius * orbit
                br = radius * blob_r_frac
                grad = QRadialGradient(QPointF(bx, by), br)
                c1 = QColor(hexcolor)
                c1.setAlpha(int((base_alpha + 40 * self._level) * op))
                c2 = QColor(hexcolor)
                c2.setAlpha(0)
                grad.setColorAt(0.0, c1)
                grad.setColorAt(1.0, c2)
                painter.setBrush(grad)
                painter.drawEllipse(QRectF(bx - br, by - br, br * 2, br * 2))

            # Small glossy highlight
            hl_r = radius * 0.22
            hl_cx = cx - radius * 0.35
            hl_cy = cy - radius * 0.40
            hl_grad = QRadialGradient(QPointF(hl_cx, hl_cy), hl_r)
            hl_grad.setColorAt(0.0, QColor(255, 255, 255, int(200 * op)))
            hl_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(hl_grad)
            painter.drawEllipse(
                QRectF(hl_cx - hl_r, hl_cy - hl_r, hl_r * 2, hl_r * 2))

            painter.restore()

        # ── Caption: frosted-glass pill ──
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

            for i in range(4, 0, -1):
                grow = i * 3
                alpha = int(18 * (1 - (i - 1) / 4) * cap_op)
                glow_rect = pill_rect.adjusted(-grow, -grow, grow, grow)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(20, 25, 60, alpha))
                painter.drawRoundedRect(
                    glow_rect, pill_h / 2 + grow, pill_h / 2 + grow)

            painter.setBrush(QColor(25, 28, 48, int(150 * cap_op)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(pill_rect, pill_h / 2, pill_h / 2)

            border_pen = QPen(QColor(255, 255, 255, int(50 * cap_op)))
            border_pen.setWidthF(1.0)
            painter.setPen(border_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(pill_rect, pill_h / 2, pill_h / 2)

            painter.setPen(QColor(255, 255, 255, int(235 * cap_op)))
            painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, elided)

            painter.setOpacity(1.0)


class OrbOverlay:
    def __init__(self, size: int = 60, top_margin: int = 16):
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

    def show(self):
        self._queue.put(("show", None))

    def hide(self):
        self._queue.put(("hide", None))

    def update_level(self, rms: float):
        self._queue.put(("level", rms))

    def update_caption(self, text: str):
        self._queue.put(("caption", text))


_orb: OrbOverlay | None = None


def get_orb() -> OrbOverlay:
    global _orb
    if _orb is None:
        _orb = OrbOverlay()
    return _orb
