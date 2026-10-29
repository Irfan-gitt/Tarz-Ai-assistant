import keyboard
import threading
from datetime import datetime
import re
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from typing import Annotated
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict
import sys  # noqa
import os  # noqa

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa
from langsmith import traceable
from config_settings.guardrails import Guardrails, jev_ask, restore
from config_settings.jev_gate import jev_gate
from config_settings.llm_gateway import get_gateway, AllModelsFailed
from config_settings.config import TOOL_CALLING_LLMS, PLAIN_LLMS
from Prompts.prompt import SYSTEM
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.tools import tool
from Tools.gmail import send_email, read_emails, search_emails
from Tools.calendar_tool import create_event, list_events, delete_event
from Tools.real_time_data import rt_data
from Tools.Dedicated_Tools.spotify_gui import spotify_play_song, spotify_play_playlist
from Tools.Dedicated_Tools.telegram_gui import telegram_message_user
from Tools.Dedicated_Tools.whatsapp_gui import whatsapp_send_message, whatsapp_end_call, whatsapp_call_contact
from Tools.Dedicated_Tools.discord_gui import discord_send_message, discord_toggle_mute, discord_toggle_deafen, discord_answer_call, discord_decline_call
from Tools.Dedicated_Tools.browser_gui import browser_open_url
from Actions.execute_action import click, type_text, press_key, read_screen, open_app, volume_control, wether_app, use_shortcut, set_alarm, news_update, set_timer, translate, clipboard, wait
from Memory_and_Retrieval.memory import save_imp_context_background, mark_followups_asked
from Memory_and_Retrieval.rag import build_memory_context
from Tools.media_control import media_play_pause, media_next_track, media_previous_track
from Audio.stt import live_listen
from Audio.wake_word import wait_for_wake_word, play_done_chime, wake_hotkey_event

from Tools.cancel_state import cancel_event, TaskCancelled, check_cancel
from Audio.tts import speak
from Audio.orb_overlay import get_orb

os.environ.setdefault("LANGSMITH_HIDE_INPUTS", "false")
os.environ.setdefault("LANGSMITH_HIDE_OUTPUTS", "false")

VOICE_MODE = False  # True = wake word + microphone + speaker, False = type in the terminal
WINDOW = 8  # how many recent messages every node sees


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------
@tool
def web_agent(task: str, url: str = "", file_hint: str = "") -> str:
    """Do a task on websites in the Brave browser like a human: search, click, read, fill forms, download. For a download give file_hint (part of the file name). Returns DONE or FAILED with the reason."""
    try:
        from Tools.brave_agent import run
    except ImportError as e:
        return f"Error: brave_agent.py is not importable ({e}). Put it in Tools/."
    return run(task, url, file_hint)


@tool
def ask_user(question: str, pending: str) -> str:
    """Ask the user for a missing detail instead of guessing (song name, contact, message text). question = what to ask. pending = one short line: the task and what you still need."""
    return question


ALL_TOOLS = [
    click, type_text, press_key, open_app, use_shortcut, read_screen, clipboard, wait,
    volume_control, set_alarm, set_timer,
    send_email, read_emails, search_emails,
    create_event, list_events, delete_event,
    wether_app, rt_data, translate,
    media_play_pause, media_previous_track, media_next_track,
    spotify_play_song, spotify_play_playlist,
    whatsapp_send_message, whatsapp_call_contact, whatsapp_end_call,
    discord_send_message, discord_toggle_mute, discord_toggle_deafen, discord_answer_call, discord_decline_call,
    browser_open_url, news_update,
    telegram_message_user,
]
ORCH_TOOLS = ALL_TOOLS + [web_agent, ask_user]
TOOLS_BY_NAME = {t.name: t for t in ORCH_TOOLS}

RISKY_TOOLS = {
    "delete_event", "send_email", "whatsapp_send_message", "whatsapp_call_contact",
    "discord_send_message", "telegram_message_user",
}
RISKY_WORDS = re.compile(
    r"\b(download|delete|remove|buy|purchase|pay|send|submit|post|install|transfer)\b", re.I)


