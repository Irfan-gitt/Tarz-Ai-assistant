"""
agent.py - PLAN -> per step (observe -> act -> verify) -> REPLAN on failure

    python agent.py "open spotify and play Gigi Perez"

pip install groq pyautogui pyperclip python-dotenv
set GROQ_API_KEY=...

Design rules:
  * The model never decides "done" alone. To finish a step it must cite EVIDENCE (text that is
    on screen right now) and the code checks that the text really exists in the full UIA tree.
  * Every action is followed by a screen-change check that is fed back to the model.
  * Stuck / failed steps trigger a replan from the real current screen (max MAX_REPLANS).
  * Every action is written to agent_trace.jsonl so failures can be debugged, not guessed.
"""

import os
import sys
import json
import time
import re
from copy import copy

import pyautogui
import pyperclip
from groq import Groq
from dotenv import load_dotenv

from screen_layer import open_app, wait, expose, compact, read_text, move_to

load_dotenv()

MODEL = "openai/gpt-oss-120b"
MAX_ACTIONS_PER_STEP = 8
MAX_REPLANS = 2
TRACE_FILE = "agent_trace.jsonl"
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

PLAN_SYSTEM = """You plan Windows desktop tasks. Break the task into 2-6 short, concrete steps.
Rules:
- Each step must END in a state that is visible on screen (a window title or a button/text label),
  so it can be verified. Example: "Open Spotify" (window visible), "Search for Gigi Perez"
  (results shown), "Play the top matching song" (a Pause button / track name visible).
- Only include "open the app" as a step if the task needs an app. Do not add filler steps.
- If told which steps are already done and what failed, return ONLY the remaining steps,
  adjusted to the current screen.
Reply with JSON only: {"steps": ["...", "..."]}"""

EXEC_SYSTEM = """You are a careful Windows desktop agent executing ONE step of a larger task.

Each turn you get the overall task, the plan, the CURRENT step, the front window title, a list of
interactive elements  [id] Role: "name" @(x,y) zone  (zone: visible / below / above the viewport),
and your recent actions. Each past action is followed by what happened:
  "screen changed"        -> probably worked, check the new screen
  "screen did NOT change" -> it did NOT work. Do something DIFFERENT.

Reply with ONE JSON object and nothing else ("thought" first, one short sentence):
  {"thought": "...", "action": "open_app", "name": "<app>"}
  {"thought": "...", "action": "click", "id": <id>}
  {"thought": "...", "action": "double_click", "id": <id>}
  {"thought": "...", "action": "type_into", "id": <id>, "text": "<text>", "submit": true|false}
  {"thought": "...", "action": "type", "text": "<text>"}        (field already focused)
  {"thought": "...", "action": "press", "key": "enter"}         (enter, tab, esc, ctrl+a ...)
  {"thought": "...", "action": "scroll", "amount": -500}        (negative = down)
  {"thought": "...", "action": "find", "text": "<label>"}       (search ALL elements, incl. hidden ones)
  {"thought": "...", "action": "read", "filter": ""}            (read window text)
  {"thought": "...", "action": "wait", "seconds": 1}
  {"thought": "...", "action": "step_done", "evidence": "<text currently on screen>"}
  {"thought": "...", "action": "fail", "reason": "..."}

Rules:
- Use only ids from the CURRENT list (ids from find results are 1000+ and valid until the screen changes).
- To enter text in a search bar or field use type_into, never just click it. submit=true for searches.
- If the element you need is not listed, use find before giving up.
- step_done needs "evidence": an exact label or window title that is on screen NOW and proves the
  CURRENT step is complete. The code checks it; made-up evidence is rejected.
- The window title may not contain the app name (e.g. Spotify shows the song). Any visible label is fine evidence.
- Work only on the CURRENT step. Do not skip ahead.
"""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def trace(**kw) -> None:
    with open(TRACE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(
            {"t": time.strftime("%H:%M:%S"), **kw}, default=str) + "\n")


def llm(system: str, user: str, effort: str = "medium") -> dict:
    for _ in range(2):                               # one retry on unusable output
        r = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0.1,
            max_completion_tokens=3000,              # gpt-oss reasons first
            reasoning_effort=effort,
        )
        text = r.choices[0].message.content or ""
        try:
            return json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        except Exception:
            print("   (bad JSON, retrying)")
    return {}


