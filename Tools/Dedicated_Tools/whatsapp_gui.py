"""
TARZ - WhatsApp desktop tools (UI Automation, Windows)

    whatsapp_send_message("Mom", "on my way")      # search -> Enter -> type -> Enter
    whatsapp_call("Mom")                           # search -> Enter -> voice call (default)
    whatsapp_call("Mom", video=True)               # same, but video call

CLI:
    python whatsapp_tools.py msg  "Mom" "on my way"
    python whatsapp_tools.py call "Mom" [--video]
    add --no-verify to skip the "top search result matches the name" safety check

pip install pywin32 pyautogui uiautomation psutil
"""

import ctypes
import os
import sys
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(
        2)   # win32 + UIA use the same pixels
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import psutil
import pyautogui
import uiautomation as auto
import win32api
import win32con
import win32gui
import win32process

pyautogui.PAUSE = 0.03

WA_EXE = "whatsapp.root.exe"
SEARCH_BOX_NAME = "Search or start a new chat"
CHAT_LIST_NAME = "Chat list"
# chat-list pane; its right edge = start of the chat pane
SIDE_ID = "side"
# section titles that show up as rows in search results (not selectable)
SECTION_HEADERS = {"chats", "contacts", "groups", "groups in common", "messages", "channels",
                   "communities", "people", "archived", "more", "recent chats", "search results",
                   "contacts on whatsapp", "not in your contacts", "links", "media", "docs"}


# ---------------------------------------------------------------- window
def find_wa_hwnd():
    best = []

    def visit(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindow(hwnd, win32con.GW_OWNER):
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil.Process(pid).name().lower() != WA_EXE:
                return True
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            if (r - l) * (b - t) >= 300 * 300:
                best.append(((r - l) * (b - t), hwnd))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(visit, None)
    return max(best)[1] if best else 0


def open_whatsapp(timeout=20.0):
    """Find the WhatsApp window (starting the app if needed) and bring it to the front."""
    hwnd = find_wa_hwnd()
    if not hwnd:
        try:
            os.startfile("whatsapp:")
        except Exception:
            return 0
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.6)
            hwnd = find_wa_hwnd()
            if hwnd:
                time.sleep(3)
                break
    if not hwnd:
        return 0

    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.3)
    for _ in range(2):
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        if win32gui.GetForegroundWindow() == hwnd:
            break
        # Windows sometimes refuses; tapping Alt unlocks SetForegroundWindow
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.4)
    return hwnd


# ---------------------------------------------------------------- UI helpers
def walk_find(root, predicate, skip=None, first_only=True):
    """DFS; skip(control) -> True means: don't descend into this control."""
    hits, stack = [], [root]
    while stack:
        c = stack.pop()
        try:
            if predicate(c):
                hits.append(c)
                if first_only:
                    return hits
            if skip and skip(c):
                continue
            stack.extend(reversed(c.GetChildren()))
        except Exception:
            pass
    return hits


def skip_chat_list(c):
    """Don't descend into the (huge) chat list when looking for other things."""
    try:
        return c.ControlTypeName == "DataGridControl" and (c.Name or "") == CHAT_LIST_NAME
    except Exception:
        return False


def click_center(c):
    c.MoveCursorToMyCenter()
    time.sleep(0.2)
    c.Click(simulateMove=False)


def paste_text(text):
    """Paste via clipboard: works for emoji and any language."""
    auto.SetClipboardText(text)
    time.sleep(0.1)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)


