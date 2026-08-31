# Audio/wake_word.py
import time
import collections
import queue
import numpy as np
import sounddevice as sd
import pygame
from faster_whisper import WhisperModel
from silero_vad import load_silero_vad, get_speech_timestamps

from Audio.ducking import protect_process_from_ducking

SAMPLE_RATE = 16000
BLOCK_MS = 30
BLOCK_SAMPLES = int(SAMPLE_RATE * BLOCK_MS / 1000)
WINDOW_SECONDS = 2.0
CHECK_INTERVAL_BLOCKS = int(WINDOW_SECONDS * 1000 / BLOCK_MS)

BT_HEADSET_HINTS = ("headset", "hands-free", "hfp", "hsp", "hf audio")
VIRTUAL_DEVICE_HINTS = (
    "sound mapper", "primary sound capture driver", "mapper")
PREFERRED_HOSTAPI_ORDER = ["Windows WASAPI",
                           "Windows DirectSound", "MME", "Windows WDM-KS"]

vad_model = load_silero_vad()
# bumped from tiny for accuracy
whisper_tiny = WhisperModel("small", device="cpu", compute_type="int8")

_audio_q = queue.Queue()
_selected_device = None
_selected_hostapi = None
_ducking_protected = False

_WAKE_CHIME_PATH = r"Audio\beep.wav"
DONE_CHIME_PATH = r"Audio\end_beep.wav"


def get_best_input_device():
    global _selected_device, _selected_hostapi
    if _selected_device is not None:
        return _selected_device, _selected_hostapi

    devices = sd.query_devices()
    hostapis = sd.query_hostapis()

    candidates = []
    for i, d in enumerate(devices):
        if d['max_input_channels'] <= 0:
            continue
        name_lower = d['name'].lower()
        if any(h in name_lower for h in VIRTUAL_DEVICE_HINTS):
            continue
        if any(h in name_lower for h in BT_HEADSET_HINTS):
            continue
        hostapi_name = hostapis[d['hostapi']]['name']
        candidates.append((i, d, hostapi_name))

    if not candidates:
        print(
            "[Audio] No non-Bluetooth mic found — falling back to system default input.")
        _selected_device = None
        _selected_hostapi = None
        return None, None

    def sort_key(item):
        _, _, hostapi_name = item
        try:
            return PREFERRED_HOSTAPI_ORDER.index(hostapi_name)
        except ValueError:
            return len(PREFERRED_HOSTAPI_ORDER)

    candidates.sort(key=sort_key)
    idx, dev, hostapi_name = candidates[0]
    print(
        f"[Audio] Using input device: {dev['name']} (index {idx}, {hostapi_name})")
    _selected_device = idx
    _selected_hostapi = hostapi_name
    return idx, hostapi_name


def _callback(indata, frames, time_info, status):
    if status:
        print("Mic status:", status)
    _audio_q.put(indata.copy().flatten())


def _play_wake_beep():
    if not pygame.mixer.get_init():
        pygame.mixer.init()
    pygame.mixer.Sound(_WAKE_CHIME_PATH).play()


def play_done_chime():
    if not pygame.mixer.get_init():
        pygame.mixer.init()
    pygame.mixer.Sound(DONE_CHIME_PATH).play()


def _open_stream_kwargs():
    device, hostapi_name = get_best_input_device()
    kwargs = dict(
        device=device,
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=BLOCK_SAMPLES,
        callback=_callback,
    )
    if hostapi_name == "Windows WASAPI":
        kwargs["extra_settings"] = sd.WasapiSettings(auto_convert=True)
    return kwargs


def wait_for_wake_word():
    """Blocks until 'Tarz' is heard, then plays a chime and returns."""
    ring = collections.deque(maxlen=int(WINDOW_SECONDS * SAMPLE_RATE))
    print("👂 Always listening for wake word (say 'Tarz')...")

    with sd.InputStream(**_open_stream_kwargs()):
        global _ducking_protected
        if not _ducking_protected:
            protect_process_from_ducking("Spotify.exe")
            _ducking_protected = True

        blocks_since_check = 0
        while True:
            block = _audio_q.get()
            ring.extend(block)
            blocks_since_check += 1

            if blocks_since_check < CHECK_INTERVAL_BLOCKS or len(ring) < ring.maxlen:
                continue
            blocks_since_check = 0

            window = np.array(ring, dtype=np.float32)
            speech_ts = get_speech_timestamps(
                window, vad_model, sampling_rate=SAMPLE_RATE)
            if not speech_ts:
                continue

            segments, _ = whisper_tiny.transcribe(window, language="en")
            text = " ".join(seg.text for seg in segments).lower()
            print(f"[debug] heard: {text!r}")

            if "hey" in text:
                print(f"🎯 Wake word detected in: {text!r}")

                return


def wait_for_followup(timeout: float = 5.0) -> bool:
    """
    After TARZ finishes speaking, listens for up to `timeout` seconds using
    VAD only (cheap speech DETECTION, no transcription) to see if the user
    keeps talking without saying 'Tarz' again. Returns True if speech was
    detected (caller should skip the wake word and listen directly),
    False if the window passes in silence (caller should require 'Tarz' again).
    """
    while not _audio_q.empty():
        _audio_q.get_nowait()  # drop stale audio queued up while TARZ was speaking

    # short window — just need onset, not full phrase
    ring = collections.deque(maxlen=int(0.5 * SAMPLE_RATE))
    start = time.time()

    print(
        f"👂 Listening for follow-up ({timeout:.0f}s, no wake word needed)...")

    with sd.InputStream(**_open_stream_kwargs()):
        while time.time() - start < timeout:
            try:
                block = _audio_q.get(timeout=0.1)
            except queue.Empty:
                continue
            ring.extend(block)
            if len(ring) < ring.maxlen:
                continue

            window = np.array(ring, dtype=np.float32)
            speech_ts = get_speech_timestamps(
                window, vad_model, sampling_rate=SAMPLE_RATE)
            if speech_ts:
                print("🗣️ Follow-up detected — skipping wake word.")
                return True

    print("🔇 No follow-up — back to requiring 'Tarz'.")
    return False
