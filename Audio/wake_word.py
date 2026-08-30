# Audio/wake_word.py
import os
import numpy as np
import sounddevice as sd
import pygame
import openwakeword
from openwakeword.model import Model

from Audio.ducking import protect_process_from_ducking

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280  # openWakeWord expects ~80ms chunks at 16kHz — verify against
# its docs if you hit a shape-mismatch error, this may shift by version

BT_HEADSET_HINTS = ("headset", "hands-free", "hfp", "hsp", "hf audio")
VIRTUAL_DEVICE_HINTS = (
    "sound mapper", "primary sound capture driver", "mapper")
PREFERRED_HOSTAPI_ORDER = ["Windows WASAPI",
                           "Windows DirectSound", "MME", "Windows WDM-KS"]

_selected_device = None
_selected_hostapi = None


def get_best_input_device():
    """Unchanged from before — real hardware mic, avoiding BT headsets and virtual/mapper devices."""
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


# ─── Wake word engine setup ───

# bundled options: "hey_jarvis", "alexa", "hey_mycroft"
WAKE_MODEL_NAME = "hey_jarvis"
DETECTION_THRESHOLD = 0.5

# one-time download on first run, cached after
openwakeword.utils.download_models()
oww_model = Model(wakeword_models=[WAKE_MODEL_NAME])


_WAKE_CHIME_PATH = r"Audio\beep.wav"


def _play_wake_beep():
    if not pygame.mixer.get_init():
        pygame.mixer.init()
    pygame.mixer.Sound(_WAKE_CHIME_PATH).play()


_ducking_protected = False


def wait_for_wake_word():
    print(
        f"👂 Always listening for wake word (say '{WAKE_MODEL_NAME.replace('_', ' ')}')...")

    device, hostapi_name = get_best_input_device()

    stream_kwargs = dict(
        device=device,
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
    )
    if hostapi_name == "Windows WASAPI":
        stream_kwargs["extra_settings"] = sd.WasapiSettings(auto_convert=True)

    with sd.InputStream(**stream_kwargs) as stream:
        global _ducking_protected
        if not _ducking_protected:
            protect_process_from_ducking("Spotify.exe")
            _ducking_protected = True

        oww_model.reset()  # clear any stale prediction buffer from a previous call

        while True:
            audio_chunk, _ = stream.read(FRAME_SAMPLES)
            audio_chunk = audio_chunk.flatten()

            prediction = oww_model.predict(audio_chunk)
            score = prediction[WAKE_MODEL_NAME]

            if score > DETECTION_THRESHOLD:
                print(f"🎯 Wake word detected! (score={score:.2f})")
                _play_wake_beep()
                return