def wait_for_search_box(root, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        hit = walk_find(root, lambda c: c.ControlTypeName == "EditControl"
                        and (c.Name or "") == SEARCH_BOX_NAME, skip=skip_chat_list)
        if hit:
            return hit[0]
        time.sleep(0.8)
    return None


# ---------------------------------------------------------------- search + open the chat
def top_search_results(root, n=3):
    """Names of the first n real result rows (section headers skipped), top to bottom."""
    side = walk_find(root, lambda c: (c.AutomationId or "")
                     == SIDE_ID, skip=skip_chat_list)
    stack = [side[0] if side else root]
    rows = []
    while stack:
        c = stack.pop()
        try:
            if c.ControlTypeName == "DataItemControl" and (c.Name or "") and (c.ClassName or ""):
                r = c.BoundingRectangle
                name = c.Name.strip()
                is_header = name.casefold() in SECTION_HEADERS or (r.bottom - r.top) < 60
                if r.bottom > r.top and not is_header:
                    rows.append((r.top, name))
                continue                       # don't descend into a row
            stack.extend(c.GetChildren())
        except Exception:
            pass
    rows.sort()
    return [name for _, name in rows[:n]]


def matches(query, name):
    low = (name or "").casefold()
    return all(w in low for w in query.casefold().split())


def search_and_open(root, search_box, contact, verify=True):
    """search -> (check top result) -> Enter. True if the chat was opened."""
    click_center(search_box)
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("backspace")
    paste_text(contact)
    print(f"Typed '{contact}' in search.")

    if verify:
        seen, end = [], time.time() + 4.0
        while time.time() < end:
            time.sleep(0.5)
            seen = top_search_results(root, 3)
            if seen and matches(contact, seen[0]):
                break
        if not (seen and matches(contact, seen[0])):
            print(
                f"⚠️ NOT pressing Enter: top search result doesn't match '{contact}'.")
            print(f"   Top rows seen: {[s[:50] for s in seen]}")
            print("   (If this looks right, use --no-verify / verify=False.)")
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("backspace")
            return False
        print(f"Top result: {seen[0][:60]}")
    else:
        time.sleep(1.2)

    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(1.0)       # chat opens and the message box gets focus
    return True


def open_chat(contact, verify):
    """Bring WhatsApp up, search the contact and open the chat. Returns root or None."""
    hwnd = open_whatsapp()
    if not hwnd:
        print("Could not open WhatsApp.")
        return None
    root = auto.ControlFromHandle(hwnd)
    search_box = wait_for_search_box(root)
    if not search_box:
        print("WhatsApp UI not found (search box missing).")
        return None
    return root if search_and_open(root, search_box, contact, verify) else None


# ---------------------------------------------------------------- 1) text someone
def whatsapp_send_message(contact: str, message: str, verify=True) -> bool:
    if not open_chat(contact, verify):
        return False

    # message box already has focus after Enter. Multi-line: Shift+Enter between lines.
    lines = message.split("\n")
    for i, line in enumerate(lines):
        if line:
            paste_text(line)
        if i < len(lines) - 1:
            pyautogui.hotkey("shift", "enter")
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.4)
    print(f"Message sent to {contact} ✅")
    return True


# ---------------------------------------------------------------- 2) call someone
def header_area(root):
    """Chat header strip: right of the chat list, top 150px of the window."""
    side = walk_find(root, lambda c: (c.AutomationId or "")
                     == SIDE_ID, skip=skip_chat_list)
    r = root.BoundingRectangle
    left = side[0].BoundingRectangle.right if side else r.left + \
        int((r.right - r.left) * 0.42)
    return (left, r.top, r.right, r.top + 150)


def find_in_area(root, area, words, types=("ButtonControl", "MenuItemControl")):
    al, at, ar, ab = area

    def pred(c):
        if c.ControlTypeName not in types:
            return False
        n = (c.Name or "").casefold()
        if not n or not any(w in n for w in words):
            return False
        r = c.BoundingRectangle
        return r.right > r.left and al - 5 <= r.left and r.right <= ar + 5 \
            and at <= r.top and r.bottom <= ab
    return walk_find(root, pred, skip=skip_chat_list, first_only=False)


def whatsapp_call(contact: str, video=False, verify=True) -> bool:
    root = open_chat(contact, verify)
    if not root:
        return False

    kind = "video" if video else "voice"
    area = header_area(root)

    # (a) separate buttons: "Voice call" / "Video call"
    btns = find_in_area(root, area, [f"{kind} call"])
    if btns:
        click_center(btns[0])
        print(f"{kind.capitalize()} call started with {contact} 📞")
        return True

    # (b) one "Call" button that opens a Voice / Video menu
    callbtn = [b for b in find_in_area(root, area, ["call"])
               if (b.Name or "").casefold().strip() in ("call", "calls", "start call")]
    if callbtn:
        click_center(callbtn[0])
        time.sleep(0.6)
        items = find_in_area(
            root, (area[0], area[1], area[2], area[3] + 250), [kind])
        if items:
            click_center(items[0])
            print(f"{kind.capitalize()} call started with {contact} 📞")
            return True

    print("Could not find the call button in the chat header ⚠️ "
          "(dump the UI with a chat open and send it to me).")
    return False


# ---------------------------------------------------------------- CLI
if __name__ == "__main__":
    args = sys.argv[1:]
    verify = "--no-verify" not in args
    video = "--video" in args
    args = [a for a in args if not a.startswith("--")]
    if len(args) >= 3 and args[0] == "msg":
        whatsapp_send_message(args[1], " ".join(args[2:]), verify)
    elif len(args) >= 2 and args[0] == "call":
        whatsapp_call(args[1], video, verify)
    else:
        print(__doc__)
