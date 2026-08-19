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


def _click_at(cl_target):

    # Take a screenshot to test against
    pyautogui.screenshot().save("grid_screen.png")
    image = Image.open("grid_screen.png")

    model = md.vl(api_key=os.getenv("MOONDREAM_API_KEY"))

    target = cl_target
    result = model.point(image, target)

    # Expect something like: {"points": [{"x": 0.62, "y": 0.41}]}

    if result.get("points"):
        point = result["points"][0]
        w, h = image.size
        px = int(point["x"] * w)   # if normalized 0-1, convert to real pixels
        py = int(point["y"] * h)
        print(f"'{target}' found at pixel ({px}, {py}) on a {w}x{h} screenshot")
    else:
        print(f"'{target}' not found")

    x = point["x"]
    y = point["y"]

    # handle both cases — some APIs give 0-1 normalized, some give absolute pixels
    if x <= 1 and y <= 1:
        px, py = int(x * w), int(y * h)
    else:
        px, py = int(x), int(y)

    pyautogui.moveTo(px, py, duration=0.3)
    pyautogui.click()
    return {"found": False}


def _spotify_search(query: str):
    """Shared boilerplate only — not a tool, LLM never sees or calls this directly."""
    open_app.invoke({"app_name": "Spotify"}
                    )   # .invoke(), not open_app(...) directly
    time.sleep(3)
    pyautogui.hotkey("ctrl", "l")
    time.sleep(0.3)
    pyautogui.typewrite(query, interval=0.02)
    pyautogui.press("enter")
    time.sleep(1.2)


def _find_with_retry():
    for i in range(3):
        result = _click_at()
        if result["found"]:
            return result
        print(
            f"[Retry] '{3}' attempt {i+1}/{0} failed, retrying...")
        time.sleep(1)
    return {"found": False}


@tool
def spotify_play_song(song_name: str) -> str:
    """Play a specific song on Spotify by name (optionally include artist)."""
    _spotify_search(song_name)
    result = _find_with_retry()
    if not result["found"]:
        return f"Searched for '{song_name}' but couldn't find the play button"
    _click_at("green play button")
    return f"Playing '{song_name}' on Spotify"


@tool
def spotify_play_playlist(playlist_name: str) -> str:
    """Play one of the user's own playlists by name, e.g. 'Liked Songs' or 'Workout Mix'."""
    open_app.invoke({"app_name": "Spotify"}
                    )   # .invoke(), not open_app(...) directly

    time.sleep(1)

    _click_at(
        f"playlist card named {playlist_name}")

    time.sleep(2)

    _click_at(
        f"green play button")

    return f"Playing playlist '{playlist_name}' on Spotify"
