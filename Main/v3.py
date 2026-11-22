from langchain_core.messages import ToolMessage
from dotenv import load_dotenv

import keyboard
import threading
from datetime import datetime
import re
import json
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

from config_settings.guardrails import Guardrails, jev_ask, restore, HARD, PII_RULES, TAG
from config_settings.jev_gate import jev_gate
from config_settings.llm_gateway import get_gateway, AllModelsFailed
from config_settings.config import (
    TOOL_CALLING_LLMS, PLAIN_LLMS, VISION_LLMS, CLASSIFY_LLMS,
    require_core_chat_provider,
)
from Prompts.tool_prompt import SINGLE_PROMPT, DEDICATED_PROMPT, AUTONOMOUS_PROMPT
from Prompts.prompt import SYSTEM
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.tools import tool

from Tools.gmail import send_email, read_emails, search_emails
from Tools.calendar_tool import create_event, list_events, delete_event
from Tools.real_time_data import rt_data
from Tools.Dedicated_Tools.spotify_gui import spotify_play_playlist, spotify_search_and_play
from Tools.Dedicated_Tools.telegram_gui import telegram_message_user
from Tools.Dedicated_Tools.whatsapp_gui import whatsapp_send_message, whatsapp_call
from Tools.Dedicated_Tools.discord_gui import discord_send_message, discord_toggle_mute, discord_toggle_deafen, discord_answer_call, discord_decline_call
from Tools.scheduler import schedule_task, list_scheduled, cancel_scheduled, start as start_scheduler

from Tools.Autonomous_layer.autonomous_agent import computer_agent, set_human_hooks
from Tools.research_tool import research as research_tool

from langchain_core.tools import BaseTool, StructuredTool

from Actions.execute_action import click, type_text, press_key, read_screen, open_app, volume_control, wether_app, use_shortcut, set_alarm, news_update, set_timer, translate, clipboard, wait
from Memory_and_Retrieval.memory import (
    save_imp_context_background, mark_followups_asked, get_due_followups,
    has_pending_followups,
)
from Memory_and_Retrieval.rag import build_memory_context, followup_question
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
load_dotenv()

guard = Guardrails(ask=jev_ask)
session_map: dict[str, str] = {}

# NEW: scheduler plumbing
graph_lock = threading.Lock()   # a user turn and a scheduled turn must never overlap
_local = threading.local()      # _local.unattended = True while a scheduled job runs
GRAPH = None


def _unattended() -> bool:
    return getattr(_local, "unattended", False)


def _approved() -> bool:
    """True only inside a scheduled job whose risky actions the user approved at schedule time."""
    return _unattended() and getattr(_local, "approved", False)


