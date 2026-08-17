"""
Jarvis - Grid Overlay UI Finder
Draws a labeled grid over the screenshot, asks Gemini which cell contains
the target, then converts cell → exact pixel coordinates.
"""

from google.genai import types
import time
import os
import re
import pyautogui
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
from google import genai

load_dotenv()

pyautogui.FAILSAFE = True
# PyAutoGUI applies this pause after *every* call.  Keep a tiny safety gap, but
# don't add a full second to each vision-guided click (move + click).
pyautogui.PAUSE = 0.05


GEMINI_KEYS = [
    os.getenv("GEMINI_KEY_1"),
    os.getenv("GEMINI_KEY_2"),
    os.getenv("GEMINI_KEY_3"),

]
GEMINI_KEYS = [k for k in GEMINI_KEYS if k]  # remove empty

current_key_idx = 0

_clients = [genai.Client(api_key=k) for k in GEMINI_KEYS]


def get_gemini_client():
    return _clients[current_key_idx]


COLS = 24
ROWS = 17

GRID_COLOR = (255, 255, 0, 120)
LABEL_COLOR = (255, 255, 0, 220)
LABEL_BG = (0, 0, 0, 160)

# Sub-cell position offsets (fraction of cell size)
POSITION_OFFSET = {
    "top-left":      (-0.3, -0.3),
    "top-center":    (0.0, -0.3),
    "top-right":     (0.3, -0.3),
    "center-left":   (-0.3,  0.0),
    "center":        (0.0,  0.0),
    "center-right":  (0.3,  0.0),
    "bottom-left":   (-0.3,  0.3),
    "bottom-center": (0.0,  0.3),
    "bottom-right":  (0.3,  0.3),
}

_GRID_PROMPT = """\
Find '{target}' in this grid image.
Columns: letters (A,B,C...) left to right.
Rows: numbers (1,2,3...) top to bottom.
Each cell labeled in top-left corner e.g. A1, G4.

Reply ONLY in this format:
CELL: <label>
POSITION: <top-left/top-center/top-right/center-left/center/center-right/bottom-left/bottom-center/bottom-right>
CONFIDENCE: <high/medium/low>
ELEMENT_TYPE: <button/text/icon/input/link/other>

If not visible:
NOT_FOUND
REASON: <brief reason>"""

# A grid lookup is a classification task, not a reasoning task.  Gemini 2.5
# Flash otherwise may spend time generating hidden reasoning before returning
# the four short lines that this parser needs.
_FAST_GRID_CONFIG = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_budget=0),
    max_output_tokens=64,
    temperature=0,
)


def take_screenshot() -> Image.Image:
    shot = pyautogui.screenshot()
    shot.save("temp/screen.png")
    return shot


def draw_grid(image: Image.Image, cols: int = COLS, rows: int = ROWS) -> Image.Image:
    image = image.resize((1280, 720))

    img = image.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    w, h = img.size
    cell_w = w / cols
    cell_h = h / rows

    try:
        font = ImageFont.truetype(
            "arial.ttf", size=int(min(cell_w, cell_h) * 0.28))
    except Exception:
        font = ImageFont.load_default()

    for c in range(cols + 1):
        x = int(c * cell_w)
        draw.line([(x, 0), (x, h)], fill=GRID_COLOR, width=1)

    for r in range(rows + 1):
        y = int(r * cell_h)
        draw.line([(0, y), (w, y)], fill=GRID_COLOR, width=1)

    for r in range(rows):
        for c in range(cols):
            col_letter = chr(ord('A') + c)
            label = f"{col_letter}{r + 1}"
            cx = int(c * cell_w + cell_w * 0.1)
            cy = int(r * cell_h + cell_h * 0.08)
            bbox = draw.textbbox((cx, cy), label, font=font)
            pad = 2
            draw.rectangle([bbox[0]-pad, bbox[1]-pad, bbox[2] +
                           pad, bbox[3]+pad], fill=LABEL_BG)
            draw.text((cx, cy), label, fill=LABEL_COLOR, font=font)

    result = Image.alpha_composite(img, overlay).convert("RGB")
    result.save("temp/screen_grid.png")
    return result


