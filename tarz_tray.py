import os
import subprocess
import threading
import pystray
from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_gui_proc = None          # track the running GUI process
_gui_lock = threading.Lock()


def open_gui():
    global _gui_proc
    with _gui_lock:
        # If already running — don't open another
        if _gui_proc is not None and _gui_proc.poll() is None:
            print("[Tray] GUI already open, skipping")
            return
        print("[Tray] Opening GUI...")
        _gui_proc = subprocess.Popen(
            ["venv\\Scripts\\python.exe", "Main\\ui.py"],
            cwd=BASE_DIR,
        )


def launch_gui(icon=None, item=None):
    threading.Thread(target=open_gui, daemon=True).start()


def make_icon():
    image = Image.new("RGB", (64, 64), "black")
    draw = ImageDraw.Draw(image)
    draw.ellipse([8, 8, 56, 56], fill="cyan")
    return image


def quit_tarz(icon, item):
    global _gui_proc
    if _gui_proc and _gui_proc.poll() is None:
        _gui_proc.terminate()
    icon.stop()


def main():
    # Open GUI once on startup
    launch_gui()

    icon = pystray.Icon(
        "TARZ",
        make_icon(),
        "TARZ AI Assistant",
        menu=pystray.Menu(
            pystray.MenuItem("Open TARZ", launch_gui),
            pystray.MenuItem("Quit", quit_tarz),
        ),
    )
    icon.run()


if __name__ == "__main__":
    main()