def risk_reason(name: str, args: dict, flagged: bool) -> str:
    """Why this step needs the user's OK ('' = no need)."""
    if flagged:
        return "the message looked suspicious"
    if name in RISKY_TOOLS:
        return "it sends or deletes something"
    if name == "web_agent" and RISKY_WORDS.search(str(args.get("task", ""))):
        return "it is a browser task with a risky step"
    return ""


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
class TarzState(TypedDict):
    # saved thread: your messages and TARZ's final replies only
    messages: Annotated[list, add_messages]
    pii_map: dict[str, str]
    blocked: bool
    flagged: bool
    new_session: bool  # first message after the wake word: bring up due follow-ups
    memory_text: str
    followup_ids: list[str]
    route: str  # "chat" | "action"
    # the question TARZ asked last turn, so a short answer ("sailor song") continues the task
    pending: str
    next_pending: str
    tasks: list[dict]
    task_idx: int
    results: list[dict]
    reply: str


# FIX 0: same vault as memory.py, so tags (<PHONE_1> etc.) never collide across sessions
guard = Guardrails(ask=jev_ask)   # session-only, tags live in pii_map (RAM)
TOOL_BLOCKED_NOTE = "[tool output blocked: it looked like a prompt injection]"


def extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return str(content)


def window(messages, n: int = WINDOW) -> list:
    """Last n messages as plain text. Starts with the user and carries what TARZ did, so 'what did you just play?' works."""
    msgs = [m for m in messages if isinstance(
        m, (HumanMessage, AIMessage))][-n:]
    while msgs and not isinstance(msgs[0], HumanMessage):
        msgs = msgs[1:]
    out = []
    for m in msgs:
        if isinstance(m, AIMessage):
            log = m.additional_kwargs.get("task_log")
            out.append(AIMessage(content=extract_text(m.content) +
                       (f"\n[actions done: {log}]" if log else "")))
        else:
            out.append(HumanMessage(content=extract_text(m.content)))
    return out


def now_text() -> str:
    return datetime.now().strftime("%A, %d %B %Y, %I:%M %p") + " (Asia/Kolkata)"


def memory_part(state: TarzState) -> str:
    return f"\n\n{state['memory_text']}" if state.get("memory_text") else ""


def pending_part(state: TarzState) -> str:
    if not state.get("pending"):
        return ""
    return f"\n\nTARZ asked the user earlier: {state['pending']}\nThe user's new message is probably the answer."


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------
def guardrail_node(state: TarzState) -> dict:
    last = next(m for m in reversed(
        state["messages"]) if isinstance(m, HumanMessage))
    r = guard.check(last.content, state.get("pii_map"))
    out = {"pii_map": r.pii_map, "blocked": not r.allowed,
           "flagged": r.verdict == "suspicious"}
    if not r.allowed:
        out["messages"] = [AIMessage(content=r.reply)]
    elif r.text != last.content:
        out["messages"] = [HumanMessage(content=r.text, id=last.id)]
    return out


def context_node(state: TarzState) -> dict:
    """One memory lookup per turn. Every node below reuses it."""
    users = [m.content for m in state["messages"]
             if isinstance(m, HumanMessage)][-3:]
    ctx = build_memory_context(
        users, include_followups=state.get("new_session", False))
    return {"memory_text": ctx.text, "followup_ids": ctx.followup_ids}


def gate_node(state: TarzState) -> dict:
    if state.get("pending"):  # TARZ asked something: the answer continues the task, no guessing needed
        return {"route": "action"}
    prev = window(state["messages"])[:-1][-4:]
    recent = " | ".join(
        f"{'user' if isinstance(m, HumanMessage) else 'tarz'}: {m.content[:150]}" for m in prev)
    try:
        label = jev_gate(window(state["messages"])[-1].content, recent, "")
    except Exception as e:
        print(f"[gate] Jev failed, using the action path: {e}")
        label = "action"
    return {"route": label if label in ("chat", "action") else "action"}


