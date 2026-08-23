import time
import pyautogui
from langchain_core.tools import tool
from Actions.execute_action import open_app
from Actions.app_shortcut import SHORTCUTS


@tool
def discord_send_message(target_name: str, message: str) -> str:
    """Send a Discord message to a user or channel by name using the quick switcher."""
    open_app.invoke({"app_name": "Discord"})

    pyautogui.hotkey(*SHORTCUTS["discord"]["switch"])
    time.sleep(0.5)
    pyautogui.typewrite(target_name, interval=0.02)
    time.sleep(0.5)
    pyautogui.press("enter")
    time.sleep(1)

    pyautogui.typewrite(message, interval=0.02)
    pyautogui.press("enter")
    return f"Sent to {target_name}: {message}"


@tool
def discord_toggle_mute() -> str:
    """Toggle Discord microphone mute."""
    pyautogui.hotkey(*SHORTCUTS["discord"]["mute"])
    return "Toggled mute"


@tool
def discord_toggle_deafen() -> str:
    """Toggle Discord deafen (mute audio output)."""
    pyautogui.hotkey(*SHORTCUTS["discord"]["deafen"])
    return "Toggled deafen"


@tool
def discord_answer_call() -> str:
    """Answer an incoming Discord call."""
    pyautogui.hotkey(*SHORTCUTS["discord"]["answer_call"])
    return "Answered call"


@tool
def discord_decline_call() -> str:
    """Decline an incoming Discord call."""
    pyautogui.hotkey(*SHORTCUTS["discord"]["decline_call"])
    return "Declined call"
