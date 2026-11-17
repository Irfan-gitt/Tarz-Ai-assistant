"""Desktop UI bridge for the TARZ v3 graph.

Run from the repository root with ``python Main/v3_ui.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading

import webview

import v3
from Tools.cancel_state import cancel_event, TaskCancelled


class Api:
    def __init__(self):
        v3.set_human_hooks(
            confirm=v3.human_in_the_loop,
            ask=v3.ask_human,
            cancel=cancel_event,
        )
        self.graph = v3.build_graph()
        self.history: list = []
        self.lock = threading.Lock()

    def send_message(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "Type a message first."}
        with self.lock:
            cancel_event.clear()
            try:
                out = self.graph.invoke(
                    {"user_text": text, "messages": self.history},
                    {"recursion_limit": 40},
                )
                self.history = out["messages"][-v3.WINDOW:]
                return {"ok": True, "reply": out.get("spoken") or out.get("final") or "Done."}
            except TaskCancelled:
                return {"ok": False, "error": "Request cancelled."}
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def new_chat(self) -> bool:
        with self.lock:
            self.history = []
        return True

    def cancel(self) -> bool:
        cancel_event.set()
        return True


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    api = Api()
    page = Path(__file__).resolve().parent.parent / "tarz-ui" / "screens" / "chat.html"
    webview.create_window(
        "TARZ · v3", str(page), js_api=api, width=1280, height=800,
        min_size=(900, 620), resizable=True,
    )
    webview.start()