CHAT_RULES = """

You are in chat mode and have no tools.
- If the user clearly wants you to DO something on their computer, reply with exactly [needs_action] and nothing else.
- If you ask the user something that you need to continue a task (for example which song to play), end your message with [pending: the task and what you need]."""


def chat_node(state: TarzState) -> dict:
    system = SYSTEM + f"\n\nCurrent date and time: {now_text()}" + memory_part(
        state) + pending_part(state) + CHAT_RULES
    try:
        text = extract_text(get_gateway().invoke(PLAIN_LLMS, [SystemMessage(
            content=system)] + window(state["messages"])).content)
    except AllModelsFailed as e:
        print(f"[chat] {e}")
        return {"reply": "I'm having trouble reaching my language models right now."}
    if "[needs_action]" in text:
        return {"route": "action", "reply": ""}
    pend = re.search(r"\[pending:\s*(.+?)\]", text, re.S)
    clean = re.sub(r"\[pending:.*?\]", "", text, flags=re.S).strip()
    return {"reply": clean, "next_pending": pend.group(1).strip() if pend else ""}


ORCH_PROMPT = """You are the action planner of TARZ, a desktop assistant. The user wants something done on their computer.
Call the tools that are needed, in the order they must happen. One call per separate task.
- Prefer a dedicated tool (spotify_*, whatsapp_*, discord_*, telegram_*, email, calendar...) over web_agent or click/type.
- Use web_agent for anything on a website (search, read, fill a form, download). For a download set file_hint.
- If a needed detail is missing (song name, contact, message text), call ask_user. Do not guess.
- If nothing needs to be done, just answer in text.
- Values like <PHONE_1> are private. Pass them through unchanged."""


def orchestrator_node(state: TarzState) -> dict:
    system = ORCH_PROMPT + \
        f"\n\nCurrent date and time: {now_text()}" + \
        memory_part(state) + pending_part(state)
    try:
        resp = get_gateway().invoke(TOOL_CALLING_LLMS, [SystemMessage(
            content=system)] + window(state["messages"]), tools=ORCH_TOOLS)
    except AllModelsFailed as e:
        print(f"[orchestrator] {e}")
        return {"reply": "I'm having trouble reaching my language models right now."}
    calls = list(getattr(resp, "tool_calls", None) or [])
    ask = next((c for c in calls if c["name"] == "ask_user"), None)
    if ask:
        return {"reply": ask["args"].get("question", ""), "next_pending": ask["args"].get("pending", "")}
    tasks = [{"name": c["name"], "args": c["args"]}
             for c in calls if c["name"] in TOOLS_BY_NAME]
    if tasks:
        return {"tasks": tasks, "task_idx": 0, "results": []}
    return {"reply": extract_text(resp.content) or "Done."}


def looks_ok(out) -> bool:
    return not str(out).strip().lower().startswith(("error", "failed", "could not", "cannot"))


def run_task_node(state: TarzState) -> dict:
    """One task per visit, so an approval pause never repeats a task that already ran."""
    check_cancel()
    i, tasks = state["task_idx"], state["tasks"]
    call = tasks[i]
    args = restore(call["args"], state.get("pii_map", {}))
    why = risk_reason(call["name"], args, state.get("flagged", False))
    if why:
        decision = interrupt({"type": "approval", "step": f"{i + 1}/{len(tasks)}",
                              "task": {"tool": call["name"], "args": args, "why": why}})
        if not decision.get("approved"):
            return {"results": state["results"] + [{"tool": call["name"], "ok": False, "detail": "the user said no"}],
                    "task_idx": len(tasks)}
    try:
        out = TOOLS_BY_NAME[call["name"]].invoke(args)
        ok = looks_ok(out)
    except TaskCancelled:
        raise
    except Exception as e:
        out, ok = f"{type(e).__name__}: {e}", False

    # FIX 1: the LLM must never see raw phones/emails from tool output.
    # check() masks first (so a number is never cut in half), then scans for injection.
    # Truncate only AFTER masking. The new tags go back into pii_map so the final reply can restore them.
    r = guard.check(str(out), state.get("pii_map", {}))
    detail = r.text[:400] if r.allowed else TOOL_BLOCKED_NOTE
    # store the MASKED args (call["args"]), so real values never sit in graph state
    result = {"tool": call["name"],
              "args": call["args"], "ok": ok, "detail": detail}
    # stop at the first failure
    return {"results": state["results"] + [result],
            "task_idx": i + 1 if ok else len(tasks),
            "pii_map": r.pii_map}


