"""
TARZ - Spotify tool (merged): background search + play a TRACK, or play a PLAYLIST
from your own library.  Standalone, desktop app.

Blink-focus pattern: briefly take foreground focus (AttachThreadInput), do the job,
then give focus back to whatever the user was using.

Usage:
    python spotify_tool.py song "maname"
    python spotify_tool.py playlist "gym mix"

    from spotify_tool import spotify_search_and_play, spotify_play_playlist
    spotify_search_and_play("maname")
    spotify_play_playlist("Eternal")

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

# ---------------------------------------------------------------- shared constants
SPOTIFY_EXE = "spotify.exe"
MIN_WINDOW_AREA = 200 * 200  # ignore Spotify's tiny hidden helper windows

# song search
TRACK_ID_PREFIX = "search-modal-result-spotify:track:"
RESULTS_TIMEOUT = 6.0   # max wait for search results to render
PLAY_CONFIRM_WAIT = 3.0  # max wait for the window title to show the song

# playlist
SIDEBAR_ID = "Desktop_LeftSidebar_Id"
PLAYLIST_HINT_PREFIX = "onClickHintspotify:playlist:"
# big parts of the page we never need to walk (speeds things up a lot)
PRUNE_IDS = {"main-view", "Desktop_PanelContainer_Id", "global-nav-bar"}


# ================================================================ SHARED: window handling
def find_spotify_hwnd():
    """Main Spotify window handle, whatever its title is. 0 if there is none."""
    candidates = []

    def visit(hwnd, _):
        try:
            if win32gui.GetWindow(hwnd, win32con.GW_OWNER):  # skip owned popups
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil.Process(pid).name().lower() != SPOTIFY_EXE:
                return True
            left, top, right, bottom = win32gui.GetWindowPlacement(hwnd)[4]
            area = (right - left) * (bottom - top)
            if area >= MIN_WINDOW_AREA:
                candidates.append((area, hwnd))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(visit, None)
    return max(candidates)[1] if candidates else 0


def ensure_spotify_open(timeout=20.0):
    """Return Spotify's window handle, launching or waking Spotify if needed. 0 on failure."""
    hwnd = find_spotify_hwnd()
    if not hwnd:
        # starts Spotify, or wakes the running one from the tray
        os.startfile("spotify:")
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.5)
            hwnd = find_spotify_hwnd()
            if hwnd:
                time.sleep(4)  # let the UI finish loading
                break
    if hwnd and (win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd)):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.5)
    return hwnd


def _attach(this_thread, other_thread):
    if other_thread and other_thread != this_thread:
        win32process.AttachThreadInput(this_thread, other_thread, True)
        return True
    return False


def blink_foreground(target_hwnd):
    """Attach input threads so SetForegroundWindow reliably succeeds, then bring target to front."""
    current = win32gui.GetForegroundWindow()
    cur_t = win32process.GetWindowThreadProcessId(current)[0]
    tgt_t = win32process.GetWindowThreadProcessId(target_hwnd)[0]
    me = win32api.GetCurrentThreadId()
    a1 = _attach(me, cur_t)
    a2 = _attach(me, tgt_t)
    try:
        if win32gui.IsIconic(target_hwnd):
            win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
            time.sleep(0.2)
        win32gui.SetForegroundWindow(target_hwnd)
        time.sleep(0.3)
    finally:
        if a1:
            win32process.AttachThreadInput(me, cur_t, False)
        if a2:
            win32process.AttachThreadInput(me, tgt_t, False)
    return current


def restore_previous(prev_hwnd, spotify_hwnd, minimize_spotify=True):
    if minimize_spotify:
        win32gui.ShowWindow(spotify_hwnd, win32con.SW_MINIMIZE)
    try:
        me = win32api.GetCurrentThreadId()
        pt = win32process.GetWindowThreadProcessId(prev_hwnd)[0]
        attached = _attach(me, pt)
        win32gui.SetForegroundWindow(prev_hwnd)
        if attached:
            win32process.AttachThreadInput(me, pt, False)
    except Exception as e:
        print(f"[warn] could not restore previous window: {e}")


# ================================================================ SONG: finding the track
def _song_title(name: str) -> str:
    """'Starboy by The Weeknd, Daft Punk (Track)' -> 'Starboy'."""
    return name.rsplit(" by ", 1)[0].strip()


