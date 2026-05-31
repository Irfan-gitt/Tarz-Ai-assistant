from langchain_cerebras import ChatCerebras
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
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
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from execute_action import click, type_text, press_key, open_app, read_screen, volume_control, news_update, use_shortcut, wait, done


load_dotenv()

api_key = os.getenv("groq_api")
api_or = os.getenv("OPENROUTER_KEY")
api_cb = os.getenv("CEREBRAS_API_KEY")

TOOLS = [click, type_text, press_key, open_app,
         read_screen, news_update, volume_control, use_shortcut, wait, done]


# Replace Gemini with Cerebras
llm_tools = ChatCerebras(
    model="gpt-oss-120b",
    api_key=api_cb,
    temperature=0.2
).bind_tools(TOOLS)
llm_plain = ChatGroq(
    api_key=os.getenv("groq_api"),
    temperature=0.7,
    model="llama-3.3-70b-versatile"
)


SYSTEM = """\
You are TARZ, an AI that controls a Windows computer.
Use the provided tools to complete the user's goal step by step.
Call done() only when the task is confirmed complete.
Always open_app() first if an app needs to be launched, then wait(5).

Spotify search flow — always follow this exact order:
1. open_app("spotify")
2. wait(3)
3. use_shortcut(app="spotify", action="search")
4. type_text("song name")
5. press_key("enter")
6. wait(2)
7. click("green play button")   ← always click green play button to play
8. wait(2)
9. read_screen to confirm playing
10. done()

News flow:
- ALWAYS use search_news() for ANY question about current events
- "latest news", "what's happening", "update on X" → search_news()
- Never browse manually for news, always use search_news tool

"""


def listen():
    user = input("You: ").strip()
    return user


# TASK_KEYWORDS = [
    "open", "close", "click", "type", "search", "play", "pause",
    "volume", "minimize", "maximize", "go to", "navigate", "launch",
    "start", "stop", "next", "previous", "mute", "scroll", "press",
    "news", "latest", "update", "what's happening", "tell me about",
    "war", "politics", "weather", "sports", "headlines"


def is_computer_task(user_input: str) -> bool:
    response = llm_plain.invoke([
        SystemMessage("""Reply only YES or NO.
        
Should this use computer control tools?
YES for: opening apps, clicking, typing, searching web, playing music, news lookup, volume, any task on computer
NO for: pure conversation, jokes, math, general knowledge questions with no action needed

Be generous with YES - when in doubt say YES."""),
        HumanMessage(user_input)
    ])
    return "YES" in response.content.upper()


def think(user_input):

    messages = [
        SystemMessage(content=SYSTEM),
        HumanMessage(content=user_input)
    ]
    if not is_computer_task(user_input):
        print("[Router] Conversation detected → plain LLM")
        return llm_plain.invoke(messages).content

    print("[Router] Task detected → tool LLM")
    for step in range(15):
        try:
            response = llm_tools.invoke(messages)
        except Exception as e:
            print(f"[ERROR] LLM call failed: {e}")

            try:
                fallback = llm_plain.invoke(messages)
                return fallback.content
            except Exception as e2:
                return "Something went wrong, please try again."

        messages.append(response)

        if not response.tool_calls:
            return response.content

        for tool_call in response.tool_calls:
            name = tool_call["name"]
            args = tool_call["args"]

            print(f"[Step {step+1}] {name}({args})")

            tool_fn = next((t for t in TOOLS if t.name == name), None)
            if tool_fn:
                result = tool_fn.invoke(args)
            else:
                result = f"Unknown tool: {name}"

            print(f" -> {result}")

            if name == "done":
                return args.get("summary", result)

            messages.append(ToolMessage(content=str(result),
                            tool_call_id=tool_call["id"]))
            time.sleep(0.5)

    return "Max steps reached"


def main():
    while True:
        user_input = listen()
        if not user_input:
            continue
        result = think(user_input)
        print(f"TARZ: {result}")


main()
