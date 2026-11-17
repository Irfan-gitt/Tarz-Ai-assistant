"""Desktop chat and speech settings bridge for the TARZ v3 graph."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import uuid

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import sounddevice as sd
import webview

import v3
from Audio.stt import live_listen
from Audio.tts import speak
from Audio.orb_overlay import get_orb
from Audio.wake_word import wait_for_wake_word
from Tools.cancel_state import TaskCancelled, cancel_event

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "config_settings" / "voice_settings.json"
DEFAULT_SETTINGS = {
    "stt_provider": "auto", "tts_provider": "groq", "tts_enabled": False,
    "background_listening": False,
    "input_device": None, "output_device": None, "speed": 1.0, "volume": 1.0,
}


class Api:
    def __init__(self):
        self._dialog_condition = threading.Condition()
        self._dialog_answers = {}
        self._dialog_waiting = {}
        v3.set_ui_hooks(ask=self._ask_from_ui, confirm=self._confirm_from_ui)
        v3.PRINT_OUTPUT = False
        v3.set_human_hooks(confirm=self._confirm_from_ui, ask=self._ask_from_ui, cancel=cancel_event)
        self._graph = v3.build_graph()
        self._history: list = []
        self._lock = threading.Lock()
        self._events_lock = threading.Lock()
        self._events = []
        self._listener_stop = threading.Event()
        self._listener_thread = None
        try:
            self._settings = {**DEFAULT_SETTINGS,
                              **json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            self._settings = dict(DEFAULT_SETTINGS)

    def get_voice_settings(self) -> dict:
        return dict(self._settings)

    def get_audio_devices(self) -> dict:
        inputs, outputs = [], []
        for index, device in enumerate(sd.query_devices()):
            label = f"{device['name']} (device {index})"
            if device["max_input_channels"] > 0:
                inputs.append({"id": index, "label": label})
            if device["max_output_channels"] > 0:
                outputs.append({"id": index, "label": label})
        return {"inputs": inputs, "outputs": outputs}

    def get_events(self) -> dict:
        with self._events_lock:
            events, self._events = self._events, []
        return {"events": events}

    def _request_dialog(self, kind: str, message: str):
        dialog_id = uuid.uuid4().hex
        with self._dialog_condition:
            self._dialog_waiting[dialog_id] = kind
        title = "TARZ needs approval" if kind == "confirm" else "TARZ needs your reply"
        try:
            get_orb().show_notification(
                dialog_id, title, message, kind,
                lambda response: self._resolve_dialog(dialog_id, response),
            )
        except Exception:
            self._resolve_dialog(dialog_id, False if kind == "confirm" else "")
        with self._dialog_condition:
            while dialog_id not in self._dialog_answers:
                self._dialog_condition.wait()
            self._dialog_waiting.pop(dialog_id, None)
            return self._dialog_answers.pop(dialog_id)

    def _resolve_dialog(self, dialog_id: str, response):
        with self._dialog_condition:
            if dialog_id not in self._dialog_waiting:
                return
            self._dialog_answers[dialog_id] = response
            self._dialog_condition.notify_all()

    def _confirm_from_ui(self, question: str) -> bool:
        return bool(self._request_dialog("confirm", question))

    def _ask_from_ui(self, question: str) -> str:
        return str(self._request_dialog("answer", question) or "")

    def _sync_listener(self):
        enabled = bool(self._settings.get("background_listening"))
        if enabled and (self._listener_thread is None or not self._listener_thread.is_alive()):
            self._listener_stop.clear()
            self._listener_thread = threading.Thread(target=self._listen_in_background,
                                                     name="tarz-wake-listener", daemon=True)
            self._listener_thread.start()
        elif not enabled:
            self._listener_stop.set()

    def _stop_listener(self):
        self._listener_stop.set()
        with self._dialog_condition:
            ids = list(self._dialog_waiting)
            for dialog_id, kind in self._dialog_waiting.items():
                self._dialog_answers[dialog_id] = False if kind == "confirm" else ""
            self._dialog_condition.notify_all()
        if ids:
            overlay = get_orb()
            for dialog_id in ids:
                overlay.close_notification(dialog_id)

    def _listen_in_background(self):
        while not self._listener_stop.is_set():
            try:
                heard_wake_word = wait_for_wake_word(
                    self._listener_stop, self._settings.get("input_device"))
                if not heard_wake_word or self._listener_stop.is_set():
                    continue
                transcript = live_listen(timeout=8, provider=self._settings["stt_provider"],
                                         input_device=self._settings["input_device"]).strip()
                if not transcript or self._listener_stop.is_set():
                    continue
                result = self.send_message(transcript)
                with self._events_lock:
                    self._events.append({"user": transcript,
                                         "reply": result.get("reply") or result.get("error", "TARZ could not answer.")})
            except Exception as exc:
                with self._events_lock:
                    self._events.append({"error": f"Background listening stopped: {type(exc).__name__}: {exc}"})
                self._listener_stop.set()

    def save_voice_settings(self, settings: dict) -> dict:
        stt = settings.get("stt_provider", self._settings["stt_provider"])
        tts = settings.get("tts_provider", self._settings["tts_provider"])
        if stt not in {"auto", "gemini", "cartesia"}:
            return {"ok": False, "error": "Choose a valid speech-to-text provider."}
        if tts not in {"groq", "cartesia", "windows", "off"}:
            return {"ok": False, "error": "Choose a valid text-to-speech provider."}
        try:
            speed = min(1.5, max(0.5, float(settings.get("speed", 1.0))))
            volume = min(1.0, max(0.0, float(settings.get("volume", 1.0))))
            devices = self.get_audio_devices()
            input_device = settings.get("input_device")
            output_device = settings.get("output_device")
            input_device = int(input_device) if input_device not in (None, "") else None
            output_device = int(output_device) if output_device not in (None, "") else None
            if input_device is not None and input_device not in {d["id"] for d in devices["inputs"]}:
                raise ValueError("Selected microphone is not available.")
            if output_device is not None and output_device not in {d["id"] for d in devices["outputs"]}:
                raise ValueError("Selected speaker is not available.")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        self._settings = {
            "stt_provider": stt, "tts_provider": tts,
            "tts_enabled": bool(settings.get("tts_enabled", self._settings["tts_enabled"])),
            "background_listening": bool(settings.get("background_listening", self._settings["background_listening"])),
            "input_device": input_device, "output_device": output_device,
            "speed": speed, "volume": volume,
        }
        SETTINGS_PATH.write_text(json.dumps(self._settings, indent=2), encoding="utf-8")
        self._sync_listener()
        return {"ok": True, "settings": dict(self._settings)}

    def listen_once(self) -> dict:
        try:
            text = live_listen(timeout=8, provider=self._settings["stt_provider"],
                               input_device=self._settings["input_device"])
            text = text.strip()
            return {"ok": bool(text), "text": text,
                    "error": "No speech was recognized." if not text else ""}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def test_voice(self) -> dict:
        if self._settings["tts_provider"] == "off":
            return {"ok": False, "error": "Select a text-to-speech provider first."}
        try:
            speak("TARZ voice test. Your speech settings are working.",
                  provider=self._settings["tts_provider"],
                  output_device=self._settings["output_device"],
                  speed=self._settings["speed"], volume=self._settings["volume"])
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def send_message(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "Type a message first."}
        with self._lock:
            cancel_event.clear()
            try:
                result = self._graph.invoke(
                    {"user_text": text, "messages": self._history}, {"recursion_limit": 40})
                self._history = result["messages"][-v3.WINDOW:]
                reply = result.get("spoken") or result.get("final") or "Done."
                if self._settings["tts_enabled"] and self._settings["tts_provider"] != "off":
                    speak(reply, provider=self._settings["tts_provider"],
                          output_device=self._settings["output_device"],
                          speed=self._settings["speed"], volume=self._settings["volume"])
                return {"ok": True, "reply": reply}
            except TaskCancelled:
                return {"ok": False, "error": "Request cancelled."}
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def new_chat(self) -> bool:
        with self._lock:
            self._history = []
        return True


if __name__ == "__main__":
    os.chdir(ROOT)
    api = Api()
    page = ROOT / "tarz-ui" / "screens" / "chat.html"
    window = webview.create_window("TARZ", page.as_uri(), js_api=api, width=1280, height=800,
                                   min_size=(720, 560), resizable=True)
    window.events.loaded += api._sync_listener
    window.events.closed += api._stop_listener
    webview.start()
