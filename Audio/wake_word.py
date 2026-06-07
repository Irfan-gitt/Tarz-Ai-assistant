from faster_whisper import WhisperModel
import pyaudio
import numpy as np
import threading
import wave
import tempfile
import os

# Reuse your existing model
model = WhisperModel("large-v3-turbo", device="cuda",
                     compute_type="int8_float16")

_callback = None
_running = False

WAKE_WORDS = ["tarz", "hey tarz", "hi tarz", "yo tarz", "tars"]


def _record_chunk(seconds: float = 2.0, rate: int = 16000) -> str:
    """Record a short chunk and save to temp file."""
    pa = pyaudio.PyAudio()
    stream = pa.open(rate=rate, channels=1,
                     format=pyaudio.paInt16, input=True,
                     frames_per_buffer=1024)

    frames = []
    for _ in range(int(rate / 1024 * seconds)):
        frames.append(stream.read(1024, exception_on_overflow=False))

    stream.stop_stream()
    stream.close()
    pa.terminate()

    # Save to temp wav
    tmp = tempfile.mktemp(suffix=".wav")
    with wave.open(tmp, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(frames))

    return tmp


def _listen_loop():
    print("[WakeWord] Listening for 'Hey TARZ'...")

    while _running:
        try:
            tmp = _record_chunk(seconds=2.0)
            segs, _ = model.transcribe(tmp, language="en")
            text = " ".join([s.text for s in segs]).lower().strip()
            os.remove(tmp)

            if text:
                print(f"[WakeWord] Heard: {text}")

            if any(w in text for w in WAKE_WORDS):
                print("[WakeWord] ✓ Hey TARZ detected!")
                if _callback:
                    _callback()

        except Exception as e:
            print(f"[WakeWord] Error: {e}")


def start(on_wake):
    global _callback, _running
    _callback = on_wake
    _running = True
    threading.Thread(target=_listen_loop, daemon=True).start()
    print("[WakeWord] Started")


def stop():
    global _running
    _running = False
