import os
import time
import threading
import webview
import screeninfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "orb_state.txt")
WIDTH, HEIGHT = 160, 160


def _read_state() -> str:
    try:
        with open(STATE_FILE, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "hide"


def _watch_state(window):
    last = None
    while True:
        state = _read_state()
        if state != last:
            if state == "show":
                window.show()
            else:
                window.hide()
            last = state
        time.sleep(0.15)


def main():
    screen = screeninfo.get_monitors()[0]
    x = (screen.width - WIDTH) // 2
    y = 20  # top of screen, small margin

    window = webview.create_window(
        "TARZ Orb",
        os.path.join(BASE_DIR, "orb.html"),
        width=WIDTH,
        height=HEIGHT,
        x=x,
        y=y,
        frameless=True,
        easy_drag=False,
        on_top=True,
        transparent=True,
        resizable=False,
    )

    threading.Thread(target=_watch_state, args=(window,), daemon=True).start()
    webview.start()


if __name__ == "__main__":
    main()
