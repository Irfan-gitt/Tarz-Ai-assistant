import pyttsx3
from duckduckgo_search import DDGS
import speech_recognition as sr
from Main.llm_gateway import get_gateway, AllModelsFailed
from Main.config import PLAIN_LLMS
from faster_whisper import WhisperModel
import sounddevice as sd
import numpy as np
import pygame
import webbrowser

import subprocess
import pyautogui
import pytesseract
from PIL import Image
import time

from langchain_community.tools import DuckDuckGoSearchRun

# listen

recognition = sr.Recognizer()

# LLM - setup


model = WhisperModel(
    "large-v3-turbo",
    device="cuda",
    compute_type="int8_float16"
)


def listen(duration=6, fs=16000):
    print("Listening...")

    audio = sd.rec(int(duration * fs), samplerate=fs,
                   channels=1, dtype="float32")
    sd.wait()

    audio = np.squeeze(audio)
    audio = audio / np.max(np.abs(audio))

    segments, _ = model.transcribe(
        audio,
        language="en",
        beam_size=5
    )

    text = " ".join(seg.text.strip() for seg in segments)
    return text


def think(user):
    try:
        return get_gateway().invoke(PLAIN_LLMS, [user]).content
    except AllModelsFailed as e:
        print(f"[LLM] All models failed: {e}")
        return "I can't answer right now."


def main():
    while True:
        user_speech = listen()
        print(user_speech)


main()
