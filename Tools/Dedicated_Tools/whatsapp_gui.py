import time
import os
import pyautogui
from PIL import Image
from dotenv import load_dotenv
from langchain_core.tools import tool
import moondream as md
from Actions.execute_action import open_app
from Actions.app_shortcut import SHORTCUTS

load_dotenv()


def _click_at(target: str) -> dict:
    pyautogui.screenshot().save("grid_screen.png")
    image = Image.open("grid_screen.png")
    model = md.vl(api_key=os.getenv("MOONDREAM_API_KEY"))
    result = model.point(image, target)
    if not result.get("points"):
        print(f"'{target}' not found")
        return {"found": False}
    point = result["points"][0]
    w, h = image.size
    x, y = point["x"], point["y"]
    px, py = (int(x * w), int(y * h)
              ) if x <= 1 and y <= 1 else (int(x), int(y))
    pyautogui.moveTo(px, py, duration=0.3)
    pyautogui.click()
    print(f"'{target}' clicked at ({px}, {py})")
    return {"found": True, "x": px, "y": py}


def _find_with_retry(target: str, attempts: int = 3) -> dict:
    for i in range(attempts):
        result = _click_at(target)
        if result["found"]:
            return result
        print(
            f"[Retry] '{target}' attempt {i+1}/{attempts} failed, retrying...")
        time.sleep(1)
    return {"found": False}


@tool
def whatsapp_send_message(contact_name: str, message: str) -> str:
    """Send a WhatsApp message to a contact by name."""
    open_app.invoke({"app_name": "WhatsApp"}
                    )   # open_app already waits internally, no extra sleep needed

    pyautogui.hotkey(*SHORTCUTS["whatsapp"]["search"])
    time.sleep(0.3)
    pyautogui.typewrite(contact_name, interval=0.02)
    time.sleep(1)

    result = _find_with_retry(f"chat entry named {contact_name}")
    if not result["found"]:
        return f"Couldn't find a chat with '{contact_name}'"

    time.sleep(0.5)
    pyautogui.typewrite(message, interval=0.02)
    pyautogui.press("enter")
    return f"Sent to {contact_name}: {message}"