def collect_track_results(root, window_rect):
    """Visible TRACK rows of the search popup, as (control, name)."""
    win_left, win_top, win_right, win_bottom = window_rect
    found = []

    def walk(control):
        try:
            if (control.ControlTypeName == "ListItemControl"
                    and (control.AutomationId or "").startswith(TRACK_ID_PREFIX)):
                r = control.BoundingRectangle
                on_screen = (r.right > r.left and r.top >= win_top and r.bottom <= win_bottom
                             and r.left >= win_left and r.right <= win_right)
                if on_screen:
                    found.append((control, control.Name or ""))
        except Exception:
            pass
        try:
            for child in control.GetChildren():
                walk(child)
        except Exception:
            pass

    walk(root)
    return found


def wait_for_results(root, window_rect, timeout=RESULTS_TIMEOUT):
    end = time.time() + timeout
    while True:
        results = collect_track_results(root, window_rect)
        if results or time.time() >= end:
            return results
        time.sleep(0.4)


def pick_best(results, query):
    """Best match: most query words in the name, exact title wins, then top-most row."""
    words = query.lower().split()

    def key(item):
        control, name = item
        low = name.lower()
        score = sum(w in low for w in words)
        if _song_title(name).lower() == query.lower():
            score += 10
        return (-score, control.BoundingRectangle.top)

    return sorted(results, key=key)[0]


# ---------------------------------------------------------------- SONG: confirming playback
def track_play_confirmed(hwnd, title_before, song_title, wait=PLAY_CONFIRM_WAIT):
    """Spotify's window title becomes 'Artist - Song' while playing."""
    end = time.time() + wait
    while time.time() < end:
        now = win32gui.GetWindowText(hwnd)
        if song_title.lower() in now.lower():
            return True
        if now != title_before and not now.lower().startswith("spotify"):
            return True
        time.sleep(0.25)
    return False


# ---------------------------------------------------------------- SONG: the track's own Play button
def find_visible(root, window_rect, kind, predicate):
    """Visible controls of one type whose Name passes predicate, top-most first."""
    win_left, win_top, win_right, win_bottom = window_rect
    found = []

    def walk(control):
        try:
            if control.ControlTypeName == kind and predicate(control.Name or ""):
                r = control.BoundingRectangle
                if (r.right > r.left and r.top >= win_top and r.bottom <= win_bottom
                        and r.left >= win_left and r.right <= win_right):
                    found.append((r.top, control))
        except Exception:
            pass
        try:
            for child in control.GetChildren():
                walk(child)
        except Exception:
            pass

    walk(root)
    return [c for _, c in sorted(found, key=lambda x: x[0])]


def click_track_play_button(root, window_rect, result_name, song, timeout=5.0):
    """After the search click opened the album page, press the track row's own Play button.
    Its accessible name is 'Play <title> by <artists>' (same text as the hover tooltip)."""
    exact = f"play {result_name.removesuffix(' (Track)')}".lower()
    prefix = f"play {song}".lower()
    end = time.time() + timeout
    while True:
        buttons = find_visible(root, window_rect, "ButtonControl",
                               lambda n: n.lower().startswith(prefix))
        if buttons:
            best = next((b for b in buttons if (
                b.Name or "").lower() == exact), buttons[0])
            best.Click()  # moves the mouse onto the row's play icon and clicks it
            return True
        if time.time() >= end:
            return False
        time.sleep(0.3)


# ================================================================ PLAYLIST: UI helpers
def walk_find(root, predicate, prune_ids=(), first_only=True):
    """DFS over the UIA tree; skips subtrees whose AutomationId is in prune_ids."""
    hits = []
    stack = [root]
    while stack:
        c = stack.pop()
        try:
            if predicate(c):
                hits.append(c)
                if first_only:
                    return hits
            if (c.AutomationId or "") in prune_ids:
                continue
            kids = c.GetChildren()
            stack.extend(reversed(kids))
        except Exception:
            pass
    return hits


def wait_for_ui(root, timeout=15.0):
    """Chromium builds its accessibility tree lazily: wait until the sidebar exists."""
    end = time.time() + timeout
    while time.time() < end:
        found = walk_find(root, lambda c: (c.AutomationId or "") == SIDEBAR_ID,
                          prune_ids=PRUNE_IDS)
        if found:
            return found[0]
        time.sleep(0.8)
    return None