def observe(task: str):
    """-> (window title, ALL raw elements, compact list shown to the model)"""
    data = expose(task=task, compact_els=False)
    raw = data["elements"]
    shown = compact(raw, task, "", data["window"]
                    ) if data["source"] == "uia" else raw
    return data["window"], raw, shown


def fmt(title: str, shown: list, extra: dict) -> str:
    lines = [
        f'[{e.id}] {e.role}: "{e.name}" @({e.x},{e.y}) {e.where}' for e in shown]
    lines += [f'[{e.id}] {e.role}: "{e.name}" @({e.x},{e.y}) {e.where}  (from find)'
              for e in extra.values()]
    return f"Window: {title!r}\nElements:\n" + "\n".join(lines)


def evidence_ok(ev: str, title: str, raw: list) -> bool:
    """Grounded check: the cited text must really exist on screen."""
    ev = (ev or "").strip().lower()
    if len(ev) < 3:
        return False
    return ev in title.lower() or any(ev in e.name.lower() for e in raw)


def paste(text: str) -> None:
    pyperclip.copy(text)
    time.sleep(0.1)
    pyautogui.hotkey("ctrl", "v")


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------
def plan(task: str, done: list[str] | None = None, screen: str = "", feedback: str = "") -> list[str]:
    user = f"TASK: {task}\n"
    if done:
        user += "ALREADY DONE: " + "; ".join(done) + "\n"
    if feedback:
        user += f"LAST ATTEMPT FAILED BECAUSE: {feedback}\n"
    if screen:
        user += f"CURRENT SCREEN:\n{screen}\n"
    user += "Return the remaining steps as JSON."
    steps = llm(PLAN_SYSTEM, user, "low").get("steps") or []
    return [str(s) for s in steps][:8]


# --------------------------------------------------------------------------
# acting
# --------------------------------------------------------------------------
def act(a: dict, by_id: dict, raw: list, extra: dict) -> str:
    kind = a.get("action")

    if kind == "open_app":
        app = a.get("name") or a.get("app") or a.get("app_name")
        if not app:
            return 'FAILED: open_app needs "name"'
        open_app(app)
        return f"opened {app}"

    if kind in ("click", "double_click", "type_into"):
        e = by_id.get(a.get("id"))
        if not e:
            return f'FAILED: no element with id {a.get("id")} in the current list'
        move_to(e.x, e.y)
        if kind == "double_click":
            pyautogui.doubleClick()
            return f'double_click [{e.id}] "{e.name}"'
        pyautogui.click()
        if kind == "click":
            return f'click [{e.id}] "{e.name}"'
        time.sleep(0.5)                              # let the field take focus
        pyautogui.hotkey("ctrl", "a")
        paste(a["text"])
        if a.get("submit"):
            time.sleep(0.5)
            pyautogui.press("enter")
        return f'typed "{a["text"]}" into [{e.id}] "{e.name}"' + (" + enter" if a.get("submit") else "")

    if kind == "find":
        q = (a.get("text") or "").strip().lower()
        hits = [e for e in raw if q and q in e.name.lower()][:8]
        for k, e in enumerate(hits):
            c = copy(e)
            c.id = 1000 + k
            extra[c.id] = c
        return ("found: " + "; ".join(f'[{c.id}] {c.role} "{c.name}" @({c.x},{c.y}) {c.where}'
                                      for c in extra.values())) if hits else f'found nothing for "{q}"'
    if kind == "type":
        paste(a["text"])
        return f'typed "{a["text"]}"'
    if kind == "press":
        pyautogui.hotkey(*a["key"].lower().split("+"))
        return f'pressed {a["key"]}'
    if kind == "scroll":
        pyautogui.scroll(int(a.get("amount", -500)))
        return f'scrolled {a.get("amount", -500)}'
    if kind == "read":
        return "read: " + read_text(a.get("filter", ""))
    if kind == "wait":
        time.sleep(float(a.get("seconds", 1)))
        return "waited"
    return f"unknown action {kind}"


