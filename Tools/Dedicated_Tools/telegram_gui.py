import time
import pyautogui
from langchain_core.tools import tool
from Actions.execute_action import open_app


@tool
def telegram_message_user(user, query: str) -> str:
    """Opens Telegram and sends a message to the specified user."""
    open_app.invoke({"app_name": "Telegram"})
    time.sleep(3)
    pyautogui.typewrite(user, interval=0.02)
    time.sleep(0.5)
    pyautogui.press("enter")
    time.sleep(0.5)
    pyautogui.typewrite(query, interval=0.02)

    time.sleep(0.5)
    pyautogui.press("enter")
