from faster_whisper import WhisperModel
import sounddevice as sd
import soundfile as sf
import tempfile
import os
import torch

os.makedirs("temp", exist_ok=True)

# Auto-detect device and set compute type accordingly
device = "cuda" if torch.cuda.is_available() else "cpu"
compute_type = "int8_float16" if device == "cuda" else "int8"

print(f"[STT] Loading Whisper model on {device.upper()}...")
model = WhisperModel(
    "large-v3-turbo",
    device=device,
    compute_type=compute_type
)
print(f"[STT] Model ready on {device.upper()}")


def listen() -> str:
    """Record mic and transcribe to text."""
    sample_rate = 16000
    duration = 6

    print("[STT] Listening...")
    audio = sd.rec(
        int(duration * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="float32"
    )
    sd.wait()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir="temp") as f:
        sf.write(f.name, audio, sample_rate)
        segments, _ = model.transcribe(f.name, language="en")
        text = " ".join([s.text for s in segments]).strip()
        os.unlink(f.name)  # clean up temp file

    print(f"[STT] Heard: {text}")
    return text
