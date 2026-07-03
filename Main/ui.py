"""
Main/gui_backend.py — TARZ desktop UI
Run: python Main/gui_backend.py
"""

import os
import threading
import webview
from tarz import think
from Audio.stt import listen as stt_listen
from Audio.tts import speak


class Api:
    def __init__(self):
        self.tts_enabled = True
        self.listen_enabled = False
        self._cancelled = False

    def send_message(self, text: str) -> str:
        self._cancelled = False
        result = think(text)
        if self._cancelled:
            return "__cancelled__"
        if self.tts_enabled:
            threading.Thread(target=speak, args=(result,), daemon=True).start()
        return result

    def cancel_thinking(self) -> bool:
        self._cancelled = True
        return True

    def start_voice(self) -> str:
        return stt_listen() or ""

    def set_tts(self, enabled: bool) -> bool:
        self.tts_enabled = bool(enabled)
        return self.tts_enabled

    def set_listen(self, enabled: bool) -> bool:
        self.listen_enabled = bool(enabled)
        return self.listen_enabled


if __name__ == "__main__":
    api = Api()
    html_path = os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "gui.html")

    window = webview.create_window(
        "TARZ", html_path,
        js_api=api,
        width=900, height=700,
        resizable=True,
        background_color="#0a0a0a"
    )
    webview.start()
