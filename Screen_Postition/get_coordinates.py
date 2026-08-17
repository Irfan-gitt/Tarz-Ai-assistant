from Screen_Postition.grid_finder import click as grid_click
from dotenv import load_dotenv
import easyocr
import pyautogui
import warnings

warnings.filterwarnings(
    "ignore",
    message="'pin_memory' argument is set as true but no accelerator is found.*",
    category=UserWarning,
)


load_dotenv()

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.5


reader = easyocr.Reader(['en'], gpu=False)


def take_screenshot():
    pyautogui.screenshot().save("temp/screen.png")


def easyocr_find(target):
    try:
        results = reader.readtext("temp/screen.png")
        best = None
        best_conf = 0

        for (bbox, text, conf) in results:
            if conf < 0.5:
                continue
            if target.lower() in text.lower():
                if conf > best_conf:
                    x = int((bbox[0][0] + bbox[2][0]) / 2)
                    y = int((bbox[0][1] + bbox[2][1]) / 2)
                    best = {"found": True, "x": x, "y": y, "text": text}
                    best_conf = conf

        return best if best else {"found": False}

    except Exception as e:
        print(f"[OCR] Error: {e}")
        return {"found": False}


def find_on_screen(target):
    # grid_click captures the screenshot itself.  Taking one here only adds
    # latency and used to make every returned dictionary look like success.
    result = grid_click(target)
    if result.get("found"):
        return result

    print(f"'{target}' not found on screen")
    return {"found": False}
