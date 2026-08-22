import time
import pyautogui
from langchain_core.tools import tool
from Actions.execute_action import open_app
from Actions.app_shortcut import SHORTCUTS


@tool
def browser_open_url(query_or_url: str, browser: str = "brave") -> str:
    """Open a website or search query in the browser (brave or chrome)."""
    browser = browser.lower()
    open_app.invoke({"app_name": browser})

    pyautogui.hotkey(*SHORTCUTS[browser]["new_tab"])
    time.sleep(0.3)
    pyautogui.typewrite(query_or_url, interval=0.02)
    pyautogui.press("enter")
    time.sleep(2)
    return f"Opened '{query_or_url}' in {browser}"
