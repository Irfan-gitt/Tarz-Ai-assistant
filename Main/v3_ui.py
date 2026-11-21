"""Desktop chat and speech settings bridge for the TARZ v3 graph."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
import uuid

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import sounddevice as sd
import webview
import keyboard
from langchain_core.messages import AIMessage

import v3
from Audio.stt import live_listen
from Audio.tts import speak
from Audio.orb_overlay import get_orb
from Audio.wake_word import wait_for_wake_word
from Tools.cancel_state import TaskCancelled, cancel_event
from Memory_and_Retrieval.memory import get_due_followups, mark_followups_asked
from Memory_and_Retrieval.rag import followup_question

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "config_settings" / "voice_settings.json"
DEFAULT_SETTINGS = {
    "stt_provider": "auto", "tts_provider": "groq", "tts_enabled": False,
    "background_listening": False,
    "input_device": None, "output_device": None, "speed": 1.0, "volume": 1.0,
}


class Api:
    def __init__(self):
        v3.require_core_chat_provider()
        self._dialog_condition = threading.Condition()
        self._dialog_answers = {}
        self._dialog_waiting = {}
        v3.set_ui_hooks(ask=self._ask_from_ui, confirm=self._confirm_from_ui)
        v3.PRINT_OUTPUT = False
        v3.set_human_hooks(confirm=self._confirm_from_ui, ask=self._ask_from_ui, cancel=cancel_event)
        self._graph = v3.build_graph()
        v3.GRAPH = self._graph
        v3.start_scheduler(v3.run_scheduled, confirm=self._confirm_from_ui)
        self._history: list = []
        self._lock = threading.Lock()
        self._followup_lock = threading.Lock()
        self._request_running = threading.Event()
        self._voice_activity_lock = threading.Lock()
        self._voice_activity = "idle"
        self._events_lock = threading.Lock()
        self._events = []
        self._next_followup_poll = 0.0
        self._stop_hotkey = None
        try:
            self._stop_hotkey = keyboard.add_hotkey(
                "ctrl+shift+space", self.stop_current_task, suppress=False)
        except Exception as exc:
            self._events.append({"error": f"Global stop shortcut unavailable: {exc}"})
        self._listener_stop = threading.Event()
        self._listener_thread = None
        try:
            self._settings = {**DEFAULT_SETTINGS,
                              **json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            self._settings = dict(DEFAULT_SETTINGS)
        self._voice_activity = "listening" if self._settings.get(
            "background_listening") else "idle"

    def get_voice_settings(self) -> dict:
        return dict(self._settings)

    def get_voice_activity(self) -> dict:
        with self._voice_activity_lock:
            return {"state": self._voice_activity}

    def _set_voice_activity(self, state: str):
        with self._voice_activity_lock:
            self._voice_activity = state
        if self._settings.get("background_listening"):
            get_orb().set_state(state)

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
        # This endpoint is polled by the chat page, so reminders can appear at
        # their due time even when the user has not sent another message.
        if (not self._request_running.is_set()
                and self._followup_lock.acquire(blocking=False)):
            try:
                now = time.monotonic()
                if not self._request_running.is_set() and now >= self._next_followup_poll:
                    self._next_followup_poll = now + 15.0
                    try:
                        due = get_due_followups(limit=1)
                        if due:
                            item = due[0]
                            question = followup_question(item)
                            mark_followups_asked([item["id"]])
                            self._history = [*self._history, AIMessage(content=question)][-v3.WINDOW:]
                            with self._events_lock:
                                self._events.append({"assistant": question})
                    except Exception as exc:
                        with self._events_lock:
                            self._events.append({"error": f"Follow-up reminder failed: {type(exc).__name__}: {exc}"})
            finally:
                self._followup_lock.release()
        with self._events_lock:
            events, self._events = self._events, []
        return {"events": events}

    def _request_dialog(self, kind: str, message: str):
        dialog_id = uuid.uuid4().hex
        with self._dialog_condition:
            self._dialog_waiting[dialog_id] = kind
        if kind == "answer":
            # Clarifying questions belong in the chat, not a system-wide popup.
            with self._events_lock:
                self._events.append({"question": {"id": dialog_id, "text": message}})
        else:
            try:
                get_orb().show_notification(
                    dialog_id, "TARZ needs approval", message, kind,
                    lambda response: self._resolve_dialog(dialog_id, response),
                )
            except Exception:
                self._resolve_dialog(dialog_id, False)
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

    def answer_question(self, question_id: str, answer: str) -> dict:
        answer = (answer or "").strip()
        if not answer:
            return {"ok": False, "error": "Type a reply first."}
        with self._dialog_condition:
            if self._dialog_waiting.get(question_id) != "answer":
                return {"ok": False, "error": "That question is no longer waiting for a reply."}
            self._dialog_answers[question_id] = answer
            self._dialog_condition.notify_all()
        return {"ok": True}

    def _confirm_from_ui(self, question: str) -> bool:
        return bool(self._request_dialog("confirm", question))

    def _ask_from_ui(self, question: str) -> str:
        return str(self._request_dialog("answer", question) or "")

    def _sync_listener(self):
        enabled = bool(self._settings.get("background_listening"))
        if enabled and (self._listener_thread is None or not self._listener_thread.is_alive()):
            self._set_voice_activity("listening")
            self._listener_stop.clear()
            self._listener_thread = threading.Thread(target=self._listen_in_background,
                                                     name="tarz-wake-listener", daemon=True)
            self._listener_thread.start()
        elif not enabled:
            self._listener_stop.set()
            self._set_voice_activity("idle")
            if self._listener_thread is not None:
                get_orb().hide()

    def _stop_listener(self):
        self._listener_stop.set()
        with self._dialog_condition:
            ids = [dialog_id for dialog_id, kind in self._dialog_waiting.items()
                   if kind == "confirm"]
            for dialog_id, kind in self._dialog_waiting.items():
                self._dialog_answers[dialog_id] = False if kind == "confirm" else ""
            self._dialog_condition.notify_all()
        if ids:
            overlay = get_orb()
            for dialog_id in ids:
                overlay.close_notification(dialog_id)

    def close(self):
        self._stop_listener()
        if self._stop_hotkey is not None:
            try:
                keyboard.remove_hotkey(self._stop_hotkey)
            except Exception:
                pass
            self._stop_hotkey = None
        if v3._last_save is not None:
            v3._last_save.join(timeout=15)

    def stop_current_task(self) -> dict:
        """Request cancellation of the active chat or speech task."""
        if not self._request_running.is_set():
            return {"ok": False, "message": "TARZ is not working on a request."}
        cancel_event.set()
        with self._dialog_condition:
            dialog_ids = [dialog_id for dialog_id, kind in self._dialog_waiting.items()
                          if kind == "confirm"]
            for dialog_id, kind in self._dialog_waiting.items():
                self._dialog_answers[dialog_id] = False if kind == "confirm" else ""
            self._dialog_condition.notify_all()
        if dialog_ids:
            overlay = get_orb()
            for dialog_id in dialog_ids:
                overlay.close_notification(dialog_id)
        return {"ok": True, "message": "Stopping TARZ..."}

    def _listen_in_background(self):
        while not self._listener_stop.is_set():
            try:
                self._set_voice_activity("listening")
                heard_wake_word = wait_for_wake_word(
                    self._listener_stop, self._settings.get("input_device"))
                if not heard_wake_word or self._listener_stop.is_set():
                    continue
                transcript = live_listen(timeout=8, provider=self._settings["stt_provider"],
                                         input_device=self._settings["input_device"]).strip()
                if not transcript or self._listener_stop.is_set():
                    get_orb().hide()
                    continue
                with self._events_lock:
                    self._events.append({"user": transcript})
                result = self.send_message(transcript)
                with self._events_lock:
                    self._events.append({"assistant": result.get("reply") or result.get("error", "TARZ could not answer.")})
                get_orb().hide()
            except Exception as exc:
                get_orb().hide()
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
        with self._lock, self._followup_lock:
            cancel_event.clear()
            self._request_running.set()
            background_voice = bool(self._settings.get("background_listening"))
            try:
                if background_voice:
                    self._set_voice_activity("thinking")
                result = self._graph.invoke(
                    {"user_text": text, "messages": self._history}, {"recursion_limit": 40})
                self._history = result["messages"][-v3.WINDOW:]
                reply = result.get("spoken") or result.get("final") or "Done."
                if background_voice:
                    get_orb().update_caption(reply)
                if self._settings["tts_enabled"] and self._settings["tts_provider"] != "off":
                    if background_voice:
                        self._set_voice_activity("speaking")
                    speak(reply, provider=self._settings["tts_provider"],
                          output_device=self._settings["output_device"],
                          speed=self._settings["speed"], volume=self._settings["volume"])
                return {"ok": True, "reply": reply}
            except TaskCancelled:
                return {"ok": False, "error": "Request cancelled."}
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            finally:
                self._request_running.clear()
                if background_voice:
                    self._set_voice_activity("listening")

    def new_chat(self) -> bool:
        with self._lock, self._followup_lock:
            self._history = []
        return True


if __name__ == "__main__":
    os.chdir(ROOT)
    api = Api()
    page = ROOT / "tarz-ui" / "screens" / "chat.html"
    window = webview.create_window("TARZ", page.as_uri(), js_api=api, width=1280, height=800,
                                   min_size=(720, 560), resizable=True)
    window.events.loaded += api._sync_listener
    window.events.closed += api.close
    webview.start()