def respond_node(state: TarzState) -> dict:
    lines = [f"{n}. {r['tool']} -> {'OK' if r['ok'] else 'FAILED'}: {r['detail']}" for n,
             r in enumerate(state["results"], 1)]
    skipped = [t["name"] for t in state["tasks"][len(state["results"]):]]
    log = "\n".join(lines) + \
        (f"\nNot done: {', '.join(skipped)}" if skipped else "")
    system = (SYSTEM + f"\n\nCurrent date and time: {now_text()}" + memory_part(state)
              + f"\n\nTASK RESULTS (what TARZ just did, in order):\n{log}\n\n"
              "Write TARZ's reply: short and natural. Say what worked and what did not. Do not invent results.")
    try:
        text = extract_text(get_gateway().invoke(PLAIN_LLMS, [SystemMessage(
            content=system)] + window(state["messages"])).content)
    except AllModelsFailed:
        text = "Finished: " + \
            ", ".join(
                f"{r['tool']} {'ok' if r['ok'] else 'failed'}" for r in state["results"])
    return {"reply": text}


def finish_node(state: TarzState) -> dict:
    log = "; ".join(
        f"{r['tool']} {'ok' if r['ok'] else 'FAILED'}" for r in state.get("results", []))
    reply = state.get("reply") or "Done."
    return {"messages": [AIMessage(content=reply, additional_kwargs={"task_log": log} if log else {})],
            "pending": state.get("next_pending", ""), "next_pending": "", "tasks": [], "results": []}


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------
def after_guardrails(state: TarzState) -> str:
    return "end" if state.get("blocked") else "context"


def after_gate(state: TarzState) -> str:
    return state["route"]


def after_chat(state: TarzState) -> str:
    return "orchestrator" if state.get("route") == "action" else "finish"


def after_orchestrator(state: TarzState) -> str:
    return "run_task" if state.get("tasks") else "finish"


def after_task(state: TarzState) -> str:
    return "run_task" if state["task_idx"] < len(state["tasks"]) else "respond"


graph = StateGraph(TarzState)
for name, fn in [("guardrails", guardrail_node), ("context", context_node), ("gate", gate_node), ("chat", chat_node),
                 ("orchestrator", orchestrator_node), ("run_task",
                                                       run_task_node), ("respond", respond_node),
                 ("finish", finish_node)]:
    graph.add_node(name, fn)
graph.add_edge(START, "guardrails")
graph.add_conditional_edges("guardrails", after_guardrails, {
                            "end": END, "context": "context"})
graph.add_edge("context", "gate")
graph.add_conditional_edges(
    "gate", after_gate, {"chat": "chat", "action": "orchestrator"})
graph.add_conditional_edges(
    "chat", after_chat, {"orchestrator": "orchestrator", "finish": "finish"})
graph.add_conditional_edges("orchestrator", after_orchestrator, {
                            "run_task": "run_task", "finish": "finish"})
graph.add_conditional_edges("run_task", after_task, {
                            "run_task": "run_task", "respond": "respond"})
graph.add_edge("respond", "finish")
graph.add_edge("finish", END)

app = graph.compile(checkpointer=MemorySaver())
CONFIG = {"configurable": {"thread_id": "user"}}

if os.getenv("TARZ_DRAW_GRAPH"):  # needs internet, so only when you ask for it
    with open("langgraph.png", "wb") as f:
        f.write(app.get_graph().draw_mermaid_png())