def is_visible(c, win_rect):
    try:
        r = c.BoundingRectangle
        l, t, rr, b = win_rect
        return r.right > r.left and r.bottom > r.top and r.top >= t and r.bottom <= b \
            and r.left >= l and r.right <= rr
    except Exception:
        return False


def click_center(c):
    c.MoveCursorToMyCenter()      # hover first: the cover's play button reacts to hover
    time.sleep(0.3)
    c.Click(simulateMove=False)


def type_text(text):
    """Type into the focused box. Plain ASCII is typed; emoji/unicode goes through the clipboard."""
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    if text.isascii():
        pyautogui.write(text, interval=0.02)
    else:
        auto.SetClipboardText(text)
        pyautogui.hotkey("ctrl", "v")


# ---------------------------------------------------------------- PLAYLIST: Library open + filter
def open_library_filter(sidebar, win_rect):
    """Make sure the library is open and the filter box has focus."""
    # collapsed library? ("Open Your Library")
    opener = walk_find(sidebar, lambda c: c.ControlTypeName == "ButtonControl"
                       and (c.Name or "").lower() == "open your library")
    if opener and is_visible(opener[0], win_rect):
        click_center(opener[0])
        time.sleep(0.8)

    btn = walk_find(sidebar, lambda c: c.ControlTypeName == "ButtonControl"
                    and (c.Name or "") == "Search in Your Library")
    if btn and is_visible(btn[0], win_rect):
        click_center(btn[0])
        time.sleep(0.5)
        return True
    edit = walk_find(sidebar, lambda c: c.ControlTypeName == "EditControl"
                     and (c.Name or "").startswith("Type to filter your library"))
    if edit and is_visible(edit[0], win_rect):
        click_center(edit[0])
        time.sleep(0.4)
        return True
    return False


def library_rows(sidebar):
    """Outer DataItem rows of the library list (the inner duplicate has an empty ClassName)."""
    rows = []

    # don't descend into a row once found: collect then stop at that node
    stack = [sidebar]
    while stack:
        c = stack.pop()
        try:
            if c.ControlTypeName == "DataItemControl" and (c.Name or "") and (c.ClassName or ""):
                rows.append(c)
                continue
            stack.extend(reversed(c.GetChildren()))
        except Exception:
            pass
    return rows


def row_info(row):
    """(is_playlist, play_button) for one library row."""
    is_pl, play = False, None
    stack = [(row, 0)]
    while stack:
        c, d = stack.pop()
        try:
            if (c.AutomationId or "").startswith(PLAYLIST_HINT_PREFIX):
                is_pl = True
            if c.ControlTypeName == "ButtonControl" and (c.Name or "").startswith("Play "):
                play = c
            if d < 7:
                for ch in c.GetChildren():
                    stack.append((ch, d + 1))
        except Exception:
            pass
    return is_pl, play


def find_playlist_play_button(sidebar, wanted, win_rect):
    """Best PLAYLIST row for `wanted` -> (play_button, row_name) or (None, None)."""
    w = wanted.casefold().strip()
    best = None
    for row in library_rows(sidebar):
        name = (row.Name or "").strip()
        low = name.casefold()
        if w not in low and low not in w:
            continue
        is_pl, play = row_info(row)
        if not is_pl or play is None or not is_visible(play, win_rect):
            continue
        score = 3 if low == w else 2 if low.startswith(w) else 1
        top = row.BoundingRectangle.top
        key = (-score, top)
        if best is None or key < best[0]:
            best = (key, play, name)
    return (best[1], best[2]) if best else (None, None)


# ---------------------------------------------------------------- PLAYLIST: confirming playback
def now_playing(root):
    """'Now playing: Maname by Sarah Black' (or '' if nothing)."""
    hit = walk_find(root, lambda c: (c.Name or "").startswith("Now playing:"),
                    prune_ids=PRUNE_IDS | {SIDEBAR_ID})
    return (hit[0].Name or "") if hit else ""


def playlist_play_confirmed(hwnd, root, title_before, np_before, wait=4.0):
    end = time.time() + wait
    while time.time() < end:
        title = win32gui.GetWindowText(hwnd)
        if title != title_before and not title.lower().startswith("spotify"):
            return True
        time.sleep(0.4)
    np_now = now_playing(root)
    return bool(np_now) and np_now != np_before


