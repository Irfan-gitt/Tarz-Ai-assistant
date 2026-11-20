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
import ast
import re
import queue
import threading

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QFrame,
)
from PyQt6.QtGui import QPainter, QPainterPath, QRadialGradient, QConicalGradient, QColor, QPen, QFont, QFontMetrics, QCursor
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF

try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows fallback
    winsound = None


class _PopupOrb(QWidget):
    """Small animated orb used by the system-wide approval card."""

    def __init__(self, color: str):
        super().__init__()
        self._color = QColor(color)
        self._phase = 0.0
        self.setFixedSize(48, 48)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(32)

    def _tick(self):
        self._phase += 0.08
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = 15 * (1 + 0.04 * math.sin(self._phase))
        glow = QRadialGradient(center, 23)
        halo = QColor(self._color)
        halo.setAlpha(60)
        glow.setColorAt(0, halo)
        glow.setColorAt(1, QColor(halo.red(), halo.green(), halo.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(1, 1, 46, 46))
        sphere = QRadialGradient(QPointF(center.x() - radius * .3,
                                         center.y() - radius * .35), radius * 1.5)
        sphere.setColorAt(0, QColor("#ffffff"))
        sphere.setColorAt(.35, self._color.lighter(140))
        sphere.setColorAt(1, self._color.darker(150))
        painter.setBrush(sphere)
        painter.drawEllipse(QRectF(center.x() - radius, center.y() - radius,
                                   radius * 2, radius * 2))


class _ApprovalPopup(QWidget):
    """Small system-wide, always-on-top prompt used for TARZ approvals."""

    def __init__(self, title: str, message: str, kind: str, on_result):
        super().__init__()
        self.setObjectName("approvalPopup")
        self._kind = kind
        self._on_result = on_result
        self._finished = False
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setFixedWidth(440)
        self.setStyleSheet("""
            QWidget#approvalPopup { background:#191919; color:#ffffff; font:14px 'Segoe UI'; border:1px solid #343840; border-radius:18px; }
            QWidget { color:#f5f6f8; font:14px 'Segoe UI'; }
            QLabel#eyebrow { color:#9a9ea5; font:10px 'Consolas'; letter-spacing:1px; }
            QLabel#title { font-size:16px; font-weight:600; }
            QLabel#message { color:#d4d5d8; }
            QFrame#details { background:#1a1c20; border:1px solid #212327; border-radius:11px; }
            QLabel#key { color:#949aa4; font-size:12px; }
            QLabel#value { color:#f1f2f4; font-size:13px; }
            QPushButton { border:1px solid #41454d; border-radius:16px; padding:7px 17px; background:transparent; color:#f3f4f6; font-weight:600; }
            QPushButton#primary { background:#ffffff; color:#0a0a0a; border-color:#ffffff; }
            QPushButton#primary:hover { background:#e6e6e6; border-color:#e6e6e6; }
            QPushButton#deny:hover { background:#30333a; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(7)

        eyebrow = QLabel("TARZ  /  ACTION APPROVAL")
        eyebrow.setObjectName("eyebrow")
        heading = QLabel(title)
        heading.setObjectName("title")
        heading_block = QVBoxLayout()
        heading_block.setSpacing(3)
        heading_block.addWidget(eyebrow)
        heading_block.addWidget(heading)
        header = QHBoxLayout()
        header.setSpacing(11)
        header.addWidget(_PopupOrb("#ffb347" if kind == "confirm" else "#ff7a17"))
        header.addLayout(heading_block, 1)
        layout.addLayout(header)

        action_name, args = self._parse_action(message)
        if action_name and args is not None:
            heading.setText("Review this action")
            action_title = QLabel(self._humanize_action(action_name))
            action_title.setObjectName("message")
            layout.addWidget(action_title)
            details = QFrame()
            details.setObjectName("details")
            details_layout = QVBoxLayout(details)
            details_layout.setContentsMargins(10, 8, 10, 8)
            details_layout.setSpacing(5)
            for key, value in args.items():
                field = QHBoxLayout()
                field.setSpacing(8)
                key_label = QLabel(self._humanize_field(key))
                key_label.setObjectName("key")
                key_label.setFixedWidth(62)
                value_label = QLabel(str(value))
                value_label.setObjectName("value")
                value_label.setWordWrap(True)
                value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                field.addWidget(key_label)
                field.addWidget(value_label, 1)
                details_layout.addLayout(field)
            layout.addWidget(details)
            body_text = "Allow TARZ to continue with this action?"
        else:
            body_text = message
        body = QLabel(body_text)
        body.setObjectName("message")
        body.setWordWrap(True)
        body.setMaximumHeight(90)
        layout.addWidget(body)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Deny")
        cancel.setObjectName("deny")
        primary = QPushButton("Allow")
        primary.setObjectName("primary")
        cancel.clicked.connect(self._cancel)
        primary.clicked.connect(self._approve)
        buttons.addWidget(cancel)
        buttons.addWidget(primary)
        layout.addLayout(buttons)

        self.adjustSize()
        screen_handle = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        screen = screen_handle.availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.top() + 24)

    @staticmethod
    def _parse_action(message: str):
        question = message.strip()
        if question.endswith("?"):
            question = question[:-1].rstrip()
        match = re.fullmatch(r"Run\s+([\w.-]+)\s+with\s+(.+)", question, re.DOTALL)
        if not match:
            return None, None
        try:
            args = ast.literal_eval(match.group(2))
        except (SyntaxError, ValueError):
            return None, None
        return match.group(1), args if isinstance(args, dict) else None

    @staticmethod
    def _humanize_action(name: str) -> str:
        titles = {
            "whatsapp_send_message": "Send a WhatsApp message",
            "whatsapp_call": "Start a WhatsApp call",
            "send_email": "Send an email",
            "delete_event": "Delete a calendar event",
            "create_event": "Create a calendar event",
            "discord_send_message": "Send a Discord message",
            "telegram_message_user": "Send a Telegram message",
        }
        return titles.get(name, name.replace("_", " ").capitalize())

    @staticmethod
    def _humanize_field(name: str) -> str:
        return {"contact": "Contact", "message": "Message", "user": "Recipient",
                "email": "Email", "subject": "Subject", "body": "Message"}.get(
                    name.lower(), name.replace("_", " ").capitalize())

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        if winsound:
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except OSError:
                QApplication.beep()
        else:
            QApplication.beep()

    def _finish(self, result):
        if self._finished:
            return
        self._finished = True
        self._on_result(result)
        self.close()

    def _approve(self):
        self._finish(True)

    def _cancel(self):
        self._finish(False)

    def closeEvent(self, event):
        if not self._finished:
            self._finished = True
            self._on_result(False)
        super().closeEvent(event)


ORB_STATES = {
    "idle": ("#9a9ea5", "#3a3d42"),
    "listening": ("#ff7a17", "#b8420a"),
    "thinking": ("#7c3aed", "#2e1065"),
    "speaking": ("#f5f5f5", "#ff7a17"),
    "confirm": ("#ffb347", "#b8420a"),
}


class _OrbWidget(QWidget):
    def __init__(self, size: int, top_margin: int):
        super().__init__()
        self._size = size
        self._level = 0.0
        self._target_level = 0.0
        self._phase = 0.0
        self._state = "idle"
        self._color = QColor(ORB_STATES["idle"][0])
        self._deep_color = QColor(ORB_STATES["idle"][1])
        self._target_color = QColor(self._color)
        self._target_deep_color = QColor(self._deep_color)

        self._opacity = 0.0
        self._target_opacity = 0.0
        self._pending_hide = False

        self._caption_text = ""
        self._caption_opacity = 0.0
        self._caption_target_opacity = 0.0
        self._caption_idle_frames = 0
        self._caption_hold_frames = 240
        self._top_margin = top_margin
        self._caption_width = 345
        self._capsule_height = 78

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._total_w = 620
        self._total_h = self._capsule_height + 34

        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self._total_w) // 2
        y = top_margin
        self.setGeometry(x, y, self._total_w, self._total_h)

        self._orb_cx = 56
        self._orb_cy = 56

        self.hide()

        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    # ─── Animation ───

    def _tick(self):
        self._level += (self._target_level - self._level) * 0.22
        self._phase += 0.012 + 0.02 * self._level
        self._opacity += (self._target_opacity - self._opacity) * 0.18
        self._color = self._blend(self._color, self._target_color, 0.14)
        self._deep_color = self._blend(
            self._deep_color, self._target_deep_color, 0.14)

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

    @staticmethod
    def _blend(source: QColor, target: QColor, amount: float) -> QColor:
        return QColor(
            round(source.red() + (target.red() - source.red()) * amount),
            round(source.green() + (target.green() - source.green()) * amount),
            round(source.blue() + (target.blue() - source.blue()) * amount),
        )

    def set_state(self, state: str):
        if state not in ORB_STATES:
            return
        self._state = state
        main, deep = ORB_STATES[state]
        self._target_color = QColor(main)
        self._target_deep_color = QColor(deep)

    def set_caption(self, text: str):
        self._caption_text = text.strip()
        self._caption_idle_frames = 0
        if self._caption_text:
            self._caption_target_opacity = 1.0
        font = QFont("Segoe UI Semibold", 11)
        metrics = QFontMetrics(font)
        bounds = metrics.boundingRect(
            0, 0, self._caption_width, 10000,
            Qt.TextFlag.TextWordWrap, self._caption_text)
        desired = max(78, bounds.height() + 54)
        screen = QApplication.primaryScreen().availableGeometry()
        max_height = max(78, screen.height() - self._top_margin - 34)
        self._capsule_height = min(desired, max_height)
        self._total_h = self._capsule_height + 34
        self.setGeometry((screen.width() - self._total_w) // 2,
                         screen.top() + self._top_margin,
                         self._total_w, self._total_h)
        self.update()

    def animate_show(self):
        self._pending_hide = False
        self._target_opacity = 1.0
        if not self.isVisible():
            super().show()

    def animate_hide(self):
        self._target_opacity = 0.0
        self._caption_text = ""
        self._caption_target_opacity = 0.0
        self._pending_hide = True

    # ─── Drawing ───

    def paintEvent(self, event):
        if self._opacity <= 0.01:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        op = self._opacity
        capsule = QRectF(12, 17, self._total_w - 24, self._capsule_height)

        # The voice session is a single translucent capsule around the orb,
        # live caption, activity waveform, and the real stop shortcut.
        for i in range(4, 0, -1):
            grow = i * 3
            glow = QColor(self._color)
            glow.setAlpha(int(13 * (1 - (i - 1) / 4) * op))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawRoundedRect(capsule.adjusted(-grow, -grow, grow, grow),
                                    capsule.height() / 2 + grow,
                                    capsule.height() / 2 + grow)

        panel = QColor("#191919")
        panel.setAlpha(int(246 * op))
        painter.setBrush(panel)
        border = QColor("#45474d")
        border.setAlpha(int(220 * op))
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(capsule, capsule.height() / 2,
                                capsule.height() / 2)

        # Animated morphing orb at the leading edge.
        cx, cy = self._orb_cx, self._orb_cy
        radius = self._size * (0.34 + 0.07 * self._level)
        points = []
        for i in range(48):
            angle = i / 48 * math.tau
            wobble = (0.045 * math.sin(3 * angle + self._phase)
                      + 0.03 * math.sin(5 * angle - 1.3 * self._phase)
                      + self._level * 0.14 * math.sin(2 * angle + 2 * self._phase))
            r = radius * (1 + wobble)
            points.append(QPointF(cx + r * math.cos(angle),
                                  cy + r * math.sin(angle)))
        body = QPainterPath(points[0])
        for i in range(len(points)):
            p0, p1 = points[(i - 1) % len(points)], points[i]
            p2, p3 = points[(i + 1) % len(points)], points[(i + 2) % len(points)]
            c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6,
                         p1.y() + (p2.y() - p0.y()) / 6)
            c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6,
                         p2.y() - (p3.y() - p1.y()) / 6)
            body.cubicTo(c1, c2, p2)
        body.closeSubpath()

        glow = QRadialGradient(QPointF(cx, cy), radius * 2.2)
        halo = QColor(self._color)
        halo.setAlpha(int(90 * op))
        glow.setColorAt(0, halo)
        glow.setColorAt(1, QColor(halo.red(), halo.green(), halo.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(cx, cy), radius * 1.8, radius * 1.8)

        light = QRadialGradient(QPointF(cx - radius * .32, cy - radius * .4),
                                radius * 1.6)
        for pos, color in ((0, QColor("#ffffff")),
                           (.35, self._color.lighter(140)),
                           (1, self._deep_color)):
            shade = QColor(color)
            shade.setAlpha(int(250 * op))
            light.setColorAt(pos, shade)
        painter.setBrush(light)
        painter.drawPath(body)

        # State label and live voice text.
        labels = {
            "idle": "TARZ  /  READY",
            "listening": "01  /  LISTENING",
            "thinking": "02  /  THINKING",
            "speaking": "03  /  SPEAKING",
            "confirm": "04  /  NEEDS APPROVAL",
        }
        painter.setPen(QColor(154, 158, 165, int(245 * op)))
        painter.setFont(QFont("Consolas", 8))
        painter.drawText(QPointF(96, 49), labels.get(self._state, "TARZ"))

        caption = self._caption_text or {
            "idle": "Ready when you are",
            "listening": "Listening for your request...",
            "thinking": "Planning your request...",
            "speaking": "TARZ is speaking...",
            "confirm": "Waiting for your approval...",
        }.get(self._state, "")
        painter.setPen(QColor(255, 255, 255, int(245 * op)))
        painter.setFont(QFont("Segoe UI Semibold", 11))
        caption_rect = QRectF(96, 55, self._caption_width,
                              max(18, self._capsule_height - 45))
        painter.drawText(caption_rect,
                         Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignTop, caption)

        # Audio bars move with microphone level and keep a soft idle pulse.
        if self._state in ("listening", "speaking"):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, int(235 * op)))
            profile = (.42, .72, 1, .78, .95, .62, .4)
            for i, scale in enumerate(profile):
                wave = max(0.0, min(1.0,
                    self._level * scale + .12 * math.sin(self._phase * 3 + i)))
                height = 4 + 20 * wave
                x = self._total_w - 166 + i * 6
                painter.drawRoundedRect(QRectF(x, cy - height / 2, 3, height),
                                        1.5, 1.5)

        # Display the shortcut that actually cancels TARZ in this app.
        key_rect = QRectF(self._total_w - 110, 44, 96, 24)
        painter.setBrush(QColor(255, 255, 255, int(12 * op)))
        painter.setPen(QPen(QColor(255, 255, 255, int(70 * op)), 1))
        painter.drawRoundedRect(key_rect, 6, 6)
        painter.setPen(QColor(205, 208, 214, int(245 * op)))
        painter.setFont(QFont("Consolas", 7))
        painter.drawText(key_rect, Qt.AlignmentFlag.AlignCenter,
                         "Ctrl+Shift+Space")


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
        self._notifications = {}
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
                    self.widget.set_caption("")
                    self.widget.set_state("listening")
                    self.widget.animate_show()
                elif action == "hide":
                    self.widget.animate_hide()
                    self.widget.set_state("idle")
                elif action == "state":
                    self.widget.set_state(payload)
                elif action == "level":
                    self.widget.set_target_level(payload)
                elif action == "caption":
                    self.widget.set_caption(payload)
                elif action == "notification":
                    dialog_id, title, message, kind, callback = payload
                    if dialog_id in self._notifications:
                        self._notifications[dialog_id].close()

                    def finish(result, key=dialog_id, done=callback):
                        self._notifications.pop(key, None)
                        done(result)

                    popup = _ApprovalPopup(title, message, kind, finish)
                    self._notifications[dialog_id] = popup
                    popup.show()
                elif action == "close_notification":
                    popup = self._notifications.pop(payload, None)
                    if popup:
                        popup.close()
        except queue.Empty:
            pass

    def show(self):
        self._queue.put(("show", None))

    def set_state(self, state: str):
        self._queue.put(("state", state))

    def hide(self):
        self._queue.put(("hide", None))

    def update_level(self, rms: float):
        self._queue.put(("level", rms))

    def update_caption(self, text: str):
        self._queue.put(("caption", text))

    def show_notification(self, dialog_id: str, title: str, message: str,
                          kind: str, on_result):
        self._queue.put(("notification", (dialog_id, title, message, kind, on_result)))

    def close_notification(self, dialog_id: str):
        self._queue.put(("close_notification", dialog_id))


_orb: OrbOverlay | None = None


def get_orb() -> OrbOverlay:
    global _orb
    if _orb is None:
        _orb = OrbOverlay()
    return _orb
