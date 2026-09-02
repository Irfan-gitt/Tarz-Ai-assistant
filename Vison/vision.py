import re
import os
import base64
import pyautogui
from dotenv import load_dotenv
from groq import Groq
from langchain_openrouter import ChatOpenRouter
from langchain_core.messages import HumanMessage

load_dotenv()

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    return _THINK_BLOCK.sub("", text or "").strip()


GROQ_API_KEYS = [
    os.getenv("GROQ_VISION_KEY_1"),
    os.getenv("GROQ_VISION_KEY_2"),
]
current_key = 0

GROQ_VISION_MODEL = "qwen/qwen3.8-27b"
api_open = os.getenv("OPENROUTER_API_KEY2")

llm_vision_openrouter = ChatOpenRouter(
    model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", temperature=0.2, api_key=api_open)


def _get_groq_client():
    return Groq(api_key=GROQ_API_KEYS[current_key])


def _ask_vision(img_b64: str, prompt_text: str) -> str:
    """Primary: OpenRouter's free router (auto-picks a free vision-capable
    model). Fallback: Groq qwen3.8-27b, rotating across GROQ_VISION_KEY_*
    on 429, only if OpenRouter fails entirely."""
    global current_key

    try:
        print("Trying OpenRouter (openrouter/free)...")
        message = HumanMessage(content=[
            {"type": "image_url", "image_url": {
                "url": f"data:image/png;base64,{img_b64}"}},
            {"type": "text", "text": prompt_text},
        ])
        full_response = ""
        for chunk in llm_vision_openrouter.stream([message]):
            if chunk.content:
                print(chunk.content, end="", flush=True)
                full_response += chunk.content
        print()
        if full_response:
            return _strip_think(full_response)
    except Exception as e:
        print(f"OpenRouter vision error: {e}")

    for _ in range(len(GROQ_API_KEYS)):
        try:
            print(f"Trying Groq {GROQ_VISION_MODEL}...")
            response = _get_groq_client().chat.completions.create(
                model=GROQ_VISION_MODEL,
                stream=True,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{img_b64}"}},
                        {"type": "text", "text": prompt_text}
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
            return _strip_think(full_response)
        except Exception as e:
            if "429" in str(e):
                current_key = (current_key + 1) % len(GROQ_API_KEYS)
                print("Rate limited on Groq, trying next key...")
                continue
            print(f"Groq vision error: {e}")
            break

    return "All models failed or rate limited. Try again in a minute."


def describe_screen(question):
    screenshot = pyautogui.screenshot()
    screenshot.save("screen_vision.png")
    with open("screen_vision.png", "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    prompt = f"""You are TARZ, an AI assistant with vision capabilities.

User question: "{question}"

Look at this screenshot and answer the user's question directly.
Be specific and helpful. If they ask about something on screen, point it out clearly."""

    return _ask_vision(img_b64, prompt)


def vision_verify_system(question):
    screenshot = pyautogui.screenshot()
    screenshot.save("supervisor_vision.png")
    with open("supervisor_vision.png", "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    prompt = f"""TARZ, is an AI assistant with real time computer control and vision capability, You are working on its main brain side you confirms is the task user was assigned is completed or not
                    TWO TYPES OF TASK:
                    1. Tarz can do clicks on screen so If the task involved a click, don't just check where the cursor is — check whether
                    the screen now shows the actual result of that click succeeding (e.g. a song is
                    playing, a message was sent, a page loaded). Cursor position alone proves nothing

                    2. To verify the task is done for another activity just explain briefly answers must be accurate without being hallucinate if you dont know the answer just say "I don't know" and don't make it up

User question: "{question}"
Start your response with exactly one word: YES, NO, or UNCLEAR.
Then, on the same line, briefly explain what you see that supports that answer and what app or screen is currently visible."""

    return _ask_vision(img_b64, prompt)
