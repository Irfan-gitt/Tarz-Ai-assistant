import re
import base64
import pyautogui
from groq import Groq
from openai import OpenAI
from Main.llm_gateway import get_gateway, AllModelsFailed
from Main.config import VISION_LLMS
from langchain_core.messages import HumanMessage

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    return _THINK_BLOCK.sub("", text or "").strip()


def _ask_vision(img_b64: str, prompt_text: str) -> str:
    """Primary: OpenRouter's free router (auto-picks a free vision-capable
    model). Fallback: Groq qwen3.8-27b, rotating across GROQ_VISION_KEY_*
    on 429, only if OpenRouter fails entirely."""
    message = HumanMessage(content=[
            {"type": "image_url", "image_url": {
                "url": f"data:image/png;base64,{img_b64}"}},
            {"type": "text", "text": prompt_text},
        ])

    def call(name, cfg, key):
        if cfg["provider"] == "groq_vision":
            client = Groq(api_key=key)
        else:
            client = OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")
        response = client.chat.completions.create(
            model=cfg["model"], messages=[{"role": "user", "content": message.content}]
        )
        return response.choices[0].message.content

    try:
        return _strip_think(get_gateway().run(VISION_LLMS, call))
    except AllModelsFailed as e:
        print(f"[vision] All models failed: {e}")
        return "I couldn't inspect the screen just now."


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
