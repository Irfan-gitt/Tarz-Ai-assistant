import re
import os
from duckduckgo_search import DDGS
from langchain_groq import ChatGroq
from dotenv import load_dotenv
import webbrowser
import subprocess
import pyautogui
from PIL import Image
import time
from groq import Groq

import base64
from openai import OpenAI
from prompt import SYSTEM_PROMPT
from get_coordinates import find_on_screen
from vision import describe_screen
from app_shortcut import focus_search
from execute_action import execute_step
load_dotenv()

api_key = os.getenv("groq_api")
llm = ChatGroq(api_key=api_key, temperature=0.7,
               model="llama-3.3-70b-versatile")


def listen():
    user = input("You: ").strip()
    return user


conversation_history = [
    {"role": "system", "content": SYSTEM_PROMPT}]


def think(user_input):

    steps_history = []

    for step in range(10):
        history_text = "\n".join(
            steps_history) if steps_history else "No steps yet"
        screen = describe_screen(
            "Describe what app is open and what's visible. One sentence.")

        decision = llm.invoke(f"""\
You are TARZ, an AI that controls a Windows computer.
You work in a loop: Think → Act → Observe screen → Think again.

Goal :{user_input}

Previous Steps :{history_text}

Current Screen{screen}


Available actions (ONE per reply):
CLICK: <element>        — click a visible UI element on screen
TYPE: <text>            — type text
PRESS: <key>            — keyboard key (enter, esc, tab, win, etc.)
ACTION: <app>           — open an app by name
SEARCH:<search>         — search inside an app
SHORTCUT: <app/action>  — app shortcut (spotify/search, youtube/search)
VOLUME: <up/down/mute>
WAIT: <seconds>
READ: <question>        — read something from screen
CHAT: <response>        — talk to user
DONE: <summary>         — task complete

Rules:
- ONE action per reply
- ALWAYS start with ACTION if app needs to be opened
- Check screen description first — if app not open, open it
- Only use SHORTCUT after app is confirmed open on screen
- After ACTION always use WAIT: 3 before the next step
- Use screen description to decide each next step
- DONE only when task is confirmed complete on screen

Examples:
"open spotify and play X"
  ACTION: spotify → WAIT: 3 → SHORTCUT: spotify/search → TYPE: X → PRESS: enter → WAIT: 2 → CLICK: play button → DONE

"open youtube and search X"
  ACTION: brave → WAIT: 3 → TYPE: youtube.com → PRESS: enter → WAIT: 3 → SHORTCUT: youtube/search → TYPE: X → PRESS: enter → DONE

"volume up"
  VOLUME: up → DONE
"""
                              ).content.strip()
        print(f"[DEBUG] Decision: {decision}")  # helpful while testing

        print(f"[Step {step+1}] {decision}")

        if decision.startswith("DONE:"):
            return decision.split("DONE:")[1].strip()

        if decision.startswith("CHAT:"):
            conversation_history.append(
                {"role": "user", "content": user_input})
            response = llm.invoke(conversation_history).content.strip()
            conversation_history.append(
                {"role": "assistant", "content": response})
            return decision.split("CHAT:")[1].strip()
        result = execute_step(decision)
        print(f"Result:{result}")

        steps_history.append(f"Step {step+1}: {decision} -> {result}")
        time.sleep(0.5)

    return "Max steps reached"


def main():
    while True:
        user_input = listen()
        ss = think(user_input)
        print(ss)


main()
