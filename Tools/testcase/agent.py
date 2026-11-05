"""
agent.py - tiny agent layer on top of expose.py

    python agent.py "open spotify and play Sailor Song" spotify
    python agent.py "scroll down in this window"           (no app = use current front window)

Loop:  expose() -> LLM (OpenRouter free models, auto-fallback) picks ONE tool -> pyautogui executes -> repeat
Tools: click, move, scroll, type_into, type_text, press_key, done
Needs: pip install langchain-openai pyautogui python-dotenv pyperclip   +   OPENROUTER_API_KEY in .env
"""

from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))  # noqa

import time
import pyautogui
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from Tools.web_agent import move_to           # human-like cursor movement
from screen_layer import open_app, wait, expose     # the exposing layer

load_dotenv(override=True)             # .env wins over a stale system env var
pyautogui.FAILSAFE = True                     # slam mouse into a corner to abort

# tried top to bottom; if one errors / is rate-limited / gets pulled, the next one answers.
# free listings rotate often -> override without editing code: OPENROUTER_MODELS="a:free,b:free"
MODELS = [m.strip() for m in os.getenv("OPENROUTER_MODELS", "").split(",") if m.strip()] or [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "nvidia/nemotron-3.5-lightning:free",
]
_llms: dict = {}
MAX_STEPS = 25
HISTORY = 8                                   # how many past actions the LLM sees

# id -> El from the latest expose()
_els: dict[int, object] = {}


# --------------------------------------------------------------------------
# Tools (pure pyautogui)
# --------------------------------------------------------------------------
def _go(element_id: int):
    e = _els.get(element_id)
    if e is None:
        return None, f"no element with id {element_id}"
    move_to(e.x, e.y)
    return e, ""


@tool
def click(element_id: int, button: str = "left", clicks: int = 1) -> str:
    """Move the cursor to the element and click it. button: left|right|middle. clicks: 1 or 2 (double)."""
    e, err = _go(element_id)
    if err:
        return err
    pyautogui.click(button=button, clicks=clicks)
    return f'clicked [{e.id}] "{e.name}" ({button} x{clicks})'


@tool
def move(element_id: int) -> str:
    """Only move the cursor onto the element (hover), no click."""
    e, err = _go(element_id)
    if err:
        return err
    return f'moved cursor to [{e.id}] "{e.name}"'


@tool
def scroll(direction: str = "down", amount: int = 5, element_id: int = -1) -> str:
    """Scroll the mouse wheel. direction: up|down|left|right. Optionally hover element_id first
    (so the right pane scrolls). amount = wheel notches."""
    if element_id in _els:
        _go(element_id)
    n = abs(int(amount)) * 120
    if direction in ("up", "down"):
        pyautogui.scroll(n if direction == "up" else -n)
    else:
        pyautogui.hscroll(n if direction == "right" else -n)
    return f"scrolled {direction} {amount}"


def _paste(text: str) -> None:
    """Clipboard paste: works in Electron/CEF apps (Spotify, Discord...) and with unicode,
    where raw pyautogui.write key events often get dropped."""
    try:
        import pyperclip
    except ImportError:
        pyautogui.write(text, interval=0.05)      # fallback (ASCII only)
        return
    old = pyperclip.paste()
    pyperclip.copy(text)
    time.sleep(0.15)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)
    pyperclip.copy(old)


@tool
def type_into(element_id: int, text: str, press_enter: bool = False) -> str:
    """Click a text field / search box, clear it, then type text. PREFER this over click + type_text."""
    e, err = _go(element_id)
    if err:
        return err
    pyautogui.click()
    time.sleep(0.4)                               # let focus land
    pyautogui.hotkey("ctrl", "a")
    _paste(text)
    if press_enter:
        time.sleep(0.2)
        pyautogui.press("enter")
    return f'typed "{text}" into [{e.id}] "{e.name}"' + (" + Enter" if press_enter else "")


@tool
def type_text(text: str, press_enter: bool = False) -> str:
    """Type text into whatever is ALREADY focused. Use type_into if you need to click the field first."""
    _paste(text)
    if press_enter:
        time.sleep(0.2)
        pyautogui.press("enter")
    return f'typed "{text}"' + (" + Enter" if press_enter else "")


