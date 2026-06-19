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

    def send_message(self, text: str) -> str:
        result = think(text)
        if self.tts_enabled:
            threading.Thread(target=speak, args=(result,), daemon=True).start()
        return result

    def start_voice(self) -> str:
        return stt_listen() or ""

    def set_tts(self, enabled: bool) -> bool:
        self.tts_enabled = bool(enabled)
        return self.tts_enabled


if __name__ == "__main__":
    api = Api()
    html_path = os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "gui.html")

    window = webview.create_window(
        "TARZ", html_path,
        js_api=api,
        width=520, height=720,
        resizable=True,
        background_color="#0c0c0c"
    )
    webview.start()
