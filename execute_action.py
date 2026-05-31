import os
from duckduckgo_search import DDGS
from langchain_groq import ChatGroq
from dotenv import load_dotenv
import pyautogui
from PIL import Image
import time
from groq import Groq
import base64
from openai import OpenAI
from prompt import SYSTEM_PROMPT
from get_coordinates import find_on_screen
from vision import describe_screen
from langchain_core.tools import tool
from news import search_news
from app_shortcut import volume_down, volume_up, mute, SHORTCUTS


load_dotenv()

api_key = os.getenv("groq_api")
llm = ChatGroq(api_key=api_key, temperature=0.7,
               model="llama-3.3-70b-versatile")


@tool
def click(element: str) -> str:
    """
    Click a UI element on screen.

    IMPORTANT: element must be SHORT and SIMPLE (2-4 words max):

    Rules:
    - Use the EXACT visible text or element name only
    - NO descriptions, NO artist names, NO "titled", NO "by"
    - NO sentences or long phrases

    Examples:
    Good: "Sailor Song", "play button", "search bar", "Send", "Like", "Subscribe", "John", "Settings"
    Bad: "A song titled Sailor Song", "the green play button", "click on John's chat"

    Works for: Spotify, WhatsApp, YouTube, Chrome, any app
    """
    result = find_on_screen(element)
    if result["found"]:
        return f"Clicked {element}"
    return f"Could not find {element}"


@tool
def type_text(text: str) -> str:
    """Type text into the currently focused input field."""
    pyautogui.write(text, interval=0.05)
    return f"Typed: {text}"


@tool
def press_key(key: str) -> str:
    """Press a keyboard key. Examples: enter, esc, tab, space, win."""
    pyautogui.press(key)
    return f"Pressed: {key}"


@tool
def open_app(app_name: str) -> str:
    """Open any application by name using Windows search."""
    pyautogui.press("win")
    time.sleep(1)
    pyautogui.write(app_name, interval=0.05)
    time.sleep(1)
    pyautogui.press("enter")
    time.sleep(1)
    return f"Opened {app_name}"


@tool
def read_screen(question: str) -> str:
    """Read and describe what is currently visible on screen."""
    return describe_screen(question)


@tool
def news_update(question: str) -> str:
    """Search for latest news on the topic and return summary + article links.
    Use when user asks about current events, news, wars, sports, politics."""
    return search_news.invoke({"query": question})


@tool
def volume_control(action: str) -> str:
    """Control system volume. action must be: up, down, or mute."""
    if action == "up":
        return volume_up()
    if action == "down":
        return volume_down()
    if action == "mute":
        return mute()
    return f"Unknown volume action: {action}"


@tool
def use_shortcut(app: str, action: str) -> str:
    """
    Trigger an in-app keyboard shortcut.
    Examples: app=spotify action=search, app=youtube action=search
    """
    app = app.strip().lower()
    action = action.strip().lower()
    if app in SHORTCUTS and action in SHORTCUTS[app]:
        keys = SHORTCUTS[app][action]
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)
        time.sleep(0.3)
        return f"Used {app}/{action} shortcut"
    return f"No shortcut found for {app}/{action}"


@tool
def wait(seconds: int) -> str:
    """Wait for a number of seconds. Use after opening apps or navigating."""
    time.sleep(seconds)
    return f"Waited {seconds}s"


@tool
def done(summary: str) -> str:
    """Call this when the task is fully complete."""
    return f"Done: {summary}"


"""
def execute_step(decision):

    if decision.startswith("CLICK:"):
        target = decision.split("CLICK:")[1].strip()

        result = find_on_screen(target)
        if result["found"]:
            return f"Clicked {target}"
        return f"Could not find {target}"

    elif decision.startswith("SEARCH"):

        current_app = describe_screen(
            "What app is currently open and in focus? Reply ONE word only."
        )

        current_app = current_app.strip().lower().split()[0]

        print(f"[DEBUG] Detected app: '{current_app}'")
        print(f"[DEBUG] App in SHORTCUTS: {current_app in SHORTCUTS}")

        success = focus_search(current_app)

        print(f"[DEBUG] Shortcut success: {success}")

        if success:
            pyautogui.press("enter")
            return f"Opened search in {current_app} via shortcut"

        return f"No search shortcut for {current_app}"

    elif decision.startswith("TYPE:"):
        text = decision.split("TYPE:")[1].strip()
        pyautogui.write(text, interval=0.05)
        return f"Typed: {text}"

    elif decision.startswith("PRESS:"):
        key = decision.split("PRESS:")[1].strip()
        pyautogui.press(key)
        return f"Pressed: {key}"

    elif decision.startswith("ACTION:"):
        app = decision.split("ACTION:")[1].strip()
        pyautogui.press("win")
        time.sleep(1)
        pyautogui.write(app, interval=0.05)
        time.sleep(1)
        pyautogui.press("enter")
        time.sleep(1)
        return f"Opened {app}"

    elif decision.startswith("READ:"):
        question = decision.split("READ:")[1].strip()
        return describe_screen(question)
    elif decision.startswith("VOLUME:"):
        action = decision.split("VOLUME:")[1].strip().lower()
        if action == "up":
            return volume_up()
        elif action == "down":
            return volume_down()
        elif action == "mute":
            return mute()
    elif decision.startswith("SHORTCUT:"):
        value = decision.split("SHORTCUT:")[1].strip()

        # Split app and action
        if "/" in value:
            app, action = value.split("/", 1)
            app = app.strip().lower()
            action = action.strip().lower()

            if app in SHORTCUTS and action in SHORTCUTS[app]:
                keys = SHORTCUTS[app][action]
                if len(keys) == 1:
                    pyautogui.press(keys[0])
                else:
                    pyautogui.hotkey(*keys)
                time.sleep(0.3)
                return f"Used {action} shortcut on {app}"

            return f"No shortcut found for {app}/{action}"

        return f"Invalid shortcut format: {value}"
    elif decision.startswith("WAIT:"):
        seconds = int(decision.split("WAIT:")[1].strip())
        time.sleep(seconds)
        return f"Waited {seconds}s"
    return f"Unknown: {decision}"

"""
