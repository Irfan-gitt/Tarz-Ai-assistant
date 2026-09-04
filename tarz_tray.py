import threading
import pystray
from PIL import Image, ImageDraw
from Main.tarz import main as tarz_main


def make_icon():
    image = Image.new("RGB", (64, 64), "black")
    draw = ImageDraw.Draw(image)
    draw.ellipse([8, 8, 56, 56], fill="cyan")
    return image


def quit_tarz(icon, item):
    icon.stop()


def run_tray():
    icon = pystray.Icon(
        "TARZ",
        make_icon(),
        "TARZ AI Assistant",
        menu=pystray.Menu(
            pystray.MenuItem("Quit", quit_tarz),
        ),
    )

    icon.run()


if __name__ == "__main__":
    # Tray runs in background
    tray_thread = threading.Thread(
        target=run_tray,
        daemon=True,
    )
    tray_thread.start()

    # TARZ runs on the main thread
    tarz_main()
