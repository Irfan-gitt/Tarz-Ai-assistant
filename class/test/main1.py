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

whisper_model = WhisperModel("base", device="cpu", compute_type="int8")


def listen(duration=6, fs=16000):
    print("Listening...")

    audio = sd.rec(int(duration * fs), samplerate=fs,
                   channels=1, dtype="float32")
    sd.wait()

    audio = np.squeeze(audio)
    audio = audio / np.max(np.abs(audio))

    segments, _ = whisper_model.transcribe(
        audio,
        language="en",
        beam_size=5
    )

    text = " ".join(seg.text.strip() for seg in segments)
    return text


def speak(user_speech):

    engine = pyttsx3.init()

    voices = engine.getProperty('voices')

    engine.setProperty('voice', voices[0].id)

    engine.setProperty('rate', 180)

    engine.setProperty('volume', 1.0)
    if not user_speech:
        return

    try:
        engine.say(user_speech)
        engine.runAndWait()

    except Exception as e:
        print(f"TTS error: {e}")


def think(user):
    respose = llm.invoke(user)

    if any(word in user.lower() for word in ["news", "search", "find", "what is", "who is"]):
        return search_web(user)

    elif any(word in user.lower() for word in ["spotify"]):
        return system_control()

    else:
        return respose.content


def search_web(query: str):

    search = DuckDuckGoSearchRun()

    result = search.invoke(query)

    url = f"https://www.google.com/search?q={query}"

    webbrowser.open(url)

    return result


def system_control(app):

    pytesseract.pytesseract.tesseract_cmd = (
        r"C:\Users\IRFAN\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
    )

    def spotify(choice):

        # OPEN SPOTIFY
        # =========================

        subprocess.Popen(
            "Spotify.exe"
        )

        time.sleep(5)

        # =========================
        # SEARCH SONG
        # =========================

        song = choice

        pyautogui.hotkey("ctrl", "l")

        time.sleep(1)

        pyautogui.write(song, interval=0.05)

        time.sleep(1)

        pyautogui.press("enter")

        time.sleep(3)

        # =========================
        # SCREENSHOT
        # =========================

        screenshot = pyautogui.screenshot()

        screenshot.save("spotify_screen.png")

        # =========================
        # OCR READ
        # =========================

        text = pytesseract.image_to_string(
            Image.open("spotify_screen.png")
        )

        print("\n===== OCR TEXT =====\n")
        print(text)

        # =========================
        # AI-LIKE DECISION
        # =========================

        if song.lower() in text.lower():

            print("\nSong detected on screen")

            pyautogui.press("tab")

            time.sleep(1)

            pyautogui.press("down")

            time.sleep(1)

            pyautogui.press("enter")

            print("\nPlaying song")

        else:

            print("\nSong not detected")
        return "playing spotify"

    if app == "spotify":
        return spotify()


def main():
    while True:
        user_speech = listen()
        print(user_speech)
        brain = think(user_speech)
        tarz_speech = speak(brain)
        search_web(brain)

        print(brain)


main()
