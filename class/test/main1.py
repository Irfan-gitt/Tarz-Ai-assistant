import os
import pyttsx3
from duckduckgo_search import DDGS
import speech_recognition as sr
from langchain_groq import ChatGroq
from faster_whisper import WhisperModel
import sounddevice as sd
import numpy as np
from dotenv import load_dotenv
import pygame
import webbrowser

import subprocess
import pyautogui
import pytesseract
from PIL import Image
import time

from langchain_community.tools import DuckDuckGoSearchRun
from elevenlabs.client import ElevenLabs
load_dotenv()

# listen

recognition = sr.Recognizer()

# TTS - engine setup
elevenlabs = ElevenLabs(
    api_key="sk_9b01908fcd2bc15b4d46764c1e65ea878db6d475fd43ee88")


# LLM - setup

api_key = os.getenv("groq_api")

llm = ChatGroq(api_key=api_key,
               temperature=0.7,
               model="llama-3.3-70b-versatile"
               )

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
    respose = llm.invoke(user)

    return respose.content


def main():
    while True:
        user_speech = listen()
        print(user_speech)


main()