# ---------------- STATE ----------------
class State(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    user_text: str
    blocked: bool
    memory_ctx: str
    followup_ids: list
    followups: list
    followup_shown_ids: list
    handoff_data: str
    handoffs: list
    plan: list
    step_idx: int
    route: str
    task: str
    results: list
    last_result: str
    last_ok: bool
    final: str        # masked reply: memory reads this
    spoken: str       # real values: screen and voice read this


# ---------------- TOOLS ----------------
@tool
def ask_user(question: str, pending: str) -> str:
    """Ask the user for a missing detail instead of guessing (song name, contact, message text). question = what to ask. pending = one short line: the task and what you still need."""
    return question


AUTONOMOUS_TOOLS = [computer_agent]

SINGLE_TOOLS = [
    click, type_text, press_key, open_app, use_shortcut, read_screen, clipboard, wait,
    volume_control, set_alarm, set_timer,
    send_email, read_emails, search_emails,
    create_event, list_events, delete_event,
    wether_app, rt_data, translate, news_update,
    media_play_pause, media_previous_track, media_next_track,
    schedule_task, list_scheduled, cancel_scheduled,
    research_tool,
]

DEDICATED_TOOLS = [
    spotify_play_playlist, spotify_search_and_play,
    whatsapp_send_message, whatsapp_call,
    discord_send_message, discord_toggle_mute, discord_toggle_deafen,
    discord_answer_call, discord_decline_call,
    telegram_message_user,
]


def _as_tool(t):
    """Plain functions become real tools, so .name and .invoke() exist everywhere."""
    if isinstance(t, BaseTool):
        return t
    return StructuredTool.from_function(
        t, name=t.__name__, description=(t.__doc__ or t.__name__).strip())


AUTONOMOUS_TOOLS = [_as_tool(t) for t in AUTONOMOUS_TOOLS]
SINGLE_TOOLS = [_as_tool(t) for t in SINGLE_TOOLS]
DEDICATED_TOOLS = [_as_tool(t) for t in DEDICATED_TOOLS]
# ---------------- HUMAN IN THE LOOP (shared) ----------------
YES = {"y", "yes", "yeah", "yep", "ok", "sure", "do it"}
_UI_ASK_HOOK = None
_UI_CONFIRM_HOOK = None
PRINT_OUTPUT = True


def set_ui_hooks(ask=None, confirm=None):
    """Route questions and confirmations to a desktop UI when one is present."""
    global _UI_ASK_HOOK, _UI_CONFIRM_HOOK
    _UI_ASK_HOOK, _UI_CONFIRM_HOOK = ask, confirm


def ask_human(question: str) -> str:
    if _unattended():  # NEW: nobody is there to answer a scheduled job
        return "Nobody is available to answer. Use your best judgment or stop."
    if _UI_ASK_HOOK is not None:
        return _UI_ASK_HOOK(question)
    if VOICE_MODE:
        speak(question)
        return live_listen()
    return input(f"TARZ: {question}\nYou: ")


def human_in_the_loop(question: str) -> bool:
    if _unattended():  # NEW: nobody can answer live; named risky tools use _approved() instead
        return False
    if _UI_CONFIRM_HOOK is not None:
        return bool(_UI_CONFIRM_HOOK(question))
    return ask_human(f"{question} (yes/no)").strip().lower() in YES


def _visible_text(content) -> str:
    """Keep only readable text from provider content blocks, excluding signatures/reasoning."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, (list, tuple)):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif getattr(block, "type", None) in {"text", "output_text"}:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(part for part in parts if part).strip()


def _replayable_ai_message(message):
    """Replay tool calls without provider-private reasoning/signature metadata."""
    kwargs = {"content": _visible_text(message.content),
              "tool_calls": getattr(message, "tool_calls", None) or []}
    if getattr(message, "id", None):
        kwargs["id"] = message.id
    return AIMessage(**kwargs)


# ---------------- GUARDRAILS (input) ----------------
def guardrails_input(user_text: str):
    global session_map
    result = guard.check(user_text, session_map)
    session_map = result.pii_map
    return result


def guard_in(state: State) -> dict:
    result = guardrails_input(state["user_text"])
    reset = {"plan": [], "step_idx": 0, "results": [], "final": "", "task": "",
             "last_ok": True, "last_result": "", "memory_ctx": "", "followup_ids": [],
             "followups": [], "followup_shown_ids": [], "handoff_data": "", "handoffs": []}
    if not result.allowed:
        return {**reset, "blocked": True, "final": result.reply, "spoken": result.reply}
    return {**reset, "blocked": False, "messages": [HumanMessage(content=result.text)]}


# ---------------- RETRIEVAL ----------------
def retrieval(state: State) -> dict:
    users = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    # Event reminders are checked on every turn; the UI also polls between turns.
    # Keep their delivery deterministic in gaurdrails_output, rather than hoping
    # the chat model notices and asks the question itself.
    ctx = build_memory_context(users, include_followups=False)
    followups = get_due_followups(limit=1)
    return {"memory_ctx": ctx.text,
            "followup_ids": [item["id"] for item in followups],
            "followups": followups}


# ---------------- CHAT ----------------
def chat(state: State) -> dict:
    # read-only info tools the chat model may call to answer with live data
    CHAT_TOOL_NAMES = {"rt_data", "wether_app", "news_update", "translate",
                       "list_events", "read_emails", "search_emails", "read_screen"}
    chat_tools = [t for t in SINGLE_TOOLS if t.name in CHAT_TOOL_NAMES]
    tool_map = {t.name: t for t in chat_tools}

    now = datetime.now().strftime("%A, %d %B %Y, %I:%M %p")
    ctx = state.get("memory_ctx", "")
    sys_msg = SystemMessage(
        content=(f"{SYSTEM}\n\n{ctx}\n\n" if ctx else f"{SYSTEM}\n\n")
        + "You may call the info tools (weather, real-time data, news, translate, "
          "calendar/email reading) when the user asks about live or personal data. "
          "Never guess live data, call the tool. Don't call tools for plain conversation.\n"
        + f"Current date/time: {now}")

    local = [sys_msg, *state["messages"][-WINDOW:]]

    for _ in range(3):  # max tool rounds for chat
        check_cancel()
        try:
            # Chat needs natural responses, but its read-only info tools remain available.
            # The plain model configs use a higher temperature than the tool-agent configs,
            # so this handles conversation and tool calls in the same request path.
            ai = get_gateway().invoke(PLAIN_LLMS, local, tools=chat_tools)
        except AllModelsFailed:
            return {"last_result": "All my models are down right now, try again in a bit.",
                    "last_ok": False}

        calls = getattr(ai, "tool_calls", None) or []
        if not calls:
            return {"last_result": _visible_text(ai.content) or "I couldn't get a text response.", "last_ok": True}

        local.append(_replayable_ai_message(ai))
        for call in calls:
            check_cancel()
            tool_fn = tool_map.get(call["name"])
            if tool_fn is None:
                result = f"Error: tool '{call['name']}' is not available in chat."
            else:
                try:
                    result = tool_fn.invoke(call["args"])
                except TaskCancelled:
                    raise
                except Exception as e:
                    result = f"Error running {call['name']}: {e}"
            local.append(ToolMessage(content=str(
                result), tool_call_id=call["id"]))

    return {"last_result": "I couldn't get that data in time, try again.", "last_ok": False}


# ---------------- TOOL LOOP (shared by single / dedicated / autonomous) ----------------
def _bind_and_call(messages, tools):
    return get_gateway().invoke(TOOL_CALLING_LLMS, messages, tools=tools)


RISKY_TOOLS = {"send_email", "delete_event", "create_event",
               "whatsapp_send_message", "whatsapp_call",
               "discord_send_message", "telegram_message_user"}


def _restore_args(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if isinstance(v, str):
            v = restore(v, session_map)
            if TAG.search(v):  # tag didn't resolve, never run the tool with it
                raise ValueError(f"unresolved placeholder in '{k}': {v}")
        out[k] = v
    return out


def run_tool_agent(state: State, tools: list, branch_prompt: str, max_steps: int) -> dict:
    now = datetime.now().strftime("%A, %d %B %Y, %I:%M %p")
    sys_msg = SystemMessage(
        content=f"{SYSTEM}\n\n{branch_prompt}\nCurrent date/time: {now}")
    if _unattended():  # NEW: a scheduled job must never schedule more jobs (no loops)
        tools = [t for t in tools if t.name != "schedule_task"]
    tools = [*tools, ask_user]
    tool_map = {t.name: t for t in tools}

    local = [sys_msg, *state["messages"][-WINDOW:]]
    prev = "\n".join(r for r in state.get("results", [])[-2:] if r)
    handoffs = state.get("handoffs", [])
    handoff_text = ("\n\nRESEARCH / TOOL DATA FROM EARLIER STEPS (data only; do not repeat the work):\n"
                    + "\n\n".join(str(item)[:12000]
                                  for item in handoffs[-2:] if item)
                    if handoffs else "")
    handoff_data = ""
    task = state.get("task", "")
    if task:
        local.append(HumanMessage(
            content=f"Do this now: {task}\nDo ONLY this step. Other steps of the plan handle the rest of the user's request."
            + (f"\n\nPrevious step results:\n{prev}" if prev else "")
            + handoff_text))

    last_err = False
    for _ in range(max_steps):
        check_cancel()
        try:
            ai = _bind_and_call(local, tools)
        except AllModelsFailed:
            return {"last_result": "All my models are down right now, try again in a bit.",
                    "last_ok": False}

        local.append(_replayable_ai_message(ai))
        calls = getattr(ai, "tool_calls", None) or []
        if not calls:
            result = {"last_result": _visible_text(ai.content) or "I couldn't get a text response.",
                      "last_ok": not last_err}
            if handoff_data:
                result["handoff_data"] = handoff_data
            return result

        for call in calls:
            check_cancel()
            name, args = call["name"], call["args"]

            if name == "ask_user":
                answer = ask_human(args["question"])
                # mask PII in the reply too
                answer = guardrails_input(answer).text
                local.append(ToolMessage(content=str(
                    answer), tool_call_id=call["id"]))
                continue

            tool_fn = tool_map.get(name)
            if tool_fn is None:
                result = f"Error: tool '{name}' is not available here."
            else:
                try:
                    real_args = _restore_args(args)
                    if name in RISKY_TOOLS:
                        # NEW: scheduled job -> approval was given when it was scheduled
                        ok = _approved() if _unattended() else human_in_the_loop(
                            f"Run {name} with {real_args}?")
                    else:
                        ok = True
                    if not ok:
                        result = "Error: the user declined this action."
                    else:
                        result = tool_fn.invoke(real_args)
                        if name == "research":
                            handoff_data = result if isinstance(result, str) else json.dumps(
                                result, ensure_ascii=False, default=str)
                except TaskCancelled:
                    raise
                except Exception as e:
                    result = f"Error running {name}: {e}"

            last_err = (str(result).startswith("Error")
                        or (isinstance(result, dict)
                            and result.get("status") in ("failed", "aborted")))
            if name == "research" and isinstance(result, str):
                try:
                    last_err = last_err or bool(
                        json.loads(result).get("error"))
                except (TypeError, ValueError):
                    pass
            local.append(ToolMessage(content=str(
                result), tool_call_id=call["id"]))

    return {"last_result": "I hit my step limit before finishing.", "last_ok": False}


def single_tool(state: State) -> dict:
    tools = SINGLE_TOOLS
    task = state.get("task", "")
    # A research step may ONLY research. With GUI tools in reach it kept going and tried to do the rest of the
    # user's request (open Excel, click, type) by itself. Later plan steps do that work.
    if re.search(r"\bresearch\b", task, re.I) and "schedule_task" not in task:
        tools = [t for t in SINGLE_TOOLS if t.name ==
                 "research"] or SINGLE_TOOLS
    return run_tool_agent(state, tools, SINGLE_PROMPT, max_steps=5)


def tools_dedicated(state: State) -> dict:
    return run_tool_agent(state, DEDICATED_TOOLS, DEDICATED_PROMPT, max_steps=8)


def agent_autonomous(state: State) -> dict:
    return run_tool_agent(state, AUTONOMOUS_TOOLS, AUTONOMOUS_PROMPT, max_steps=5)


# ---------------- ORCHESTRATOR (the hub) ----------------
ROUTES = {"chat", "single", "dedicated", "autonomous"}
MAX_PLAN_STEPS = 4

PLANNER_PROMPT = """You are the planner for a desktop voice assistant. Reply with ONLY a JSON array:
[{"route": "chat|single|dedicated|autonomous", "task": "..."}]

Routes:
- chat: conversation, questions, opinions, anything needing no action. task = "".
- single: ONE tool call that finishes the job by itself: set an alarm or timer, send or read email, calendar,
  weather, news, translate, volume, media play/pause/next/previous, schedule/list/cancel a scheduled task,
  or research fresh web information.
- dedicated: actions using direct app tools: Spotify, WhatsApp, Telegram, Discord.
- autonomous: everything else. Anything that needs several clicks or typing, interactive work on a website
  or in a browser, downloads, filling forms, or any job no single or dedicated tool covers. Use research
  for web lookups and product comparisons; use the browser only when the user asks to interact with a site.

Rules:
- SCHEDULING HAS PRIORITY OVER APP ROUTING: if the user asks to do anything later, after a delay,
  at a future time, or on a repeating schedule, route it to SINGLE and use schedule_task. This applies
  even when the action mentions WhatsApp, Spotify, email, or another dedicated app. Never route a
  future action to a direct-send/play tool, and never offer to do it immediately instead.
- Treat follow-ups such as "schedule it", "after two minutes", or a corrected time as updates to the
  most recent unfinished scheduling request. Carry forward its action and details from conversation
  history; ignore an earlier assistant claim that scheduling is unavailable if it conflicts with this.
- NEVER plan low-level GUI actions (open an app, type text, press a key, click a button) as separate steps.
  If a job needs several UI actions, it is ONE autonomous step with the whole goal as the task.
- RESEARCH HANDOFF: when the user asks for fresh web research AND then wants a desktop/app action using those
  findings, plan two dependent steps: first SINGLE, call research with the user's query and the exact fields
  needed; then route the requested app work to AUTONOMOUS or DEDICATED as appropriate. Never send the computer agent to research the web.
  If the user only asks for research/results, use one SINGLE step with research and return its answer.
- A task like "download X from the official website using Brave" is exactly ONE autonomous step.
- One clear request = ONE step. Never split one app action ("play X on spotify" is ONE step).
- Use more than one step only for truly different jobs ("set a timer, then email Sam").
- For a future/repeating request, plan ONE single step whose task instructs the executor to call
  schedule_task with the complete action (without time words) and the requested time/repeat. Keep the
  time phrase exactly as the user said it. NEVER run the action now. Example:
  "Use schedule_task: task='Play Sailor Song on Spotify', when='11 pm'".
- Before scheduling, check all details required to perform the action later. For a message this means
  recipient AND exact message text; for music, song/playlist; for an event, title and time. If something
  is missing, the executor must call ask_user to ask only for the missing detail, then schedule after
  the user answers. Do not schedule an incomplete task and do not perform it immediately.
- Scheduling a risky action triggers an approval dialog in the app while the user is present. Let the
  scheduler tool request that approval; do not treat asking in chat as approval or claim it is scheduled
  until the tool confirms success. If approval is declined, report that it was not scheduled.
- Questions like "what is scheduled" or "cancel my 11 pm task" are ONE single step (list_scheduled / cancel_scheduled).
- Each task must be self-contained: include names, text, times, and the browser name if the user gave one.
  Keep placeholders like <EMAIL_1> exactly as written.
- Use the memory block to resolve things like "my usual playlist".
- Maximum 4 steps.

Examples:
user: download spotify from the official website in brave
[{"route": "autonomous", "task": "Download the Spotify installer from the official Spotify website using Brave"}]
user: play sailor song on spotify at 11 pm
[{"route": "single", "task": "Use schedule_task: task='Play Sailor Song on Spotify', when='11 pm'"}]
user: text Mummy on WhatsApp after 2 minutes
[{"route": "single", "task": "Schedule a WhatsApp message to Mummy after 2 minutes. Message text is missing: ask the user for the exact text, then call schedule_task. Do not send now."}]
user: in response to the pending request, say hey
[{"route": "single", "task": "Schedule the previously requested WhatsApp message to Mummy with exact text 'hey' after 2 minutes. Call schedule_task; do not send now."}]
user: schedule it for 11:32 instead
[{"route": "single", "task": "Update the time on the most recent unfinished scheduled request to 11:32, preserve its action and supplied details, ask for any still-missing required detail, then call schedule_task. Do not perform now."}]"""


def _make_plan(state: State) -> list:
    info = (f"\n\nSingle tools: {', '.join(t.name for t in SINGLE_TOOLS)}"
            f"\nDedicated tools: {', '.join(t.name for t in DEDICATED_TOOLS)}")
    ctx = state.get("memory_ctx", "")
    sys_msg = SystemMessage(content=PLANNER_PROMPT +
                            info + (f"\n\n{ctx}" if ctx else ""))
    try:
        raw = get_gateway().invoke(
            CLASSIFY_LLMS, [sys_msg, *state["messages"][-WINDOW:]]).content
        data = json.loads(re.search(r"\[.*\]", raw, re.S).group())
    except Exception:
        data = []

    plan = []
    for s in data[:MAX_PLAN_STEPS]:
        if isinstance(s, dict):
            route = s.get("route") if s.get("route") in ROUTES else "chat"
            plan.append({"route": route, "task": str(s.get("task") or "")})
    # planner failed: just talk
    plan = plan or [{"route": "chat", "task": ""}]
    print("[plan] " +
          " | ".join(f"{s['route']}: {s['task'][:70]}" for s in plan))
    return plan


def orchastration(state: State) -> dict:
    plan = state.get("plan")
    if not plan:                                        # new turn
        plan, idx, results = _make_plan(state), 0, []
        handoffs = []
    else:                                               # a branch just finished
        results = [*state.get("results", []), state.get("last_result", "")]
        handoffs = list(state.get("handoffs", []))
        if state.get("handoff_data"):
            handoffs.append(state["handoff_data"])
        idx = state["step_idx"] + 1
        # verify: failed -> stop, report it
        if not state.get("last_ok", True):
            idx = len(plan)

    if idx >= len(plan):
        final = "\n".join(r for r in results if r) or "Done."
        return {"plan": plan, "step_idx": idx, "results": results,
                "handoffs": handoffs, "handoff_data": "",
                "route": "end", "final": final}

    step = plan[idx]
    return {"plan": plan, "step_idx": idx, "results": results,
            "handoffs": handoffs, "handoff_data": "",
            "route": step["route"], "task": step["task"]}


# ---------------- GUARDRAILS (output) ----------------
_SECRET = dict(PII_RULES)["SECRET"]
SAFE_REPLY = "I can't say that one."


def _hard_hit(text: str) -> bool:
    return any(rx.search(text) for patterns in HARD.values() for rx in patterns)


def gaurdrails_output(state: State) -> dict:
    reply = state.get("final") or ""
    if not reply:
        return {}

    blocked = False
    # 1. secrets never leave
    reply = _SECRET.sub("[hidden]", reply)

    followups = state.get("followups") or []
    followup_shown_ids = []
    if followups:
        item = followups[0]
        question = followup_question(item)
        if question not in reply:
            reply = reply.rstrip() + "\n\n" + question
        followup_shown_ids = [item["id"]]

    if _hard_hit(reply):                                 # 2. free regex first
        labels = jev_ask(reply[:2000])                   # 3. jev only on a hit
        if labels is None or "attack" in labels.values():
            reply, blocked = SAFE_REPLY, True

    # 4. real values, screen/voice only
    spoken = restore(reply, session_map)
    spoken = TAG.sub("", spoken)                         # drop unresolved tags
    # a restored secret must not be spoken
    spoken = _SECRET.sub("[hidden]", spoken)

    # final stays masked for memory
    out = {"final": reply, "spoken": spoken,
           "followup_shown_ids": followup_shown_ids if not blocked else []}
    if blocked:
        # memory_extraction skips blocked turns
        out["blocked"] = True
    return out


# ---------------- MEMORY EXTRACTION ----------------
_last_save: threading.Thread | None = None
_MEMORY_SIGNAL = re.compile(
    r"\b(interview|exam|test|trip|travel|presentation|meeting|deadline|appointment|"
    r"wedding|birthday|graduation|competition|performance|conference|flight|vacation|holiday|date)\b|"
    r"\b(i am|i'm|i have|i've|my goal|my plan|i prefer|i like|i love|i hate|"
    r"i want to become|i work as|i study)\b", re.I)


def memory_extraction(state: State) -> dict:
    global _last_save
    if state.get("blocked") or _unattended():  # NEW: scheduled runs are not conversations
        return {}

    masked_reply = state.get("final") or ""
    masked_user = next((m.content for m in reversed(state["messages"])
                        if isinstance(m, HumanMessage)), "")
    if not masked_user or not masked_reply:
        return {}

    # The follow-up text is now in the assistant reply. Mark it asked before
    # starting background extraction so a quick user answer can close the loop.
    shown_ids = state.get("followup_shown_ids") or []
    if shown_ids:
        mark_followups_asked(shown_ids)

    # Skip routine single-tool commands, but keep personal facts and future
    # events even when the planner also routed the turn to a tool.
    routes = {p["route"] for p in state.get("plan", [])}
    if routes and routes == {"single"} and not has_pending_followups():
        if not _MEMORY_SIGNAL.search(masked_user):
            return {}

    _last_save = save_imp_context_background(masked_user, masked_reply)
    return {}


# ---------------- SPEAK ----------------
def speak_output(state: State) -> dict:
    text = state.get("spoken") or state.get("final") or "Done."
    if PRINT_OUTPUT:
        print(f"TARZ: {text}")
    if VOICE_MODE:
        speak(text)
    # history stays masked
    return {"messages": [AIMessage(content=state.get("final") or text)]}


# ---------------- GRAPH ----------------
def build_graph():
    g = StateGraph(State)

    for name, fn in [
        ("guard_in", guard_in),
        ("retrieval", retrieval),
        ("orchastration", orchastration),
        ("chat", chat),
        ("single_tool", single_tool),
        ("tools_dedicated", tools_dedicated),
        ("agent_autonomous", agent_autonomous),
        ("gaurdrails_output", gaurdrails_output),
        ("memory_extraction", memory_extraction),
        ("speak_output", speak_output),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "guard_in")
    g.add_conditional_edges("guard_in",
                            lambda s: "speak_output" if s["blocked"] else "retrieval",
                            ["speak_output", "retrieval"])
    g.add_edge("retrieval", "orchastration")
    g.add_conditional_edges("orchastration", lambda s: s["route"],
                            {"chat": "chat", "single": "single_tool",
                             "dedicated": "tools_dedicated",
                             "autonomous": "agent_autonomous",
                             "end": "gaurdrails_output"})
    for n in ("chat", "single_tool", "tools_dedicated", "agent_autonomous"):
        # the loop in the diagram
        g.add_edge(n, "orchastration")
    g.add_edge("gaurdrails_output", "memory_extraction")
    g.add_edge("memory_extraction", "speak_output")
    g.add_edge("speak_output", END)

    graph = g.compile()
    png = graph.get_graph().draw_mermaid_png()
    with open("langgraph.png", "wb") as f:
        f.write(png)
    print("✅ Graph saved as langgraph.png")

    return graph


# ---------------- SCHEDULED RUNS ----------------
def run_scheduled(task: str, approved: bool = False):
    """Called by the scheduler thread when a job is due: runs it like a fresh user request.
    approved = the user allowed risky actions (send/call/delete) when they scheduled it."""
    with graph_lock:
        _local.unattended = True
        _local.approved = approved
        cancel_event.clear()
        try:
            GRAPH.invoke({"user_text": task, "messages": []},
                         {"recursion_limit": 40})
        except TaskCancelled:
            print("TARZ: scheduled task cancelled.")
        except Exception as e:
            print(f"[scheduled error] {type(e).__name__}: {e}")
        finally:
            _local.unattended = False
            _local.approved = False


# ---------------- MAIN ----------------
def listen() -> str:
    if VOICE_MODE:
        wait_for_wake_word()
        return live_listen()
    return input("You: ")


def main():
    global GRAPH
    require_core_chat_provider()
    on = os.getenv("LANGSMITH_TRACING", "").lower(
    ) == "true" and bool(os.getenv("LANGSMITH_API_KEY"))
    print(
        f"LangSmith tracing: {'ON' if on else 'OFF'} | project: {os.getenv('LANGSMITH_PROJECT', 'default')}")

    set_human_hooks(confirm=human_in_the_loop,
                    ask=ask_human, cancel=cancel_event)
    GRAPH = build_graph()
    # NEW: after the graph exists, before the loop
    start_scheduler(run_scheduled, confirm=human_in_the_loop)
    history: list = []

    while True:
        try:
            text = listen()
        except (KeyboardInterrupt, EOFError):
            break
        if not text or not text.strip():
            continue
        if text.strip().lower() in {"exit", "quit"}:
            break

        cancel_event.clear()
        try:
            with graph_lock:  # NEW
                out = GRAPH.invoke({"user_text": text, "messages": history},
                                   {"recursion_limit": 40})
            history = out["messages"][-WINDOW:]
        except TaskCancelled:
            print("TARZ: cancelled.")
        except Exception as e:
            print(f"[error] {type(e).__name__}: {e}")

    if _last_save:                       # let the memory save finish before exiting
        _last_save.join(timeout=15)

    from langchain_core.tracers.langchain import wait_for_all_tracers
    wait_for_all_tracers()


if __name__ == "__main__":
    main()