# --------------------------------------------------------------------------
# one step: observe -> act -> verify, until evidence is confirmed or it fails
# --------------------------------------------------------------------------
def run_step(task: str, steps: list[str], i: int):
    goal = steps[i]
    # [text, annotate_screen_change]
    history: list[list] = []
    extra: dict = {}
    recent: list[str] = []
    prev_sig = None
    plan_txt = "\n".join(
        f'{"->" if k == i else "  "} {k + 1}. {s}' for k, s in enumerate(steps))

    for n in range(1, MAX_ACTIONS_PER_STEP + 1):
        wait()
        title, raw, shown = observe(task)
        sig = title + "|" + ";".join(f"{e.role}:{e.name}" for e in shown[:60])
        if history and prev_sig is not None and history[-1][1]:
            history[-1][0] += " | " + \
                ("screen did NOT change" if sig == prev_sig else "screen changed")
        if sig != prev_sig:
            extra = {}                               # find results go stale when the screen changes
        prev_sig = sig

        prompt = (f"OVERALL TASK: {task}\n\nPLAN:\n{plan_txt}\n\nCURRENT STEP: {goal}\n\n"
                  f"RECENT ACTIONS (this step):\n" +
                  ("\n".join(h[0] for h in history[-6:]) or "(none yet)")
                  + f"\n\nCURRENT SCREEN:\n{fmt(title, shown, extra)}\n\nYour next action (JSON only):")
        a = llm(EXEC_SYSTEM, prompt)
        kind = a.get("action")
        print(f"   [{i + 1}.{n}] {a}")

        if kind == "step_done":
            ev = a.get("evidence", "")
            if evidence_ok(ev, title, raw):
                trace(step=i + 1, goal=goal, result="verified",
                      evidence=ev, window=title)
                return True, ev
            history.append(
                [f'{n}. step_done REJECTED: "{ev}" is NOT on screen', False])
            trace(step=i + 1, goal=goal, result="evidence rejected",
                  evidence=ev, window=title)
            continue
        if kind == "fail":
            return False, a.get("reason", "model gave up")

        key = json.dumps({k: v for k, v in a.items()
                         if k != "thought"}, sort_keys=True)
        recent.append(key)
        if len(recent) >= 3 and len(set(recent[-3:])) == 1:
            return False, f"stuck repeating {key}"

        by_id = {**{e.id: e for e in shown}, **extra}
        try:
            note = act(a, by_id, raw, extra)
        except Exception as ex:
            note = f"FAILED: {ex}"
        print("      ->", note)
        trace(step=i + 1, goal=goal, action=key, note=note, window=title)
        history.append([f"{n}. {key} -> {note}", kind not in ("find", "read")])
        time.sleep(0.8)

    return False, f"step not completed within {MAX_ACTIONS_PER_STEP} actions"


# --------------------------------------------------------------------------
# main loop
# --------------------------------------------------------------------------
def run(task: str) -> None:
    trace(task=task, event="start")
    steps = plan(task)
    if not steps:
        print("FAILED: planner returned no steps")
        return
    print("PLAN:\n" +
          "\n".join(f"  {k + 1}. {s}" for k, s in enumerate(steps)))

    i, replans = 0, 0
    while i < len(steps):
        print(f"\n== step {i + 1}/{len(steps)}: {steps[i]}")
        ok, info = run_step(task, steps, i)
        if ok:
            print(f"   verified on screen: {info!r}")
            i += 1
            continue

        print(f"   step failed: {info}")
        replans += 1
        if replans > MAX_REPLANS:
            print(
                f"FAILED: gave up after {MAX_REPLANS} replans. Last reason: {info}")
            trace(event="failed", reason=info)
            return
        title, raw, shown = observe(task)
        new = plan(task, steps[:i], fmt(title, shown, {}), info)
        if not new:
            print("FAILED: replanning returned nothing")
            return
        steps = steps[:i] + new
        print("REPLAN:\n" +
              "\n".join(f"  {k + 1}. {s}" for k, s in enumerate(steps) if k >= i))

    print("\nDONE: all steps verified on screen")
    trace(event="done")


if __name__ == "__main__":
    run(" ".join(sys.argv[1:]) or input("Task: "))
