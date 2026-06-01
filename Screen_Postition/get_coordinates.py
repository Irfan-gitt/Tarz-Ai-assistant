import pyautogui
import easyocr
from dotenv import load_dotenv
from Screen_Postition.grid_finder import click as grid_click

load_dotenv()

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.5

try:
    reader = easyocr.Reader(['en'], gpu=True)
except Exception:
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
    take_screenshot()

    print(f"Searching for '{target}' with OCR...")
    result = easyocr_find(target)

    if result["found"]:
        print(f"Found with OCR: {result}")
        pyautogui.moveTo(result["x"], result["y"], duration=0.3)
        pyautogui.click()
        return result

    print(f"OCR failed, trying grid vision...")
    success = grid_click(target)

    if success:
        return {"found": True}

    print(f"'{target}' not found on screen")
    return {"found": False}
