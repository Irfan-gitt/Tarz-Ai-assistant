
from Tools.rag import save_preference, save_task
import pyperclip
from Tools.translator import run_translate
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
from Prompts.prompt import SYSTEM_PROMPT
from Screen_Postition.get_coordinates import find_on_screen
from Vison.vision import describe_screen
from langchain_core.tools import tool
from Tools.news import search_news
from Actions.app_shortcut import volume_down, volume_up, mute, SHORTCUTS
from Tools. weather import get_weather
from Tools.timer_alarm import run_timer, run_alarm
from Tools.memory import save_preference, retrieve_similar_task
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
def correct_memory(key: str, correct_value: str) -> str:
    """
    Correct or update something in memory.
    Use when user says 'that's wrong', 'actually it's', 'correct that to'.
    Examples:
    correct_memory(key='name', correct_value='Irfan')
    correct_memory(key='favourite_song', correct_value='Sailor Song')
    """
    save_preference(key, correct_value)
    return f"Corrected: {key} = {correct_value}"


@tool
def remember(key: str, value: str) -> str:
    """
    Save something to memory.
    Use when user says 'remember that...' or shares preferences.

    Examples:
    remember(key='favourite_song', value='Sailor Song by Gigi Perez')
    remember(key='name', value='Irfan')
    remember(key='city', value='Trivandrum')
    """
    save_preference(key, value)
    return f"Remembered: {key} = {value}"


@tool
def press_key(key: str) -> str:
    """Press a keyboard key. Examples: enter, esc, tab, space, win."""
    pyautogui.press(key)
    return f"Pressed: {key}"


@tool
def clipboard(action: str, text: str = "") -> str:
    """
    Control clipboard. 
    action: 'copy' to copy text, 'paste' to get clipboard content

    Examples:
    clipboard(action='copy', text='Hello World')
    clipboard(action='paste')
    """
    try:
        if action == "copy":
            pyperclip.copy(text)
            return f"Copied: {text}"

        elif action == "paste":
            content = pyperclip.paste()
            return f"Clipboard contains: {content}"

        return "Invalid action. Use 'copy' or 'paste'"

    except Exception as e:
        return f"Clipboard error: {e}"


@tool
def open_app(app_name: str) -> str:
    """Open any application by name using Windows search."""
    pyautogui.press("win")
    time.sleep(1)
    pyautogui.write(app_name, interval=0.05)
    time.sleep(1)
    pyautogui.press("enter")
    time.sleep(7)
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
def translate(text: str, target_lang: str, source_lang: str = "auto") -> str:
    """Translate text to any language. e.g. translate('hello', 'spanish')"""
    return run_translate(text, target_lang, source_lang)


@tool
def wether_app(city: str) -> str:
    """To find weather details and condition in specific city's, Such as how is the weather today in x city"""
    return get_weather(city)


@tool
def set_timer(seconds: int = 0, minutes: int = 0, label: str = "Timer") -> str:
    """Set a countdown timer in minutes or seconds."""
    return run_timer(seconds, minutes, label)


@tool
def set_alarm(alarm_time: str, label: str = "Alarm") -> str:
    """Set an alarm at a specific time. Format HH:MM e.g. 07:30"""
    return run_alarm(alarm_time, label)


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
