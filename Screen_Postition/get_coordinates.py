import time
import moondream as md
from PIL import Image
import os
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


def easyocr_find(target):  # not using anymore
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

    result = grid_click(target)
    if result.get("found"):
        return result

    print(f"'{target}' not found on screen")
    return {"found": False}


load_dotenv()


def find_and_click(target):
    pyautogui.screenshot().save("grid_screen.png")
    image = Image.open("grid_screen.png")

    model = md.vl(api_key=os.getenv("MOONDREAM_API_KEY"))

    target = target
    result = model.point(image, target)

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
    time.sleep(0.2)
    pyautogui.click()

    return {"found": False}
