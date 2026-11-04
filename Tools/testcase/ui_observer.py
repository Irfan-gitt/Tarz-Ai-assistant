"""
expose.py - universal "expose the screen" layer for Windows automation.

    open_app("notepad")
    wait()
    data = expose()          # {"source": "uia" | "vision", "window": str, "elements": [El, ...]}
    for e in data["elements"]:
        print(e.id, e.role, e.name, e.x, e.y)
    move_to(e.x, e.y)        # human-like cursor movement (from Tools.web_agent)

Flow of expose():
  1. try Windows UI Automation (works for native / WinUI / Win32 / most browsers)
  2. if too few elements come back (canvas apps, Electron, games, remote desktops ...)
     fall back to Moondream vision: screenshot the front window, ask it which controls it sees,
     then ask it to POINT at each one. Returns the same El objects, so callers don't care.

Vision setup (pick one):
    pip install moondream pillow
    set MOONDREAM_API_KEY=...            -> cloud
    set MOONDREAM_ENDPOINT=http://localhost:2020/v1   -> local Moondream Station
"""

from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))  # noqa

import re
import time
from typing import Optional

import pyautogui

# also sets DPI awareness + project paths; move_to = human-like cursor movement
from Tools.web_agent import El, move_to

pyautogui.FAILSAFE = True

# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
OPEN_WAIT = 2.0
SETTLE = 2.0
POLL = 0.25
OBSERVE_BUDGET = 2.5      # max seconds spent reading one window's element tree
MIN_UIA_ELEMENTS = 4      # fewer usable elements than this -> treat UIA as failed
MAX_VISION_TARGETS = 15   # how many controls vision is asked to locate

ROLES = {"Button", "CheckBox", "ComboBox", "DataItem", "Document", "Edit", "Hyperlink", "Image",
         "ListItem", "MenuItem", "RadioButton", "Slider", "Spinner", "SplitButton", "TabItem",
         "Text", "TreeItem"}
TEXT_ROLES = ("Edit", "ComboBox", "Document")


# --------------------------------------------------------------------------
# Layer step 1/2: open_app() + wait()
# --------------------------------------------------------------------------
def open_app(name: str) -> None:
    """Start menu search: Win, type the name, Enter."""
    pyautogui.press("win")
    time.sleep(1.0)
    pyautogui.write(name, interval=0.03)
    time.sleep(1.0)
    pyautogui.press("enter")
    time.sleep(OPEN_WAIT)


def _front_sig():
    import win32gui
    h = win32gui.GetForegroundWindow()
    return h, (win32gui.GetWindowText(h) if h else "")


def wait(min_wait: float = 0.5) -> None:
    """Wait the minimum, then only while the front window handle/title still change."""
    time.sleep(min_wait)
    end, last = time.time() + max(SETTLE - min_wait, 0), _front_sig()
    while time.time() < end:
        time.sleep(POLL)
        cur = _front_sig()
        if cur == last:
            return
        last = cur


# --------------------------------------------------------------------------
# Layer step 3: try_to_expose_data()  - UI Automation
# --------------------------------------------------------------------------
def _front_window():
    import win32gui
    from pywinauto import Desktop
    h = win32gui.GetForegroundWindow()
    if not h:
        return None
    win = Desktop(backend="uia").window(handle=h)
    wr = win.rectangle()
    sw, sh = pyautogui.size()
    vp = (max(wr.left, 0), max(wr.top, 0), min(
        wr.right, sw), min(wr.bottom, sh))
    return win, vp