def cell_to_pixels(
    cell: str,
    position: str = "center",
    cols: int = COLS,
    rows: int = ROWS
) -> tuple[int, int] | None:

    cell = cell.strip().upper()
    match = re.fullmatch(r"([A-Z]+)(\d+)", cell)
    if not match:
        return None

    col_idx = 0
    for ch in match.group(1):
        col_idx = col_idx * 26 + (ord(ch) - ord('A'))
    row_idx = int(match.group(2)) - 1

    if col_idx >= cols or row_idx >= rows:
        return None

    screen_w, screen_h = pyautogui.size()

    scale_x = screen_w / 1280
    scale_y = screen_h / 720

    cell_w = 1280 / cols
    cell_h = 720 / rows

    cx = col_idx * cell_w + cell_w / 2
    cy = row_idx * cell_h + cell_h / 2

    ox, oy = POSITION_OFFSET.get(position.lower().strip(), (0.0, 0.0))
    cx += ox * cell_w
    cy += oy * cell_h

    px = int(cx * scale_x)
    py = int(cy * scale_y)

    return px, py


def grid_find(target: str) -> dict:
    global current_key_idx
    print(f"\n[GridFinder] Looking for: '{target}'")

    t0 = time.time()
    image = take_screenshot()
    t1 = time.time()

    grid_image = draw_grid(image, COLS, ROWS)
    t2 = time.time()
    print(f"[TIMING] screenshot: {t1-t0:.2f}s | draw_grid: {t2-t1:.2f}s")

    response = None
    for attempt in range(len(GEMINI_KEYS)):
        try:
            client = get_gemini_client()
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[_GRID_PROMPT.format(target=target), grid_image],
                config=_FAST_GRID_CONFIG,
            )
            break
        except Exception as e:
            print(f"[GridFinder] Attempt {attempt+1} failed: {e}")
            if "429" in str(e) or "quota" in str(e).lower():
                current_key_idx = (current_key_idx + 1) % len(GEMINI_KEYS)
                continue
            return {"found": False}

    if response is None:
        print("[GridFinder] All keys exhausted")
        return {"found": False}

    t3 = time.time()
    print(f"[TIMING] gemini call: {t3-t2:.2f}s | total: {t3-t0:.2f}s")

    result = response.text.strip()
    print(f"[Gemini] {result}")

    if "NOT_FOUND" in result:
        print("[GridFinder] Target not found retry.")
        reason_match = re.search(r"REASON:\s*(.+)", result)
        return {"found": False, "reason": reason_match.group(1) if reason_match else "unknown"}

    cell_match = re.search(r"CELL:\s*([A-Za-z]+\d+)", result)
    pos_match = re.search(r"POSITION:\s*(.+)",        result)
    conf_match = re.search(r"CONFIDENCE:\s*(\w+)",     result)
    type_match = re.search(r"ELEMENT_TYPE:\s*(.+)",    result)

    if not cell_match:
        print("[GridFinder] Could not parse cell.")
        return {"found": False}

    cell = cell_match.group(1).upper()
    position = pos_match.group(1).strip().lower() if pos_match else "center"
    coords = cell_to_pixels(cell, position=position)

    if not coords:
        print(f"[GridFinder] Invalid cell '{cell}'")
        return {"found": False}

    px, py = coords
    print(
        f"[GridFinder] ✓ {cell} ({position}) → ({px}, {py}) | {conf_match.group(1) if conf_match else '?'}")

    return {
        "found":        True,
        "x":            px,
        "y":            py,
        "cell":         cell,
        "position":     position,
        "confidence":   conf_match.group(1) if conf_match else "unknown",
        "element_type": type_match.group(1).strip() if type_match else "unknown",
        "method":       "grid_vision"
    }


def click(target: str) -> dict:
    """Find and click a UI element. Returns dict with found/x/y."""
    result = grid_find(target)
    if not result["found"]:
        return {"found": False}

    x, y = result["x"], result["y"]
    screen_w, screen_h = pyautogui.size()
    if not (0 < x < screen_w and 0 < y < screen_h):
        return {"found": False}

    # Supplying coordinates directly avoids a separate moveTo() call and its
    # global PyAutoGUI pause.
    pyautogui.click(x, y)
    print(f"[GridFinder] ✓ Clicked at ({x}, {y})")
    return {"found": True, "x": x, "y": y}


if __name__ == "__main__":
    result = grid_find("play button")
    print(result)

    # click("search button")
    click("play button")
