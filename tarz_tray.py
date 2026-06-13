
import keyboard
import threading
import time
import os
import importlib.util
import pystray
from PIL import Image, ImageDraw
from Audio.tts import speak
from Audio.stt import listen as stt_listen

print("[Tray] Loading TARZ module...")
spec = importlib.util.spec_from_file_location(
    "tarz", os.path.join(os.path.dirname(__file__), "Main", "tarz.py")
)
tarz_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tarz_module)
think = tarz_module.think
print("[Tray] TARZ loaded ✓")


def tarz_loop():
    speak("TARZ is ready. Press Control + SPACE to ")

    while True:

        print("\n[TARZ] Press Ctrl + SPACE to speak...")
        keyboard.wait("ctrl+space")

        print("[TARZ] Listening...")
        # input("You: ")
        user_input = stt_listen()

        if not user_input:
            speak("Didn't catch that, press space to try again.")
            continue

        print(f"You: {user_input}")
        result = think(user_input)
        print(f"TARZ: {result}")
        speak(result)

        # After task done → back to waiting
        print("\n[TARZ] Task complete. Press SPACE for next command.")


def make_icon():
    img = Image.new("RGB", (64, 64), "black")
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill="cyan")
    return img


def quit_tarz(icon, item):
    icon.stop()
    os.kill(os.getpid(), 9)


print("[Tray] Starting...")
threading.Thread(target=tarz_loop, daemon=True).start()

icon = pystray.Icon(
    "TARZ", make_icon(), "TARZ AI Assistant",
    menu=pystray.Menu(
        pystray.MenuItem("TARZ is running", lambda: None, enabled=False),
        pystray.MenuItem("Quit", quit_tarz)
    )
)

icon.run()