def try_to_expose_data():
    """-> (window title, viewport (l,t,r,b) or None, [El]) with ALL readable elements.
    Stops after OBSERVE_BUDGET seconds so a huge tree cannot freeze the agent."""
    fw = _front_window()
    if not fw:
        return "", None, []
    win, vp = fw
    title = win.window_text()
    vis, below, above = [], [], []
    t0 = time.time()
    for info in win.element_info.descendants()[:3000]:
        if time.time() - t0 > OBSERVE_BUDGET:
            break
        try:
            role = info.control_type
            if role not in ROLES:
                continue
            name = (info.name or "").strip()
            if not name and role not in TEXT_ROLES:
                continue
            r = info.rectangle
            if r.right - r.left < 3 or r.bottom - r.top < 3:
                continue
            cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
            if not vp[0] <= cx <= vp[2]:
                continue
            if cy > vp[3]:
                below.append(El(role, name[:80], cx, cy, "below"))
            elif cy < vp[1]:
                above.append(El(role, name[:80], cx, cy, "above"))
            elif info.visible:
                vis.append(El(role, name[:80], cx, cy, "visible"))
        except Exception:
            continue                             # element vanished mid-read
    below.sort(key=lambda e: e.y)
    above.sort(key=lambda e: -e.y)
    els = vis + below + above
    for i, e in enumerate(els):
        e.id = i
    return title, vp, els


# --------------------------------------------------------------------------
# Layer step 4: fallback to Moondream vision
# --------------------------------------------------------------------------
_md_model = None


def _moondream():
    global _md_model
    if _md_model is None:
        import moondream as md
        endpoint = os.environ.get("MOONDREAM_ENDPOINT")
        _md_model = (md.vl(endpoint=endpoint) if endpoint
                     else md.vl(api_key=os.environ["MOONDREAM_API_KEY"]))
    return _md_model


def vision_expose(vp, targets: Optional[list[str]] = None) -> list[El]:
    """Screenshot the front window, let Moondream name the controls (or use `targets`),
    then POINT at each one. Returns El objects with real screen coordinates."""
    from PIL import Image
    l, t, r, b = vp or (0, 0, *pyautogui.size())
    shot = pyautogui.screenshot(region=(l, t, r - l, b - t))
    img: Image.Image = shot
    model = _moondream()

    if not targets:
        ans = model.query(img, "List the names of all clickable buttons, tabs, menu items, "
                               "text fields and links visible. Comma separated, short names only.")["answer"]
        targets = [x.strip() for x in re.split(r"[,\n]", ans)
                   if x.strip()][:MAX_VISION_TARGETS]

    els: list[El] = []
    for name in targets:
        try:
            pts = model.point(img, name).get("points", [])
        except Exception:
            continue
        if not pts:
            continue
        # moondream returns coords normalised 0..1 inside the screenshot
        x = int(l + pts[0]["x"] * (r - l))
        y = int(t + pts[0]["y"] * (b - t))
        els.append(El("Vision", name[:80], x, y, "visible"))
    for i, e in enumerate(els):
        e.id = i
    return els


# --------------------------------------------------------------------------
# The layer: expose()
# --------------------------------------------------------------------------
def expose(targets: Optional[list[str]] = None, force_vision: bool = False) -> dict:
    """UIA first; if it exposes too little (canvas / Electron / etc.) use vision.
    Returns {"source", "window", "viewport", "elements"}; elements carry name + x,y."""
    title, vp, els = ("", None, [])
    if not force_vision:
        try:
            title, vp, els = try_to_expose_data()
        except Exception as e:
            print(f"[expose] UIA failed: {e}")
    named = [e for e in els if e.name]
    if len(named) >= MIN_UIA_ELEMENTS and not force_vision:
        return {"source": "uia", "window": title, "viewport": vp, "elements": els}

    print(
        f"[expose] UIA gave {len(named)} elements -> falling back to Moondream vision")
    if vp is None:
        fw = _front_window()
        vp = fw[1] if fw else None
        title = title or (fw[0].window_text() if fw else "")
    return {"source": "vision", "window": title, "viewport": vp,
            "elements": vision_expose(vp, targets)}


if __name__ == "__main__":
    app = sys.argv[1] if len(sys.argv) > 1 else ""
    if app:
        open_app(app)
    wait()
    data = expose()
    print(f"source={data['source']} window={data['window']!r}")
    for e in data["elements"]:
        print(f'[{e.id}] {e.role}: "{e.name}" @({e.x},{e.y}) {e.where}')

# final
