"""
Audio/orb_overlay.py

A floating glowing "listening orb" overlay in the Gemini/ChatGPT voice-mode
style: a soft glowing blob with an organic, liquid silhouette (not a rigid
circle), a colour gradient that cross-fades with the assistant's state, a
slow-drifting iridescent rim, a handful of orbiting motes, and a live
caption line on a frosted-glass pill below it.

Runs its own PyQt6 QApplication on a dedicated background thread. All
public methods (show/hide/update_level/update_caption/set_state) are
thread-safe — call them freely from mic callback threads, asyncio tasks,
or anywhere else.

    orb.show()
    orb.set_state("listening")      # "idle" | "listening" | "thinking" | "speaking"
    orb.update_level(rms)           # 0.0-1.0, mic or TTS-output amplitude
    orb.update_caption("hey tars...")
    orb.hide()

"thinking" and "speaking" animate themselves (a slow auto-swirl / gentle
pulse) even if you never call update_level during them, so the orb doesn't
go dead while you're waiting on Gemini Live or don't have TTS amplitude
wired up. If you do feed update_level during those states it'll layer on
top and look even more reactive.

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
manually via layered low-alpha shapes instead — the same trick as
before, just applied to an arbitrary QPainterPath (the blob outline)
instead of a plain ellipse, since drawPath() with a wide low-alpha pen
works identically to drawEllipse() did.
"""

