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
from dotenv import load_dotenv
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
load_dotenv()  # Load environment variables from .env file if present


def _moondream():
    global _md_model
    if _md_model is None:
        import moondream as md
        endpoint = os.environ.get("MOONDREAM_ENDPOINT")
        _md_model = (md.vl(endpoint=endpoint) if endpoint
                     else md.vl(api_key=os.getenv("MOONDREAM_API_KEY")))
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
# Reading helpers (observe tools for the agent)
# --------------------------------------------------------------------------
READ_ROLES = {"Text", "ListItem", "DataItem", "Document"}


def read_text(filter_text: str = "", limit: int = 1500) -> str:
    """Text content of the front window top-to-bottom (latest chat messages come last)."""
    fw = _front_window()
    if not fw:
        return ""
    win, vp = fw
    rows, t0 = [], time.time()
    for info in win.element_info.descendants()[:3000]:
        if time.time() - t0 > OBSERVE_BUDGET:
            break
        try:
            if info.control_type not in READ_ROLES:
                continue
            name = (info.name or "").strip()
            if not name:
                continue
            r = info.rectangle
            cy, cx = (r.top + r.bottom) // 2, (r.left + r.right) // 2
            if vp[0] <= cx <= vp[2] and vp[1] <= cy <= vp[3] and info.visible:
                rows.append((cy, name))
        except Exception:
            continue
    rows.sort()
    seen, lines = set(), []
    for _, name in rows:
        if name not in seen and (not filter_text or filter_text.lower() in name.lower()):
            seen.add(name)
            lines.append(name)
    return " | ".join(lines)[-limit:]


def vision_query(question: str) -> str:
    """Ask Moondream a free-form question about the front window (works on canvas/Electron)."""
    fw = _front_window()
    l, t, r, b = fw[1] if fw else (0, 0, *pyautogui.size())
    img = pyautogui.screenshot(region=(l, t, r - l, b - t))
    return _moondream().query(img, question)["answer"]


# --------------------------------------------------------------------------
# Compaction: cut duplicates + decoration, keep everything the agent can act on
# --------------------------------------------------------------------------
MAX_VISIBLE, MAX_BELOW, MAX_ABOVE = 60, 8, 5
STRONG = {"Button", "CheckBox", "ComboBox", "Edit", "MenuItem", "RadioButton",
          "Slider", "Spinner", "SplitButton", "TabItem"}
# when several roles sit on the same spot with the same name, keep the most useful one
RANK = {**{r: 0 for r in STRONG}, "Hyperlink": 1, "ListItem": 2, "TreeItem": 2,
        "DataItem": 3, "Document": 4, "Text": 5, "Image": 6}
# row wrappers that repeat their children's text
CONTAINERS = {"DataItem", "ListItem"}
BULLETS = {"•", "·", "|", "-", "—", "/"}
ROW_Y = 30                                       # "same row" tolerance in pixels
# Text elements that are really controls (Spotify exposes sliders this way) or live values
CONTROL_TEXT = re.compile(
    r"^(change|set|adjust|seek|toggle|select|search|enter)\b", re.I)


