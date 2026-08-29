import winsound
import collections
import queue
import numpy as np
import sounddevice as sd
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
whisper_tiny = WhisperModel("tiny", device="cpu", compute_type="int8")

_audio_q = queue.Queue()
_selected_device = None
_selected_hostapi = None


def get_best_input_device():
    """
    Auto-selects a real hardware microphone, excluding Bluetooth headset
    mics AND virtual/mapper devices (which silently follow the OS default
    input, including a BT headset if that's the current Windows default).
    Returns (device_index, hostapi_name) — hostapi_name tells the caller
    whether WASAPI-specific stream settings are safe to apply.
    """
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


_ducking_protected = False


def _play_wake_beep():
    winsound.Beep(800, 100)
    winsound.Beep(1200, 100)


def wait_for_wake_word():
    ring = collections.deque(maxlen=int(WINDOW_SECONDS * SAMPLE_RATE))
    print("👂 Always listening for wake word (say 'Tarz')...")

    device, hostapi_name = get_best_input_device()

    stream_kwargs = dict(
        device=device,
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=BLOCK_SAMPLES,
        callback=_callback,
    )
    if hostapi_name == "Windows WASAPI":
        stream_kwargs["extra_settings"] = sd.WasapiSettings(auto_convert=True)

    with sd.InputStream(**stream_kwargs):
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
                _play_wake_beep()
                return
