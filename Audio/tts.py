from pathlib import Path
import subprocess
import os
import uuid
import wave

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

BASE_DIR = Path(__file__).resolve().parent.parent
TEMP_DIR = BASE_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)

PIPER = BASE_DIR / "configs" / "piper.exe"
VOICE_MODEL = BASE_DIR / "en_US-ryan-high.onnx"

# Tuning
LENGTH_SCALE = 1.05
NOISE_SCALE = 0.667
NOISE_W = 0.8


def _is_valid_wav(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 44:
        return False

    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnchannels() > 0 and wav.getframerate() > 0 and wav.getnframes() > 0
    except wave.Error:
        return False


def speak(text: str):
    if not text or not text.strip():
        return

    text = text.replace("TARZ", "Tarz").replace("tarz", "Tarz")
    output_file = TEMP_DIR / f"tts_{os.getpid()}_{uuid.uuid4().hex}.wav"

    try:
        result = subprocess.run(
            [
                str(PIPER),
                "--model", str(VOICE_MODEL),
                "--output_file", str(output_file),
                "--length_scale", str(LENGTH_SCALE),
                "--noise_scale", str(NOISE_SCALE),
                "--noise_w", str(NOISE_W),
            ],
            input=text,
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
            timeout=60,
        )

        if result.returncode != 0:
            print(f"[TTS] Piper failed: {result.stderr.strip()}")
            return

        if not _is_valid_wav(output_file):
            print(f"[TTS] Skipped invalid WAV: {output_file}")
            return

        pygame.mixer.init()
        pygame.mixer.music.load(str(output_file))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)

    except pygame.error as e:
        print(f"[TTS] Playback failed: {e}")
    except subprocess.TimeoutExpired:
        print("[TTS] Piper timed out.")
    except Exception as e:
        print(f"[TTS] Failed: {e}")
    finally:
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.quit()
        except pygame.error:
            pass

        try:
            output_file.unlink(missing_ok=True)
        except OSError:
            pass
