"""Desktop chat and speech settings bridge for the TARZ v3 graph."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
import uuid

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ["TARZ_UI_DOCK"] = "1"

import sounddevice as sd
import webview
import keyboard

import v3
from Audio.stt import live_listen
from Audio.tts import speak
from Audio.orb_overlay import get_orb, use_external_dock
from Audio.wake_word import wait_for_wake_word
from Tools.cancel_state import TaskCancelled, cancel_event

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "config_settings" / "voice_settings.json"
DEFAULT_SETTINGS = {
    "stt_provider": "auto", "tts_provider": "groq", "tts_enabled": False,
    "background_listening": False,
    "dock_enabled": True,
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
        self._request_running = threading.Event()
        self._voice_activity_lock = threading.Lock()
        self._voice_activity = "idle"
        self._dock_lock = threading.Lock()
        self._dock_state = {"state": "idle", "title": "", "active": "",
                            "step_label": "", "steps": []}
        self._dock_window = None
        self._main_window = None
        self._dock_height = None
        use_external_dock(True)
        v3.set_progress_hook(self._on_agent_progress)
        self._events_lock = threading.Lock()
        self._events = []
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
        if not self._request_running.is_set():
            with self._dock_lock:
                if self._dock_state.get("state") not in {"working", "done", "error"}:
                    self._dock_state.update({"state": state, "active": ""})

    def set_dock_window(self, window):
        self._dock_window = window
        window.events.before_show += self._make_dock_transparent
        window.events.loaded += self._on_dock_loaded

    def _on_dock_loaded(self):
        self._make_dock_transparent()
        if self._settings.get("dock_enabled", True) and self._dock_window is not None:
            self._dock_window.show()

    def _make_dock_transparent(self):
        """Give the transparent WebView canvas a dark native host instead of Windows white."""
        if os.name != "nt" or self._dock_window is None:
            return
        try:
            from System.Drawing import Color
            from System import Action
            native = self._dock_window.native
            if native is not None:
                # Do not use TransparencyKey: matching pixels become click-through.
                dark = Color.FromArgb(255, 24, 24, 27)
                native.Invoke(Action(lambda: setattr(native, "BackColor", dark)))
        except Exception as exc:
            with self._events_lock:
                self._events.append({"error": f"Dock transparency setup failed: {exc}"})

    def set_main_window(self, window):
        self._main_window = window

    def close_dock(self):
        """Hide the dock until the user enables it again in Speech options."""
        self._settings["dock_enabled"] = False
        try:
            SETTINGS_PATH.write_text(json.dumps(self._settings, indent=2), encoding="utf-8")
            if self._dock_window is not None:
                self._dock_window.hide()
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": f"Could not close the dock: {exc}"}

    def open_main_window(self):
        if self._main_window is not None:
            self._main_window.show()
            self._main_window.restore()
        return True

    def _resize_dock(self, expanded: bool, step_count: int = 0):
        # The dock stays a single compact capsule during idle and task progress.
        height = 72
        if height == self._dock_height or self._dock_window is None:
            return
        try:
            import ctypes
            width = 370
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            self._dock_window.resize(width, height)
            self._dock_window.move((screen_w - width) // 2,
                                   18)
            self._dock_height = height
        except Exception as exc:
            with self._events_lock:
                self._events.append({"error": f"Dock resize failed: {exc}"})

    def get_dock_state(self) -> dict:
        with self._dock_lock:
            if (self._dock_state.get("state") == "done"
                    and time.monotonic() - self._dock_state.get("done_at", 0) > 10.0):
                listening = bool(self._settings.get("background_listening"))
                self._dock_state = {"state": "listening" if listening else "idle",
                                    "title": "", "active": "", "step_label": "", "steps": []}
                self._dock_state["done_at"] = 0
            state = dict(self._dock_state)
            state["steps"] = [dict(step) for step in state.get("steps", [])]
        self._resize_dock(state.get("state") in {"working", "done", "error", "approval"},
                          len(state.get("steps", [])))
        return state

    def _set_dock_state(self, state: str, title: str = "", active: str = "", steps=None):
        with self._dock_lock:
            self._dock_state = {"state": state, "title": title,
                                "active": active, "step_label": active if steps else "",
                                "steps": list(steps or [])}
            if state == "done":
                self._dock_state["done_at"] = time.monotonic()

    def _on_agent_progress(self, event: dict):
        kind = event.get("type")
        if kind == "plan":
            self._set_dock_state("working", event.get("title", "Working"),
                                  event.get("active", "Working…"), event.get("steps", []))
        elif kind == "activity":
            with self._dock_lock:
                if self._dock_state.get("state") == "working":
                    self._dock_state["active"] = event.get("text", "Working…")
        elif kind == "finished":
            with self._dock_lock:
                self._dock_state.update({"state": "done", "active": "Task complete"})
                self._dock_state["done_at"] = time.monotonic()

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
        # The chat page polls this endpoint. Follow-ups are NOT pushed from here:
        # they are asked naturally in the reply when the user greets TARZ.
        with self._events_lock:
            events, self._events = self._events, []
        return {"events": events}

    def send_message_from_dock(self, text: str) -> dict:
        """Run a dock request and mirror both sides into the main chat transcript."""
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "Type a message first."}
        with self._events_lock:
            self._events.append({"user": text})
        result = self.send_message(text)
        reply = result.get("reply") or result.get("error") or "TARZ could not answer."
        with self._events_lock:
            self._events.append({"assistant": reply})
        return result

    def _request_dialog(self, kind: str, message: str):
        dialog_id = uuid.uuid4().hex
        with self._dialog_condition:
            self._dialog_waiting[dialog_id] = kind
        if kind == "answer":
            # Clarifying questions belong in the chat, not a system-wide popup.
            with self._events_lock:
                self._events.append({"question": {"id": dialog_id, "text": message}})
        else:
            with self._events_lock:
                self._events.append({"assistant": f"Approval required: {message}"})
            with self._dock_lock:
                self._dock_state.update({"state": "approval", "title": "Approval needed",
                                         "active": message})
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
            kind = self._dialog_waiting[dialog_id]
            self._dialog_answers[dialog_id] = response
            self._dialog_condition.notify_all()
        if kind == "confirm":
            with self._events_lock:
                self._events.append({"user": "Approved the action." if response else "Denied the action."})
        with self._dock_lock:
            if self._request_running.is_set() and self._dock_state.get("state") == "approval":
                self._dock_state["state"] = "working"
                self._dock_state["active"] = "Continuing task…"

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
        v3.set_progress_hook(None)
        use_external_dock(False)
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
            "dock_enabled": bool(settings.get("dock_enabled", self._settings.get("dock_enabled", True))),
            "input_device": input_device, "output_device": output_device,
            "speed": speed, "volume": volume,
        }
        SETTINGS_PATH.write_text(json.dumps(self._settings, indent=2), encoding="utf-8")
        self._sync_listener()
        if self._dock_window is not None:
            try:
                (self._dock_window.show if self._settings["dock_enabled"]
                 else self._dock_window.hide)()
            except Exception:
                pass
        return {"ok": True, "settings": dict(self._settings)}

    def listen_once(self) -> dict:
        self._set_dock_state("listening", "Voice input", "Listening…")
        try:
            text = live_listen(timeout=8, provider=self._settings["stt_provider"],
                               input_device=self._settings["input_device"])
            text = text.strip()
            result = {"ok": bool(text), "text": text,
                      "error": "No speech was recognized." if not text else ""}
            if not text:
                self._set_dock_state("idle", "", "No speech was recognized.")
            return result
        except Exception as exc:
            self._set_dock_state("idle", "", f"Voice input failed: {type(exc).__name__}")
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
            self._request_running.set()
            self._set_dock_state("working", text, "Planning your request…")
            background_voice = bool(self._settings.get("background_listening"))
            try:
                if background_voice:
                    self._set_voice_activity("thinking")
                result = self._graph.invoke(
                    {"user_text": text, "messages": self._history}, {"recursion_limit": 40})
                self._history = result["messages"][-v3.WINDOW:]
                reply = result.get("spoken") or result.get("final") or "Done."
                with self._dock_lock:
                    self._dock_state.update({"state": "done", "active": reply[:220]})
                    self._dock_state["done_at"] = time.monotonic()
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
                self._set_dock_state("done", text, "Stopped.")
                return {"ok": False, "error": "Request cancelled."}
            except Exception as exc:
                self._set_dock_state("error", text, f"Task failed: {type(exc).__name__}")
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            finally:
                self._request_running.clear()
                if background_voice:
                    self._set_voice_activity("listening")

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
    screen_w = screen_h = 1080
    try:
        import ctypes
        screen_w = ctypes.windll.user32.GetSystemMetrics(0)
        screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    except Exception:
        pass
    dock_page = ROOT / "tarz-ui" / "dock.html"
    dock = webview.create_window(
        "TARZ Dock", dock_page.as_uri(), js_api=api,
        width=370, height=72, x=(screen_w - 370) // 2, y=18,
        frameless=True, on_top=True, transparent=True, resizable=False,
        easy_drag=False, text_select=True, focus=True,
        background_color="#18181b", hidden=True,
    )
    api.set_dock_window(dock)
    api.set_main_window(window)
    window.events.loaded += api._sync_listener
    window.events.closed += api.close
    dock.events.closed += api.close
    webview.start()