@tool
def press_key(keys: str) -> str:
    """Press a key or hotkey. Examples: 'enter', 'esc', 'ctrl+l', 'alt+tab'."""
    parts = [k.strip() for k in keys.lower().split("+")]
    pyautogui.hotkey(*parts) if len(parts) > 1 else pyautogui.press(parts[0])
    return f"pressed {keys}"


@tool
def done(summary: str) -> str:
    """Call when the task is finished (or impossible). summary = what happened."""
    return summary


TOOLS = [click, move, scroll, type_into, type_text, press_key, done]
BY_NAME = {t.name: t for t in TOOLS}


def _key() -> str:
    return os.environ.get("OPENROUTER_API_KEY2", "").strip().strip("\"'")


def _check_key() -> None:
    """Fail fast on a bad key instead of looping on 401s."""
    import requests
    k = _key()
    if not k:
        sys.exit("[agent] OPENROUTER_API_KEY is empty - put it in .env")
    r = requests.get("https://openrouter.ai/api/v1/key",
                     headers={"Authorization": f"Bearer {k}"}, timeout=15)
    if r.status_code != 200:
        sys.exit(f"[agent] key {k[:10]}... rejected ({r.status_code}): {r.text[:120]}\n"
                 "        -> make a fresh key at openrouter.ai/settings/keys")
    print(f"[agent] key OK ({k[:10]}...)")


def _llm(name: str):
    if name not in _llms:
        _llms[name] = ChatOpenAI(
            model=name, temperature=0, max_retries=0,          # fail fast -> next model
            base_url="https://openrouter.ai/api/v1",
            api_key=_key(),
        ).bind_tools(TOOLS)
    return _llms[name]


def _ask(messages):
    last = None
    for name in MODELS:
        try:
            resp = _llm(name).invoke(messages)
            print(f"[agent] model: {name}")
            return resp
        except Exception as ex:
            last = ex
            print(f"[agent] {name} failed: {str(ex)[:150]}")
            if "401" in str(ex):                      # bad key: every model will fail
                break
    raise last


SYSTEM = (
    "You control a Windows PC through pyautogui. Each step you get the task, your recent actions and "
    "a list of on-screen elements: [id] Role: \"name\" @(x,y) zone. zone 'below'/'above' means off-screen "
    "(scroll to reach it). Call exactly ONE tool per step. To enter text in a field use type_into(element_id, text) in a single step. Refer to elements by id only. Ids change every "
    "step, so always use the ids from the latest list. If several elements share a name, pick by role and "
    "position. Check the recent actions so you don't repeat yourself. Call done() as soon as the task is complete."
)


# --------------------------------------------------------------------------
# Agent loop
# --------------------------------------------------------------------------
def _screen_text(data: dict) -> str:
    lines = [f'window: {data["window"]!r} (source={data["source"]})']
    for e in data["elements"]:
        lines.append(f'[{e.id}] {e.role}: "{e.name}" @({e.x},{e.y}) {e.where}')
    return "\n".join(lines)


def run(task: str, app: str = "", max_steps: int = MAX_STEPS) -> str:
    _check_key()
    if app:
        open_app(app)
    wait()

    log: list[str] = []
    for step in range(1, max_steps + 1):
        data = expose(task=task)
        _els.clear()
        _els.update({e.id: e for e in data["elements"]})

        prompt = (f"TASK: {task}\n\nRECENT ACTIONS:\n" +
                  ("\n".join(log[-HISTORY:]) or "(none yet)") +
                  f"\n\nSCREEN:\n{_screen_text(data)}")
        try:
            resp = _ask([SystemMessage(SYSTEM), HumanMessage(prompt)])
        except Exception as ex:
            print(f"[agent] LLM error: {ex}")
            if "401" in str(ex):
                return "stopped: API key rejected"
            time.sleep(2)
            continue

        if not resp.tool_calls:
            log.append("(no tool called - pick a tool)")
            continue

        call = resp.tool_calls[0]                    # one action per step
        name, args = call["name"], call["args"]
        print(f"[step {step}] {name}({args})")
        if name == "done":
            print(f"[agent] done: {args.get('summary', '')}")
            return args.get("summary", "")
        try:
            result = BY_NAME[name].invoke(args)
        except Exception as ex:
            result = f"error: {ex}"
        print(f"          -> {result}")
        log.append(f"{name}({args}) -> {result}")
        wait()

    return "stopped: max steps reached"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('usage: python agent.py "task" [app]')
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
