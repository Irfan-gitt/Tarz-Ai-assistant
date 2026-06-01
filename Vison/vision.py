import time
from openai import OpenAI
from groq import Groq
import os
import base64
import pyautogui
from dotenv import load_dotenv
load_dotenv()


API_KEYS = [
    os.getenv("GROQ_VISION_KEY_1"),
    os.getenv("GROQ_VISION_KEY_2"),

]
current_key = 0


def get_client():
    return Groq(
        api_key=API_KEYS[current_key]
    )


VISION_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
]


def take_screenshot():
    screenshot = pyautogui.screenshot()
    screenshot.save("screen.png")
    return "screen.png"


def describe_screen(question):
    take_screenshot()

    with open("screen.png", "rb") as f:
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
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"}
                        },
                        {
                            "type": "text",
                            "text": f"""You are JARVIS, an AI assistant with vision capabilities.
                            
        User question: "{question}"

        Look at this screenshot and answer the user's question directly.
        Be specific and helpful. If they ask about something on screen, point it out clearly."""
                        }
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

            return full_response
        except Exception as e:
            if "429" in str(e):
                global current_key
                current_key = (current_key + 1) % len(API_KEYS)
                print(f"Rate limited on {model}, trying next...")
                time.sleep(1)  # Wait 1 sec before next
                continue
            elif "404" in str(e):
                print(f"{model} no image support, trying next...")
                continue
            else:
                print(f"Error: {e}")
                continue

    return "All models failed or rate limited. Try again in a minute."
