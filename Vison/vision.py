import re
import time
from openai import OpenAI
from groq import Groq
import os
import base64
import pyautogui
from dotenv import load_dotenv
load_dotenv()


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    return _THINK_BLOCK.sub("", text or "").strip()


API_KEYS = [
    os.getenv("GROQ_VISION_KEY_1"),
    os.getenv("GROQ_VISION_KEY_2"),
]
current_key = 0


def get_client():
    return Groq(api_key=API_KEYS[current_key])


VISION_MODELS = [
    "qwen/qwen3.6-27b",
]


def describe_screen(question):
    screenshot = pyautogui.screenshot()
    screenshot.save("screen_vision.png")
    with open("screen_vision.png", "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    for model in VISION_MODELS:
        try:
            print(f"Trying {model}...")
            response = get_client().chat.completions.create(
                model=model,
                stream=True,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{img_b64}"}},
                        {"type": "text", "text": f"""You are TARZ, an AI assistant with vision capabilities.

        User question: "{question}"

        Look at this screenshot and answer the user's question directly.
        Be specific and helpful. If they ask about something on screen, point it out clearly."""}
                    ]
                }]
            )
            full_response = ""
            for chunk in response:
                if chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    print(text, end="", flush=True)
                    full_response += text
            print()
            return _strip_think(full_response)   # ← fixed
        except Exception as e:
            if "429" in str(e):
                global current_key
                current_key = (current_key + 1) % len(API_KEYS)
                print(f"Rate limited on {model}, trying next...")
                time.sleep(1)
                continue
            elif "404" in str(e):
                print(f"{model} no image support, trying next...")
                continue
            else:
                print(f"Error: {e}")
                continue

    return "All models failed or rate limited. Try again in a minute."


def vision_verify_system(question):
    screenshot = pyautogui.screenshot()
    screenshot.save("supervisor_vision.png")
    with open("supervisor_vision.png", "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    for model in VISION_MODELS:
        try:
            print(f"Trying {model}...")
            response = get_client().chat.completions.create(
                model=model,
                stream=True,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{img_b64}"}},
                        {"type": "text", "text": f"""TARZ, is an AI assistant with real time computer control and vision capability, You are working on its main brain side you confirms is the task user was assigned is completed or not
                            TWO TYPES OF TASK:
                            1. Tarz can do clicks on screen so If the task involved a click, don't just check where the cursor is — check whether
                            the screen now shows the actual result of that click succeeding (e.g. a song is
                            playing, a message was sent, a page loaded). Cursor position alone proves nothing

                            2. To verify the task is done for another activity just explain briefly answers must be accurate without being hallucinate if you dont know the answer just say "I don't know" and don't make it up

        User question: "{question}"
        Start your response with exactly one word: YES, NO, or UNCLEAR.
        Then, on the same line, briefly explain what you see that supports that answer and what app or screen is currently visible."""}
                    ]
                }]
            )
            full_response = ""
            for chunk in response:
                if chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    print(text, end="", flush=True)
                    full_response += text
            print()
            return _strip_think(full_response)   # ← fixed
        except Exception as e:
            if "429" in str(e):
                global current_key
                current_key = (current_key + 1) % len(API_KEYS)
                print(f"Rate limited on {model}, trying next...")
                time.sleep(1)
                continue
            elif "404" in str(e):
                print(f"{model} no image support, trying next...")
                continue
            else:
                print(f"Error: {e}")
                continue

    return "All models failed or rate limited. Try again in a minute."
