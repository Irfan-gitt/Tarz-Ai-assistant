from pathlib import Path
import subprocess
import pygame
import os

os.makedirs("temp", exist_ok=True)

VOICE_MODEL = "en_US-ryan-high.onnx"

# Tuning
LENGTH_SCALE = 1.05
NOISE_SCALE = 0.667
NOISE_W = 0.8


def speak(text: str):
    text = text.replace("TARZ", "Tarz").replace("tarz", "Tarz")
    PIPER = "configs\\piper.exe"

    subprocess.run([
        PIPER,
        "--model", VOICE_MODEL,
        "--output_file", "temp/output.wav",
        "--length_scale", str(LENGTH_SCALE),
        "--noise_scale", str(NOISE_SCALE),
        "--noise_w", str(NOISE_W),
    ], input=text.encode(), capture_output=True)

    pygame.mixer.init()
    pygame.mixer.music.load("temp/output.wav")
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        pygame.time.Clock().tick(10)
    pygame.mixer.quit()