# --------------------------------------------------------------------------
# Cancel and wake hotkeys
# --------------------------------------------------------------------------
def _on_cancel_hotkey():
    if not cancel_event.is_set():
        print("\n🛑 Cancel requested (Ctrl+Space)")
        cancel_event.set()


keyboard.add_hotkey("ctrl+space", _on_cancel_hotkey)


def _on_wake_hotkey():
    if not wake_hotkey_event.is_set():
        print("\n⌨️  Wake hotkey pressed")
        wake_hotkey_event.set()


keyboard.add_hotkey("ctrl+shift+space", _on_wake_hotkey)


# --------------------------------------------------------------------------
# One turn
# --------------------------------------------------------------------------
def ask_approval(payload: dict) -> bool:
    """Terminal version of the approval card. The GUI will replace this."""
    t = payload["task"]
    print(
        f"\n⚠️  Step {payload['step']}: {t['tool']} {t['args']}  ({t['why']})")
    return input("Approve? (y/n): ").strip().lower().startswith("y")


def _fallback_chat(user_input: str) -> str:
    # FIX 2: mask + injection-check the input, reuse the saved pii_map, restore the reply
    try:
        state = app.get_state(CONFIG).values
        history = state.get("messages", [])
        pii_map = state.get("pii_map", {})
    except Exception:
        history, pii_map = [], {}
    r = guard.check(user_input, pii_map)
    if not r.allowed:
        return r.reply
    msgs = window(history)
    if not msgs or extract_text(msgs[-1].content) != r.text:
        msgs.append(HumanMessage(content=r.text))
    try:
        text = extract_text(get_gateway().invoke(
            PLAIN_LLMS, [SystemMessage(content=SYSTEM)] + msgs).content)
        return restore(text, r.pii_map)
    except AllModelsFailed as e:
        print(f"[think] plain chat fallback failed: {e}")
        return "Something broke on my end, mind trying that again?"


@traceable(name="tarz_request", run_type="chain", metadata={"application": "tarz-ai", "workflow": "langgraph"})
def think(user_input: str, new_session: bool = False, approve=ask_approval) -> str:
    followup_ids: list[str] = []
    try:
        result = app.invoke({
            "messages": [HumanMessage(content=user_input)],
            "blocked": False, "flagged": False, "new_session": new_session, "route": "",
            "next_pending": "", "tasks": [], "task_idx": 0, "results": [], "reply": "",
        }, config=CONFIG)
        while result.get("__interrupt__"):
            payload = result["__interrupt__"][0].value
            result = app.invoke(
                Command(resume={"approved": approve(payload)}), config=CONFIG)
        followup_ids = result.get("followup_ids", [])
        response = restore(extract_text(
            result["messages"][-1].content), result.get("pii_map", {}))

    except TaskCancelled:
        cancel_event.clear()
        get_orb().hide()
        response = "Okay, stopped."

    except Exception as e:
        print(f"[think] graph failed, falling back to plain chat: {e}")
        response = _fallback_chat(user_input)

    if followup_ids and "?" in response:  # TARZ really asked, so do not ask again
        mark_followups_asked(followup_ids)
    save_imp_context_background(user_input, response)
    return response


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------
def _voice_loop():
    while True:
        try:
            wait_for_wake_word()
            user_input = live_listen()
        except EOFError:
            break
        if not user_input:
            continue
        response = think(user_input, new_session=True)
        print("TARZ:", response)
        speak(response)
        play_done_chime()

        while True:
            try:
                user_input = live_listen(timeout=10.0)
            except EOFError:
                get_orb().hide()
                return
            if not user_input:
                break
            response = think(user_input)
            print("TARZ:", response)
            speak(response)
            play_done_chime()
        get_orb().hide()  # back to silent wake-word-only mode


def _typed_loop():
    first = True
    while True:
        try:
            user_input = input("You: ").strip()
        except EOFError:
            break
        if not user_input:
            continue
        print("TARZ:", think(user_input, new_session=first))
        first = False


def main():
    _voice_loop() if VOICE_MODE else _typed_loop()


if __name__ == "__main__":
    main()
