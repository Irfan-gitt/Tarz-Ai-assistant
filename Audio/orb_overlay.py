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
    QApplication, QWidget, QLabel, QPushButton, QLineEdit, QVBoxLayout,
    QHBoxLayout, QFrame,
)
from PyQt6.QtGui import QPainter, QRadialGradient, QConicalGradient, QColor, QPen, QFont, QFontMetrics, QCursor
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF

try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows fallback
    winsound = None


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
        self.setFixedWidth(410)
        self.setStyleSheet("""
            QWidget#approvalPopup { background:#15171b; color:#f5f6f8; font:14px 'Segoe UI'; border:1px solid #343840; border-radius:16px; }
            QWidget { color:#f5f6f8; font:14px 'Segoe UI'; }
            QLabel#eyebrow { color:#ffae72; font-size:11px; font-weight:700; letter-spacing:1px; }
            QLabel#title { font-size:17px; font-weight:600; }
            QLabel#message { color:#c5c8ce; }
            QFrame#details { background:#1d2025; border:1px solid #30343b; border-radius:11px; }
            QLabel#key { color:#949aa4; font-size:12px; }
            QLabel#value { color:#f1f2f4; font-size:13px; }
            QLineEdit { background:#0d0e10; border:1px solid #45474d; border-radius:8px; padding:10px; color:white; }
            QPushButton { border:1px solid #41454d; border-radius:9px; padding:7px 14px; background:#202227; color:#f3f4f6; font-weight:600; }
            QPushButton#primary { background:#ff8a3d; color:#171717; border-color:#ff8a3d; }
            QPushButton#primary:hover { background:#ffa061; border-color:#ffa061; }
            QPushButton#deny:hover { background:#30333a; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(7)

        eyebrow = QLabel("TARZ  /  ACTION APPROVAL" if kind == "confirm" else "TARZ  /  NEEDS YOUR INPUT")
        eyebrow.setObjectName("eyebrow")
        heading = QLabel(title)
        heading.setObjectName("title")
        layout.addWidget(eyebrow)
        layout.addWidget(heading)

        action_name, args = self._parse_action(message) if kind == "confirm" else (None, None)
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

        self._answer = None
        if kind == "answer":
            self._answer = QLineEdit()
            self._answer.setPlaceholderText("Type your answer…")
            layout.addWidget(self._answer)
            self._answer.returnPressed.connect(self._submit_answer)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel" if kind == "answer" else "Deny")
        cancel.setObjectName("deny")
        primary = QPushButton("Send" if kind == "answer" else "Allow")
        primary.setObjectName("primary")
        cancel.clicked.connect(self._cancel)
        primary.clicked.connect(self._submit_answer if kind == "answer" else self._approve)
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
        if self._answer is not None:
            self._answer.setFocus()

    def _finish(self, result):
        if self._finished:
            return
        self._finished = True
        self._on_result(result)
        self.close()

    def _approve(self):
        self._finish(True)

    def _submit_answer(self):
        self._finish(self._answer.text() if self._answer else "")

    def _cancel(self):
        self._finish(False if self._kind == "confirm" else "")

    def closeEvent(self, event):
        if not self._finished:
            self._finished = True
            self._on_result(False if self._kind == "confirm" else "")
        super().closeEvent(event)


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
                    self.widget.animate_show()
                elif action == "hide":
                    self.widget.animate_hide()
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
