from faster_whisper import WhisperModel
import sounddevice as sd
import soundfile as sf
import tempfile
import os

os.makedirs("temp", exist_ok=True)

print("[STT] Loading Whisper model...")
model = WhisperModel(
    "large-v3-turbo",
    device="cuda",
    compute_type="int8_float16"
)


def listen() -> str:
    """Record mic and transcribe to text."""
    sample_rate = 16000
    duration = 6  # seconds — adjust as needed

    print("[STT] Listening...")
    audio = sd.rec(
        int(duration * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="float32"
    )
    sd.wait()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, audio, sample_rate)
        segments, _ = model.transcribe(f.name, language="en")
        text = " ".join([s.text for s in segments]).strip()

    print(f"[STT] Heard: {text}")
    return text
