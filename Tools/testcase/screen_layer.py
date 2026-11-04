"""
screen_layer.py - the EYES of the agent. No LLM in here, it only returns data.

    screen = Screen("chrome")                 # brave | chrome | edge | any exe / app name
    screen.open("https://youtube.com")        # open app, wait
    snap = screen.snapshot()                  # Windows UI Automation tree first;
                                              # Moondream fallback if the tree is empty
    print(snap.render())                      # [id] Role: "name" @(x,y) visible|below|above
    el = screen.locate("the play button")     # one-off Moondream lookup -> El(x, y)

Names + screen coordinates only. Clicking and typing live in llm_layer.py.
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

# real pixels, so UIA coordinates == screenshot == pyautogui coordinates (before pywinauto)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import pyautogui

OPEN_WAIT = 2.0                    # sleep after launching the app
# keep re-reading an empty tree this long before vision
TREE_WAIT = 4.0
MAX_VISIBLE, MAX_BELOW, MAX_ABOVE = 100, 30, 15
# max Moondream uses (snapshot fallback or locate) per Screen
VISION_BUDGET = 6
VISION_MAX_ITEMS = 8               # labels Moondream lists on a vision snapshot

ROLES = {"Button", "Hyperlink", "Edit", "ListItem", "MenuItem", "TabItem", "CheckBox",
         "RadioButton", "ComboBox", "SplitButton", "TreeItem", "Image", "Text"}
DIALOG_ROLES = {"Button", "SplitButton", "Edit", "ComboBox"}

_PF, _PF86, _LOCAL = (os.environ.get("ProgramFiles", r"C:\Program Files"),
                      os.environ.get("ProgramFiles(x86)",
                                     r"C:\Program Files (x86)"),
                      os.environ.get("LOCALAPPDATA", ""))

# browsers: name -> (possible exe paths, text in the window title)
APPS = {
    "brave": ([rf"{_PF}\BraveSoftware\Brave-Browser\Application\brave.exe",
               rf"{_PF86}\BraveSoftware\Brave-Browser\Application\brave.exe",
               rf"{_LOCAL}\BraveSoftware\Brave-Browser\Application\brave.exe"], "Brave"),
    "chrome": ([rf"{_PF}\Google\Chrome\Application\chrome.exe",
                rf"{_PF86}\Google\Chrome\Application\chrome.exe",
                rf"{_LOCAL}\Google\Chrome\Application\chrome.exe"], "Google Chrome"),
    "edge": ([rf"{_PF86}\Microsoft\Edge\Application\msedge.exe",
              rf"{_PF}\Microsoft\Edge\Application\msedge.exe"], "Edge"),
}

VISION_Q = ("List the clickable things on this screen: buttons, links, tabs, menu items, profile "
            f"cards, input fields. One short label per line, at most {VISION_MAX_ITEMS}, nothing else.")


def _add_project_paths() -> None:
    """Find the TARZ root (folder containing config_settings/) so the LLM gateway imports work."""
    d = os.path.abspath(os.path.dirname(__file__))
    while True:
        if os.path.isdir(os.path.join(d, "config_settings")):
            for p in (os.path.join(d, "config_settings"), d):
                if p not in sys.path:
                    sys.path.insert(0, p)
            return
        if os.path.dirname(d) == d:
            return
        d = os.path.dirname(d)


_add_project_paths()


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
@dataclass
class El:
    role: str
    name: str
    x: int
    y: int
    where: str = "visible"         # visible | below | above
    id: int = -1
    source: str = "uia"            # uia (exact) | vision (approximate)


@dataclass
class Snapshot:
    title: str
    # (left, top, right, bottom) of the page / window, or None
    viewport: Optional[tuple]
    elements: list
    note: str = ""

    def signature(self) -> int:
        """Changes when the screen changes (used to tell whether an action did anything)."""
        return hash((self.title, tuple((e.role, e.name) for e in self.elements[:40])))

    def render(self) -> str:
        return "\n".join(
            f'[{e.id}] {e.role}: "{e.name}" @({e.x},{e.y}) {e.where}' + (
                " (vision)" if e.source == "vision" else "")
            for e in self.elements) or "(no elements readable)"


# --------------------------------------------------------------------------
# Moondream (fallback). One gateway call = same pattern as find_and_click.
# --------------------------------------------------------------------------
def _moondream(call):
    import moondream as md
    from config_settings.config import LOCATE_MODELS
    from config_settings.llm_gateway import get_gateway
    return get_gateway().run(LOCATE_MODELS, lambda name, cfg, key: call(md.vl(api_key=key)))


def _px(p: dict, w: int, h: int) -> tuple[int, int]:
    # Moondream gives 0-1 normalized; accept pixels too
    x, y = p["x"], p["y"]
    return (int(x * w), int(y * h)) if x <= 1 and y <= 1 else (int(x), int(y))


def _read(root, roles: set, vp=None, limit: int = 3000) -> list[El]:
    """Walk a UIA element, keep named controls with their centre coordinates."""
    vis, below, above = [], [], []
    for info in root.descendants()[:limit]:
        try:
            role = info.control_type
            if role not in roles:
                continue
            name = (info.name or "").strip()
            if not name and role != "Edit":
                continue
            r = info.rectangle
            if r.right - r.left < 3 or r.bottom - r.top < 3:
                continue
            e = El(role, name[:80], (r.left + r.right) //
                   2, (r.top + r.bottom) // 2)
            if vp is not None:
                if not vp.left <= e.x <= vp.right:
                    continue
                e.where = "above" if e.y < vp.top else "below" if e.y > vp.bottom else "visible"
            {"above": above, "below": below}.get(e.where, vis).append(e)
        except Exception:
            continue                             # element vanished mid-read
    below.sort(key=lambda e: e.y)                # nearest first
    above.sort(key=lambda e: -e.y)
    els = vis[:MAX_VISIBLE] + below[:MAX_BELOW] + above[:MAX_ABOVE]
    for i, e in enumerate(els):
        e.id = i
    return els


# --------------------------------------------------------------------------
# Screen
# --------------------------------------------------------------------------
class Screen:
    def __init__(self, app: str = "brave", title_hint: str = ""):
        key = app.lower().strip()
        self.is_browser = key in APPS
        if self.is_browser:
            paths, self.mark = APPS[key]
            self.exe = next(
                (p for p in paths if p and os.path.exists(p)), None)
        else:                                    # any other app: exe path or a name Windows can start
            self.exe = app
            self.mark = title_hint or os.path.splitext(
                os.path.basename(app))[0]
        self.app = key
        self.vision_left = VISION_BUDGET

    # ---- windows ----
    def hwnd(self) -> Optional[int]:
        import win32gui
        found: list[int] = []

        def cb(h, _):
            t = win32gui.GetWindowText(h)
            if not (win32gui.IsWindowVisible(h) and t and self.mark.lower() in t.lower()):
                return
            if self.is_browser and win32gui.GetClassName(h) != "Chrome_WidgetWin_1":
                return
            found.append(h)                      # top of z-order first
        win32gui.EnumWindows(cb, None)
        return found[0] if found else None

    def dialog_hwnd(self) -> Optional[int]:
        """A Windows 'Save As' dialog (a browser asks where to save a download)."""
        import win32gui
        found: list[int] = []

        def cb(h, _):
            if (win32gui.IsWindowVisible(h) and win32gui.GetClassName(h) == "#32770"
                    and "save" in win32gui.GetWindowText(h).lower()):
                found.append(h)
        win32gui.EnumWindows(cb, None)
        return found[0] if found else None

    def focus(self) -> None:
        import win32con
        import win32gui
        h = self.dialog_hwnd() or self.hwnd()
        if not h:
            return
        if win32gui.IsIconic(h):
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(h)
        except Exception:
            try:
                import win32com.client           # alt-key tap lets us take the foreground
                win32com.client.Dispatch("WScript.Shell").SendKeys("%")
                win32gui.SetForegroundWindow(h)
            except Exception:
                pass
        time.sleep(0.2)

    def open(self, arg: str = "") -> None:
        """Open the app (optionally with a URL / file argument), wait, bring it to front."""
        if self.is_browser:
            if not self.exe:
                raise SystemExit(
                    f"{self.app} not found - add its path to APPS['{self.app}']")
            # joins the running browser
            subprocess.Popen([self.exe, arg] if arg else [self.exe])
        else:
            subprocess.Popen(
                f'start "" "{self.exe}" {arg}'.strip(), shell=True)
        time.sleep(OPEN_WAIT)
        self.focus()

    # ---- primary: UI Automation ----
    def _uia(self) -> Snapshot:
        from pywinauto import Desktop
        d = self.dialog_hwnd()
        h = d or self.hwnd()
        if not h:
            return Snapshot("", None, [], "app window not found")
        win = Desktop(backend="uia").window(handle=h)
        title, root = win.window_text(), win.element_info
        if d:
            return Snapshot("[DIALOG] " + title, None, _read(root, DIALOG_ROLES, None, 500))
        if self.is_browser:                      # the page itself, not the browser chrome
            docs = root.descendants(control_type="Document")
            if not docs:
                return Snapshot(title, None, [], "no page document exposed")
            root = max(docs, key=lambda x: (x.rectangle.right - x.rectangle.left)
                       * (x.rectangle.bottom - x.rectangle.top))
        vp = root.rectangle
        return Snapshot(title, (vp.left, vp.top, vp.right, vp.bottom), _read(root, ROLES, vp))

    # ---- fallback: Moondream ----
    def _vision_list(self) -> list[El]:
        """Ask Moondream what is clickable, then point at each label -> names + coordinates."""
        if self.vision_left <= 0:
            return []
        self.vision_left -= 1
        try:
            img = pyautogui.screenshot()

            def call(m):
                labels: list[str] = []
                for line in str(m.query(img, VISION_Q)["answer"]).splitlines():
                    lab = re.sub(r"^[\s\-\*\u2022\d\.\)]+", "",
                                 line).strip().strip("\"'")[:60]
                    if lab and lab not in labels:
                        labels.append(lab)
                return {lab: (m.point(img, lab).get("points") or [])[:3]
                        for lab in labels[:VISION_MAX_ITEMS]}
            found = _moondream(call)
        except Exception as e:
            print(f"[vision] failed: {e}")
            return []
        w, h = img.size
        return [El("Vision", lab, *_px(p, w, h), source="vision")
                for lab, pts in found.items() for p in pts]

    def locate(self, description: str) -> Optional[El]:
        """One-off Moondream lookup for a described thing ('the play button') -> El or None."""
        if self.vision_left <= 0:
            return None
        self.vision_left -= 1
        try:
            img = pyautogui.screenshot()
            res = _moondream(lambda m: m.point(img, description))
        except Exception as e:
            print(f"[vision] locate failed: {e}")
            return None
        pts = res.get("points") or []
        return El("Vision", description[:80], *_px(pts[0], *img.size), source="vision") if pts else None

    # ---- the one call the agent needs ----
    def snapshot(self, force_vision: bool = False) -> Snapshot:
        self.focus()
        snap = self._uia()
        # Chromium / Electron turn their tree on lazily
        end = time.time() + TREE_WAIT
        while not snap.elements and time.time() < end:
            time.sleep(0.5)
            snap = self._uia()
        if (force_vision or not snap.elements) and not snap.title.startswith("[DIALOG]"):
            extra = self._vision_list()
            if extra:
                snap.elements += extra
                snap.note = "vision elements are approximate (found on a screenshot)"
            elif not snap.elements:
                snap.note = "nothing readable and vision unavailable/used up - use the keyboard"
            for i, e in enumerate(snap.elements):
                e.id = i
        return snap