def compact(els: list[El], task: str = "", notes: str = "", title: str = "") -> list[El]:
    """Typical result: ~670 raw elements -> ~70. Nothing clickable/typeable is dropped except exact
    duplicates (same name, same spot) and row wrappers whose text is already covered by a button/link
    on the same row."""
    words = set(re.findall(r"\w{3,}", (task + " " + notes).lower()))

    def hit(e: El) -> bool:
        return any(w in e.name.lower() for w in words)

    t = title.lower().strip()

    # 1) empty / bullet / whole-window-document noise
    keep = []
    for e in els:
        n = e.name.strip()
        if not n and e.role not in TEXT_ROLES:
            continue
        if n in BULLETS:
            continue
        if e.role == "Document" and t and n.lower() == t:
            continue
        keep.append(e)

    # 2) same name on the same spot (DataItem+Button, Hyperlink+CheckBox, Image+Button ...)
    best: dict = {}
    for e in keep:
        k = (e.name.lower(), e.x // 8, e.y // 8, e.where)
        cur = best.get(k)
        if cur is None or RANK.get(e.role, 9) < RANK.get(cur.role, 9):
            best[k] = e
    keep = list(best.values())

    # 3) row wrappers: "Song More options for X Add to playlist" / "Gigi Perez" next to its link.
    #    Only dropped when a button/link on the SAME ROW already carries that text, so a File Explorer
    #    list item is never lost to a breadcrumb button of the same name.
    strong = [(e.name.lower(), e.y) for e in keep
              if (e.role in STRONG or e.role == "Hyperlink") and e.name]
    pats = {sn: re.compile(r"\b" + re.escape(sn) + r"\b")
            for sn, _ in strong if len(sn) >= 4 and " " in sn}

    def covered(e: El) -> bool:
        low = e.name.lower()
        for sn, sy in strong:
            if abs(sy - e.y) > ROW_Y:
                continue
            if low == sn or (sn in pats and pats[sn].search(low) and low != sn):
                return True
        return False

    keep = [e for e in keep if not (e.role in CONTAINERS and covered(e))]

    # 4) decoration: images, and text that is neither a task match, a value nor a hidden control
    def useful_text(e: El) -> bool:
        n = e.name
        return (hit(e) or bool(CONTROL_TEXT.match(n))
                or (len(n) <= 30 and bool(re.search(r"\d", n))))

    keep = [e for e in keep
            if not (e.role == "Image" and not hit(e))
            and not (e.role == "Text" and not useful_text(e))]

    # 5) order (task matches first, then reading order) and cap each zone
    zones = {"visible": [], "below": [], "above": []}
    for e in keep:
        zones[e.where].append(e)
    zones["visible"].sort(key=lambda e: (not hit(e), e.y, e.x))
    zones["below"].sort(key=lambda e: (not hit(e), e.y))
    zones["above"].sort(key=lambda e: (not hit(e), -e.y))
    caps = {"visible": MAX_VISIBLE, "below": MAX_BELOW, "above": MAX_ABOVE}
    out: list[El] = []
    for z in ("visible", "below", "above"):
        out += zones[z][:caps[z]]
    for i, e in enumerate(out):
        e.id = i
    return out


# --------------------------------------------------------------------------
# The layer: expose()
# --------------------------------------------------------------------------
def expose(targets: Optional[list[str]] = None, force_vision: bool = False,
           task: str = "", notes: str = "", compact_els: bool = True) -> dict:
    """UIA first; if it exposes too little (canvas / Electron / etc.) use vision.
    Returns {"source", "window", "viewport", "elements", "raw_count"}; elements carry name + x,y.
    Pass task/notes so elements matching the task words are ranked first."""
    title, vp, els = ("", None, [])
    if not force_vision:
        try:
            title, vp, els = try_to_expose_data()
        except Exception as e:
            print(f"[expose] UIA failed: {e}")
    named = [e for e in els if e.name]
    if len(named) >= MIN_UIA_ELEMENTS and not force_vision:
        out = compact(els, task, notes, title) if compact_els else els
        print(f"[expose] UIA {len(els)} raw -> {len(out)} sent")
        return {"source": "uia", "window": title, "viewport": vp,
                "elements": out, "raw_count": len(els)}

    print(
        f"[expose] UIA gave {len(named)} elements -> falling back to Moondream vision")
    if vp is None:
        fw = _front_window()
        vp = fw[1] if fw else None
        title = title or (fw[0].window_text() if fw else "")
    v = vision_expose(vp, targets)
    return {"source": "vision", "window": title, "viewport": vp,
            "elements": v, "raw_count": len(v)}


if __name__ == "__main__":
    app = sys.argv[1] if len(sys.argv) > 1 else "spotify"
    if app:
        open_app(app)
    wait()
    data = expose()
    print(f"source={data['source']} window={data['window']!r}")
    for e in data["elements"]:
        print(f'[{e.id}] {e.role}: "{e.name}" @({e.x},{e.y}) {e.where}')