# ================================================================ ENTRY POINT 1: song
def spotify_search_and_play(query: str, minimize_after=True) -> bool:
    """Search and play. True only if playback was confirmed, False otherwise."""
    original_hwnd = win32gui.GetForegroundWindow()  # capture BEFORE launching anything

    spotify_hwnd = ensure_spotify_open()
    if not spotify_hwnd:
        print("Could not open Spotify (not installed, or it took too long to start).")
        return False

    blink_foreground(spotify_hwnd)

    try:
        root = auto.ControlFromHandle(spotify_hwnd)
        window_rect = win32gui.GetWindowRect(spotify_hwnd)
        title_before = win32gui.GetWindowText(spotify_hwnd)

        pyautogui.hotkey("ctrl", "k")  # Spotify's search shortcut
        time.sleep(0.6)
        pyautogui.write(query, interval=0.02)

        results = wait_for_results(root, window_rect)
        if not results:
            print("No matching track found 💀")
            return False

        target, name = pick_best(results, query)
        song = _song_title(name)
        print(f"Found: {name}")

        # Step 1: click the search result. For a track this usually just OPENS its album page.
        target.Click()
        if track_play_confirmed(spotify_hwnd, title_before, song, wait=1.5):
            print("Play confirmed ✅")
            return True

        # Step 2: press that track row's own Play button. A track that is already playing
        # shows "Pause ..." instead, so this can never pause it by accident.
        if click_track_play_button(root, window_rect, name, song):
            if track_play_confirmed(spotify_hwnd, title_before, song):
                print("Play confirmed ✅")
                return True
            print("Clicked the play button, but playback was not confirmed ⚠️")
            return False

        print("Opened the track's page but found no Play button for it ⚠️")
        return False

    finally:
        restore_previous(original_hwnd, spotify_hwnd, minimize_after)


# ================================================================ ENTRY POINT 2: playlist
def spotify_play_playlist(name: str, minimize_after=True) -> bool:
    """Play a playlist from the user's library. True only if playback was confirmed."""
    original = win32gui.GetForegroundWindow()
    hwnd = ensure_spotify_open()
    if not hwnd:
        print("Could not open Spotify.")
        return False

    blink_foreground(hwnd)
    try:
        root = auto.ControlFromHandle(hwnd)
        sidebar = wait_for_ui(root)
        if not sidebar:
            print("Spotify page not exposed to UI Automation. "
                  "Start it once with:  Spotify.exe --force-renderer-accessibility")
            return False

        win_rect = win32gui.GetWindowRect(hwnd)
        title_before = win32gui.GetWindowText(hwnd)
        np_before = now_playing(root)

        if not open_library_filter(sidebar, win_rect):
            print("Could not find 'Search in Your Library' 💀")
            return False

        type_text(name)

        # the list filters as you type: poll until the playlist's cover play button shows up
        play_btn, row_name = None, None
        end = time.time() + 6.0
        while time.time() < end:
            time.sleep(0.5)
            play_btn, row_name = find_playlist_play_button(
                sidebar, name, win_rect)
            if play_btn:
                break
        if not play_btn:
            print(f"No playlist matching '{name}' in your library 💀")
            return False

        print(f"Found playlist: {row_name}")
        click_center(play_btn)

        if playlist_play_confirmed(hwnd, root, title_before, np_before):
            print("Play confirmed ✅")
            ok = True
        else:
            print("Clicked the cover's play button, but playback was not confirmed ⚠️")
            ok = False

        # leave the library filter clean for next time
        try:
            btn = walk_find(sidebar, lambda c: c.ControlTypeName == "EditControl"
                            and (c.Name or "").startswith("Type to filter your library"))
            if btn and is_visible(btn[0], win_rect):
                click_center(btn[0])
                pyautogui.hotkey("ctrl", "a")
                pyautogui.press("backspace")
                pyautogui.press("escape")
        except Exception:
            pass
        return ok

    finally:
        restore_previous(original, hwnd, minimize_after)


if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    text = " ".join(sys.argv[2:]).strip()
    if mode == "song":
        spotify_search_and_play(text or "maname")
    elif mode == "playlist":
        spotify_play_playlist(text or "liked songs")
    else:
        print('Usage:\n  python spotify_tool.py song "<song name>"\n'
              '  python spotify_tool.py playlist "<playlist name>"')