import sys
import math
import random
import queue
import threading

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtGui import (
    QPainter, QRadialGradient, QConicalGradient, QColor, QPen, QBrush,
    QFont, QFontMetrics, QPainterPath,
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF


# ─────────────────────────── state palettes ───────────────────────────

# Same 4 gradient-stop positions for every state (light -> dark) so we can
# cross-fade between palettes with a plain per-channel lerp, no risk of
# stops landing at mismatched positions mid-transition.
_STOP_POSITIONS = (0.00, 0.27, 0.61, 1.00)

_PALETTES = {
    "idle": {
        "colors": ("#dceeff", "#7fb0ff", "#3d5fe0", "#141a5e"),
        "accent": "#6fa3ff",
    },
    "listening": {
        "colors": ("#eafcff", "#5fd4ff", "#2f8fe0", "#0d2a7a"),
        "accent": "#4fd1ff",
    },
    "thinking": {
        "colors": ("#f1e8ff", "#b98bff", "#6d3fd6", "#241259"),
        "accent": "#a274ff",
    },
    "speaking": {
        "colors": ("#fff0f7", "#ff9ecf", "#8f5fea", "#3a1a6b"),
        "accent": "#ff8fd0",
    },
}


def _hex_to_rgb(hexcolor: str) -> tuple[int, int, int]:
    h = hexcolor.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_color(c1: str, c2: str, t: float) -> tuple[int, int, int]:
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return int(_lerp(r1, r2, t)), int(_lerp(g1, g2, t)), int(_lerp(b1, b2, t))


class _Spring:
    """A lightly underdamped spring toward a target value — gives motion a
    small satisfying overshoot-and-settle instead of the flat, monotonic
    feel of exponential smoothing. Standard semi-implicit-Euler spring-
    damper; assumes a fixed timestep matching the widget's 16ms QTimer."""

    def __init__(self, stiffness: float = 140.0, damping: float = 14.0, value: float = 0.0):
        self.value = value
        self.velocity = 0.0
        self.target = value
        self.stiffness = stiffness
        self.damping = damping

    def update(self, dt: float) -> float:
        force = (self.target - self.value) * \
            self.stiffness - self.velocity * self.damping
        self.velocity += force * dt
        self.value += self.velocity * dt
        return self.value


class _Particle:
    """A tiny orbiting mote — cheap extra texture/life around the orb."""

    __slots__ = ("angle", "radius_factor", "speed",
                 "twinkle_phase", "twinkle_speed", "size")

    def __init__(self):
        self.angle = random.uniform(0, 2 * math.pi)
        self.radius_factor = random.uniform(1.18, 1.60)
        self.speed = random.uniform(0.12, 0.30) * random.choice((-1, 1))
        self.twinkle_phase = random.uniform(0, 2 * math.pi)
        self.twinkle_speed = random.uniform(0.03, 0.08)
        self.size = random.uniform(1.3, 2.8)


class _OrbWidget(QWidget):
    def __init__(self, size: int, top_margin: int):
        super().__init__()
        self._size = size
        self._target_level = 0.0
        self._level_spring = _Spring(stiffness=140.0, damping=14.0)
        self._level = 0.0

        self._phase = 0.0        # drives the organic wobble
        self._swirl_angle = 0.0  # extra rotation, active mainly in "thinking"
        self._rim_angle = 0.0    # drives the rotating iridescent fringe
        self._breath = 0.0       # slow idle "breathing" oscillator

        self._opacity = 0.0
        self._target_opacity = 0.0
        self._pending_hide = False

        self._state = "idle"
        self._palette_from = _PALETTES["idle"]
        self._palette_to = _PALETTES["idle"]
        self._palette_t = 1.0
        self._palette_blend_speed = 0.06

        self._particles = [_Particle() for _ in range(8)]

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

    # ─── State ───

    def set_state(self, state: str):
        if state not in _PALETTES:
            state = "idle"
        if state == self._state:
            return
        # Snapshot the *currently interpolated* palette as the new "from",
        # so a state change mid-transition never pops/jumps.
        self._palette_from = self._current_palette()
        self._palette_to = _PALETTES[state]
        self._palette_t = 0.0
        self._state = state

    def _current_palette(self) -> dict:
        t = self._palette_t
        colors = tuple(
            "#%02x%02x%02x" % _lerp_color(a, b, t)
            for a, b in zip(self._palette_from["colors"], self._palette_to["colors"])
        )
        accent = "#%02x%02x%02x" % _lerp_color(
            self._palette_from["accent"], self._palette_to["accent"], t)
        return {"colors": colors, "accent": accent}

    # ─── Animation state ───

    def _tick(self):
        dt = 1 / 60

        self._level_spring.target = self._target_level
        self._level = self._level_spring.update(dt)

        idle_speed = 0.012
        voice_speed = 0.05 * self._level
        self._phase += idle_speed + voice_speed

        # "thinking" gets a slow auto-swirl so it stays visibly alive with
        # no mic/TTS amplitude driving it at all.
        swirl_speed = 0.018 if self._state == "thinking" else 0.0
        self._swirl_angle += swirl_speed

        self._rim_angle = (self._rim_angle + 0.5 + 2.0 * self._level) % 360.0
        self._breath = (self._breath + 0.010) % (2 * math.pi)

        if self._palette_t < 1.0:
            self._palette_t = min(1.0, self._palette_t +
                                  self._palette_blend_speed)

        self._opacity += (self._target_opacity - self._opacity) * 0.18

        for p in self._particles:
            p.angle += p.speed * dt
            p.twinkle_phase += p.twinkle_speed

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

    def _blob_wobble(self, theta: float) -> float:
        """Sum of a few independently-drifting sine harmonics — a cheap
        stand-in for Perlin/simplex noise so the silhouette wobbles
        organically without pulling in a noise library. Bounded to
        [-1, 1] regardless of phase alignment (each term's coefficient
        sums to the normalising divisor)."""
        swirl = self._swirl_angle
        n = 0.0
        n += math.sin(3 * theta + swirl + self._phase * 1.0) * 1.00
        n += math.sin(5 * theta - swirl * 0.6 - self._phase * 1.7 + 1.3) * 0.55
        n += math.sin(7 * theta + swirl * 0.3 + self._phase * 0.6 + 2.7) * 0.35
        n += math.sin(2 * theta - self._phase * 2.3 + 0.4) * 0.25
        return n / 2.15

    def _blob_points(self, cx: float, cy: float, base_r: float, wobble_amp: float, n: int = 48) -> list[QPointF]:
        pts = []
        for i in range(n):
            theta = (i / n) * 2 * math.pi
            r = base_r + wobble_amp * self._blob_wobble(theta)
            pts.append(QPointF(cx + r * math.cos(theta),
                       cy + r * math.sin(theta)))
        return pts

    @staticmethod
    def _smooth_closed_path(points: list[QPointF]) -> QPainterPath:
        """Catmull-Rom -> cubic Bezier through a closed loop of points.
        Gives a fluid, liquid curve instead of the faceted look a plain
        polyline gets at a comparable point count."""
        n = len(points)
        path = QPainterPath()
        path.moveTo(points[0])
        for i in range(n):
            p0 = points[(i - 1) % n]
            p1 = points[i]
            p2 = points[(i + 1) % n]
            p3 = points[(i + 2) % n]
            cp1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6.0,
                          p1.y() + (p2.y() - p0.y()) / 6.0)
            cp2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6.0,
                          p2.y() - (p3.y() - p1.y()) / 6.0)
            path.cubicTo(cp1, cp2, p2)
        path.closeSubpath()
        return path

    def paintEvent(self, event):
        if self._opacity <= 0.01 and self._caption_opacity <= 0.01:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = self._orb_cx, self._orb_cy
        op = self._opacity
        palette = self._current_palette()

        def accent(alpha: int) -> QColor:
            c = QColor(palette["accent"])
            c.setAlpha(alpha)
            return c

        if op > 0.01:
            min_r = self._size * 0.36
            max_r = self._size * 0.54
            # idle life even at level=0
            breathe = 0.02 * math.sin(self._breath)
            radius = min_r + (max_r - min_r) * \
                self._level + self._size * breathe

            wobble_amp = radius * (0.045 + 0.16 * self._level)
            if self._state == "thinking":
                wobble_amp = max(wobble_amp, radius * 0.08)

            blob_pts = self._blob_points(cx, cy, radius, wobble_amp)
            blob_path = self._smooth_closed_path(blob_pts)

            # ── Big, soft, diffuse outer glow — several wide low-alpha
            # strokes of the blob outline, spread well past the edge.
            glow_layers = 7
            for i in range(glow_layers, 0, -1):
                extra = i * (self._size * (0.09 + 0.05 * self._level))
                alpha = int(26 * (1 - (i - 1) / glow_layers)
                            * (0.6 + 0.5 * self._level) * op)
                pen = QPen()
                pen.setBrush(accent(alpha))
                pen.setWidthF(self._size * 0.12 + extra)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(blob_path)

            # ── Sphere body, clipped to the organic silhouette ──
            painter.save()
            painter.setClipPath(blob_path)

            light_cx = cx - radius * 0.30
            light_cy = cy - radius * 0.32
            base_grad = QRadialGradient(
                QPointF(light_cx, light_cy), radius * 1.55)
            for pos, hexcolor in zip(_STOP_POSITIONS, palette["colors"]):
                c = QColor(hexcolor)
                c.setAlpha(int(240 * op))
                base_grad.setColorAt(pos, c)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(base_grad)
            painter.drawPath(blob_path)

            # ── Fine wavy interference ripples layered on top ──
            ripple_cx = cx - radius * 0.12
            ripple_cy = cy - radius * 0.10
            ring_count = 5
            for i in range(ring_count):
                t = i / (ring_count - 1)
                base_r = radius * (0.22 + 0.72 * t)
                amp = radius * (0.03 + 0.045 * self._level) * (1.0 - t * 0.3)
                nseg = 72
                path = QPainterPath()
                for k in range(nseg + 1):
                    theta = (k / nseg) * 2 * math.pi
                    r = base_r + amp * math.sin((3 + i % 3) * theta + self._phase * (1.0 + 0.15 * i) + i) \
                        + amp * 0.35 * \
                        math.sin((5 + i % 2) * theta -
                                 self._phase * 1.4 * (1.0 + 0.1 * i))
                    x = ripple_cx + r * math.cos(theta)
                    y = ripple_cy + r * math.sin(theta)
                    if k == 0:
                        path.moveTo(x, y)
                    else:
                        path.lineTo(x, y)
                alpha = int((24 + 10 * self._level) * (1.0 - t * 0.4) * op)
                pen = QPen(QColor(255, 255, 255, alpha))
                pen.setWidthF(1.3)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)

            # ── Highlight — bright small core + soft surrounding halo ──
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

            # ── Iridescent rim fringe — now actually rotating. In the
            # previous version _rim_angle was ticked every frame but never
            # fed into the gradient, so the fringe was static; it now
            # drifts slowly around the rim, faster while reactive.
            fringe_grad = QConicalGradient(cx, cy, self._rim_angle)
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
            painter.drawPath(blob_path)

            # ── Orbiting motes ──
            particle_boost = 0.4 + 0.6 * self._level
            for p in self._particles:
                pr = radius * p.radius_factor
                px = cx + pr * math.cos(p.angle)
                py = cy + pr * math.sin(p.angle) * 0.85
                twinkle = 0.5 + 0.5 * math.sin(p.twinkle_phase)
                alpha = int(120 * twinkle * particle_boost * op)
                if alpha <= 2:
                    continue
                psize = p.size * 2.4
                grad = QRadialGradient(QPointF(px, py), psize)
                c1 = accent(alpha)
                c2 = accent(0)
                grad.setColorAt(0.0, c1)
                grad.setColorAt(1.0, c2)
                painter.setBrush(grad)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(px, py), psize, psize)

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

            for i in range(4, 0, -1):
                grow = i * 3
                alpha = int(18 * (1 - (i - 1) / 4) * cap_op)
                glow_rect = pill_rect.adjusted(-grow, -grow, grow, grow)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(20, 25, 60, alpha))
                painter.drawRoundedRect(
                    glow_rect, pill_h / 2 + grow, pill_h / 2 + grow)

            glass = QColor(25, 28, 48, int(150 * cap_op))
            painter.setBrush(glass)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(pill_rect, pill_h / 2, pill_h / 2)

            # Border tint now follows the current state's accent colour so
            # the pill reads as part of the same orb, not a bolted-on box.
            border_pen = QPen(accent(int(90 * cap_op)))
            border_pen.setWidthF(1.1)
            painter.setPen(border_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(pill_rect, pill_h / 2, pill_h / 2)

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
                elif action == "state":
                    self.widget.set_state(payload)
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

    def set_state(self, state: str):
        """Switch the orb's visual state: "idle" | "listening" | "thinking"
        | "speaking". Colours cross-fade over ~250ms and the silhouette's
        behaviour changes with it (auto-swirl in "thinking" vs. reactive
        wobble in "listening"/"speaking"). Suggested wiring into TARZ:
          - "listening" the moment openWakeWord fires / mic capture starts
          - "thinking"  once the utterance ends and Gemini Live is working
          - "speaking"  while Orpheus/Cartesia TTS is playing
          - "idle"      once playback finishes, back to waiting for the wake word
        """
        self._queue.put(("state", state))


_orb: OrbOverlay | None = None


def get_orb() -> OrbOverlay:
    """Lazy singleton — the orb window is created on first use, reused after."""
    global _orb
    if _orb is None:
        _orb = OrbOverlay()
    return _orb


if __name__ == "__main__":
    # Standalone preview: run `python orb_overlay.py` to see it cycle
    # through every state before wiring it into the real pipeline.
    import time

    orb = get_orb()
    orb.show()

    cycle = ["idle", "listening", "thinking", "speaking"]
    captions = {
        "listening": "hey tars, what's the weather like tomorrow?",
        "speaking": "it's looking clear and sunny, around 24 degrees.",
    }

    print("Demo running - cycling idle -> listening -> thinking -> speaking. Ctrl+C to quit.")
    t0 = time.time()
    idx = 0
    state_t0 = t0
    try:
        while True:
            now = time.time()
            state = cycle[idx]
            orb.set_state(state)

            if state in ("listening", "speaking"):
                level = 0.5 + 0.5 * math.sin((now - t0) * 4.0)
                orb.update_level(level)
                orb.update_caption(captions.get(state, ""))
            else:
                orb.update_level(0.0)
                orb.update_caption("")

            if now - state_t0 > 3.0:
                idx = (idx + 1) % len(cycle)
                state_t0 = now

            time.sleep(0.05)
    except KeyboardInterrupt:
        orb.hide()
        time.sleep(0.3)
