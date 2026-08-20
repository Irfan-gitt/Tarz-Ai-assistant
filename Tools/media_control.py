# Actions/media_control.py
import pyautogui
from langchain_core.tools import tool


def _media_play_pause():
    pyautogui.press("playpause")


def _media_next():
    pyautogui.press("nexttrack")


def _media_previous():
    pyautogui.press("prevtrack")


@tool
def media_play_pause() -> str:
    """Toggle play/pause on whatever's currently playing (Spotify, YouTube, VLC, anything) — works no matter which app has audio focus."""
    _media_play_pause()
    return "Toggled play/pause"


@tool
def media_next_track() -> str:
    """Skip to the next track in whatever media app is currently playing."""
    _media_next()
    return "Skipped to next track"


@tool
def media_previous_track() -> str:
    """Go back to the previous track."""
    _media_previous()
    return "Went to previous track"
