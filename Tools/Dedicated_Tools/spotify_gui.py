# Tools/spotify_gui.py
import time
import pyautogui
from langchain_core.tools import tool
from Actions.execute_action import open_app

import time
import os
import pyautogui
from PIL import Image
from dotenv import load_dotenv
import moondream as md
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


def _spotify_search(query: str):
    open_app.invoke({"app_name": "Spotify"})
    time.sleep(6)
    pyautogui.press("escape")
    time.sleep(0.5)
    pyautogui.hotkey("ctrl", "l")
    time.sleep(0.3)
    pyautogui.typewrite(query, interval=0.02)
    pyautogui.press("enter")
    time.sleep(1.2)


@tool
def spotify_play_song(song_name: str) -> str:
    """Play a specific song on Spotify by name (optionally include artist)."""
    _spotify_search(song_name)
    result = _find_with_retry(
        "green play button ▶︎ ")
    if not result["found"]:
        return f"Searched for '{song_name}' but couldn't find the play button"
    return f"Playing '{song_name}' on Spotify"


@tool
def spotify_play_playlist(playlist_name: str) -> str:
    """Play one of the user's own playlists by name, e.g. 'Liked Songs' or 'Workout Mix'."""
    open_app.invoke({"app_name": "Spotify"})
    time.sleep(6)

    result = _find_with_retry(f"playlist card named {playlist_name}")
    if not result["found"]:
        return f"Couldn't find playlist '{playlist_name}'"

    time.sleep(1)
    play_result = _find_with_retry("green play button")
    if not play_result["found"]:
        return f"Found playlist '{playlist_name}' but couldn't find the play button"

    return f"Playing playlist '{playlist_name}' on Spotify"
