import time
import pyautogui

SHORTCUTS = {
    "brave": {
        "search":    ["ctrl", "l"],
        "new_tab":   ["ctrl", "t"],
        "close_tab": ["ctrl", "w"]
    },
    "file explorer": {
        "search": ["ctrl", "f"]
    },
    "youtube": {
        "search": ["/"]
    },
    "spotify": {
        "search":      ["ctrl", "l"],
        "volume_up":   ["ctrl", "up"],
        "volume_down": ["ctrl", "down"],
        "next":        ["ctrl", "right"],
        "previous":    ["ctrl", "left"],
        "play_pause":  ["space"]
    },
    "chrome": {
        "search":    ["ctrl", "l"],
        "new_tab":   ["ctrl", "t"],
        "close_tab": ["ctrl", "w"]
    },
    "whatsapp": {
        "search":   ["ctrl", "f"],
        "new_chat": ["ctrl", "n"]
    }
}


def volume_up():
    pyautogui.press("volumeup")
    return "Volume up"


def volume_down():
    pyautogui.press("volumedown")
    return "Volume down"


def mute():
    pyautogui.press("volumemute")
    return "Muted"
