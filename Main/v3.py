from dotenv import load_dotenv

import threading
from datetime import datetime
import re
import json
from langgraph.graph import StateGraph, START, END
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

from config_settings.guardrails import Guardrails, jev_ask, restore, HARD, PII_RULES, TAG
from config_settings.llm_gateway import get_gateway, AllModelsFailed
from config_settings.config import (
    TOOL_CALLING_LLMS, PLAIN_LLMS, CLASSIFY_LLMS,
    require_core_chat_provider,
)
from Prompts.tool_prompt import SINGLE_PROMPT, DEDICATED_PROMPT, AUTONOMOUS_PROMPT
from Prompts.behave_prompt import BEHAVIOUR_PROMPT
from Prompts.prompt import SYSTEM
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool, tool

from Tools.Dedicated_Tools.gmail import send_email, read_emails, search_emails, check_job_updates
from Tools.calendar_tool import create_event, list_events, delete_event
from Tools.real_time_data import rt_data
from Tools.Dedicated_Tools.spotify_gui import spotify_play_playlist, spotify_search_and_play
from Tools.Dedicated_Tools.telegram_gui import telegram_message_user
from Tools.Dedicated_Tools.whatsapp_gui import whatsapp_send_message, whatsapp_call
from Tools.Dedicated_Tools.discord_gui import discord_send_message, discord_toggle_mute, discord_toggle_deafen, discord_answer_call, discord_decline_call
from Tools.Dedicated_Tools.file_manager import FILE_TOOLS, RISKY as FILE_MANAGER_RISKY
from Tools.scheduler import schedule_task, list_scheduled, cancel_scheduled, start as start_scheduler

from Tools.Autonomous_layer.autonomous_agent import computer_agent, set_human_hooks
from Tools.research_tool import research as research_tool

from Actions.execute_action import click, type_text, press_key, read_screen, open_app, volume_control, wether_app, use_shortcut, set_alarm, news_update, set_timer, translate, clipboard, wait
from Memory_and_Retrieval.memory import (
    save_imp_context_background, mark_followups_asked, get_due_followups,
    has_pending_followups,
)
from Memory_and_Retrieval.rag import build_memory_context, followup_question
from Tools.media_control import media_play_pause, media_next_track, media_previous_track
from Audio.stt import live_listen
from Audio.wake_word import wait_for_wake_word

from Tools.cancel_state import cancel_event, TaskCancelled, check_cancel
from Audio.tts import speak

os.environ.setdefault("LANGSMITH_HIDE_INPUTS", "false")
os.environ.setdefault("LANGSMITH_HIDE_OUTPUTS", "false")

VOICE_MODE = False  # True = wake word + microphone + speaker, False = type in the terminal
WINDOW = 8  # how many recent messages every node sees
PRINT_OUTPUT = True
load_dotenv()

guard = Guardrails(ask=jev_ask)
session_map: dict[str, str] = {}

# scheduler plumbing
graph_lock = threading.Lock()   # a user turn and a scheduled turn must never overlap
_local = threading.local()      # _local.unattended = True while a scheduled job runs
GRAPH = None


def _unattended() -> bool:
    return getattr(_local, "unattended", False)


def _approved() -> bool:
    """True only inside a scheduled job whose risky actions the user approved at schedule time."""
    return _unattended() and getattr(_local, "approved", False)


# ---------------- UI HOOKS (one registry) ----------------
_hooks = {"ask": None, "confirm": None, "progress": None}


def set_ui_hooks(ask=None, confirm=None):
    """Route questions and confirmations to a desktop UI when one is present."""
    _hooks["ask"], _hooks["confirm"] = ask, confirm


def set_progress_hook(hook=None):
    """Register a thread-safe UI callback for agent plan/tool progress."""
    _hooks["progress"] = hook


def _emit_progress(event: dict):
    hook = _hooks["progress"]
    if hook is not None:
        try:
            hook(dict(event))
        except Exception as exc:
            print(f"[ui] progress update failed: {exc}")


# ---------------- STATE ----------------
# A step's output is one dict: {"text": str, "ok": bool, "data": str}
#   text = what the user hears, ok = did it work, data = raw tool data handed to later steps
class State(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    user_text: str
    blocked: bool
    memory_ctx: str
    followups: list
    followup_shown_ids: list
    plan: list
    step_idx: int
    route: str
    task: str
    results: list     # finished steps (dicts as above)
    last: dict        # step that just finished
    final: str        # masked reply: memory reads this
    spoken: str       # real values: screen and voice read this


def _step(text: str, ok: bool = True, data: str = "") -> dict:
    return {"last": {"text": text, "ok": ok, "data": data}}


def last_user_text(state: State) -> str:
    return next((str(m.content) for m in reversed(state.get("messages", []))
                 if isinstance(m, HumanMessage)), "")


# ---------------- TOOLS ----------------
@tool
def ask_user(question: str, pending: str) -> str:
    """Ask the user for a missing detail instead of guessing (song name, contact, message text). question = what to ask. pending = one short line: the task and what you still need."""
    return question


def _as_tools(items):
    """Plain functions become real tools, so .name and .invoke() exist everywhere."""
    return [t if isinstance(t, BaseTool) else StructuredTool.from_function(
        t, name=t.__name__, description=(t.__doc__ or t.__name__).strip())
        for t in items]


AUTONOMOUS_TOOLS = _as_tools([computer_agent])

SINGLE_TOOLS = _as_tools([
    click, type_text, press_key, open_app, use_shortcut, read_screen, clipboard, wait,
    volume_control, set_alarm, set_timer,
    send_email, read_emails, search_emails, check_job_updates,
    create_event, list_events, delete_event,
    wether_app, rt_data, translate, news_update,
    media_play_pause, media_previous_track, media_next_track,
    schedule_task, list_scheduled, cancel_scheduled,
    research_tool,
])

DEDICATED_TOOLS = _as_tools([
    spotify_play_playlist, spotify_search_and_play,
    whatsapp_send_message, whatsapp_call,
    discord_send_message, discord_toggle_mute, discord_toggle_deafen,
    discord_answer_call, discord_decline_call,
    telegram_message_user,
    *FILE_TOOLS,
])

# read-only info tools the chat model may call to answer with live data
_CHAT_TOOL_NAMES = {"rt_data", "wether_app", "news_update", "translate",
                    "list_events", "read_emails", "search_emails",
                    "check_job_updates", "read_screen"}
CHAT_TOOLS = [t for t in SINGLE_TOOLS if t.name in _CHAT_TOOL_NAMES]

CHAT_PROMPT = (BEHAVIOUR_PROMPT + "You may call the info tools (weather, real-time data, news, translate, "
               "calendar/email reading) when the user asks about live or personal data. "
               "Never guess live data, call the tool. Don't call tools for plain conversation.")

# route -> (tools, prompt, max tool rounds). One executor node serves all three.
EXECUTORS = {
    "single":     (SINGLE_TOOLS,     SINGLE_PROMPT,     5),
    "dedicated":  (DEDICATED_TOOLS,  DEDICATED_PROMPT,  8),
    "autonomous": (AUTONOMOUS_TOOLS, AUTONOMOUS_PROMPT, 5),
}

# tools whose raw output is passed on to later plan steps
HANDOFF_TOOLS = {"research"}

# ---------------- HUMAN IN THE LOOP (shared) ----------------
YES = {"y", "yes", "yeah", "yep", "ok", "sure", "do it"}


def ask_human(question: str) -> str:
    if _unattended():  # nobody is there to answer a scheduled job
        return "Nobody is available to answer. Use your best judgment or stop."
    if _hooks["ask"] is not None:
        return _hooks["ask"](question)
    if VOICE_MODE:
        speak(question)
        return live_listen()
    return input(f"TARZ: {question}\nYou: ")


def human_in_the_loop(question: str) -> bool:
    if _unattended():  # nobody can answer live
        return False
    if _hooks["confirm"] is not None:
        return bool(_hooks["confirm"](question))
    return ask_human(f"{question} (yes/no)").strip().lower() in YES


RISKY_TOOLS = {"send_email", "delete_event", "create_event",
               "whatsapp_send_message", "whatsapp_call",
               "discord_send_message", "telegram_message_user"} | FILE_MANAGER_RISKY

# risky tools that are safe for certain arguments (no confirmation needed)
CONFIRM_EXEMPT = {"organize_folder": lambda args: args.get("dry_run", True)}


def confirm_action(name: str, args: dict) -> bool:
    """The single place that decides whether a tool call may run."""
    if name not in RISKY_TOOLS or CONFIRM_EXEMPT.get(name, lambda a: False)(args):
        return True
    if _unattended():  # scheduled job -> approval was given when it was scheduled
        return _approved()
    return human_in_the_loop(f"Run {name} with {args}?")


# ---------------- MESSAGE HELPERS ----------------
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


def _system_message(branch_prompt: str, memory_ctx: str = "") -> SystemMessage:
    now = datetime.now().strftime("%A, %d %B %Y, %I:%M %p")
    ctx = f"{memory_ctx}\n\n" if memory_ctx else ""
    return SystemMessage(
        content=f"{SYSTEM}\n\n{ctx}{branch_prompt}\nCurrent date/time: {now}")


# ---------------- GUARDRAILS (input) ----------------
def guardrails_input(user_text: str):
    global session_map
    result = guard.check(user_text, session_map)
    session_map = result.pii_map
    return result


def guard_in(state: State) -> dict:
    result = guardrails_input(state["user_text"])
    reset = {"plan": [], "step_idx": 0, "results": [], "last": {}, "final": "",
             "task": "", "memory_ctx": "", "followups": [], "followup_shown_ids": []}
    if not result.allowed:
        return {**reset, "blocked": True, "final": result.reply, "spoken": result.reply}
    return {**reset, "blocked": False, "messages": [HumanMessage(content=result.text)]}


# ---------------- RETRIEVAL ----------------
# Follow-ups should feel like a friend catching up, so they are only offered
# when the user opens the conversation ("hi", "hey", "yo", "what's up"), never
# pushed in the middle of a task or as an unprompted chat bubble.
_GREETING = (r"(?:hi+|hey+|hello+|yo+|hiya|howdy|hola|sup|wassup|gm|gn|"
             r"good\s+(?:morning|afternoon|evening|night))")
_SMALLTALK = (r"(?:what'?s\s+up|whats\s+good|what'?s\s+new|how'?s\s+it\s+going|"
              r"how\s+are\s+you(?:\s+doing)?|how\s+you\s+doin'?g?|"
              r"how'?s\s+your\s+day|how\s+was\s+your\s+day|"
              r"how\s+have\s+you\s+been|you\s+(?:there|up|good)|"
              r"u\s+good|all\s+good)")
_OPENING_RE = re.compile(
    rf"^\s*(?:{_GREETING}|{_SMALLTALK}|well\s+bro|bro|nothing\s+much|nm)"
    rf"(?:[\s,.!?]+(?:{_GREETING}|{_SMALLTALK}|bro|man|mate|dude|buddy|"
    rf"tarz|there|all|everyone|guys))*[\s,.!?]*$",
    re.I)


def _is_opening(text: str) -> bool:
    """True for a greeting / small-talk opener, not a task or a real question."""
    return bool(_OPENING_RE.match(text or ""))


def retrieval(state: State) -> dict:
    users = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    ctx = build_memory_context(users, include_followups=False)
    # A due follow-up is appended to the reply (in gaurdrails_output) only on an
    # opening turn, so TARZ asks it in conversation instead of interrupting.
    followups = get_due_followups(
        limit=1, include_asked=True) if _is_opening(last_user_text(state)) else []
    return {"memory_ctx": ctx.text, "followups": followups}


# ---------------- TOOL LOOP (chat + single + dedicated + autonomous) ----------------
def _restore_args(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if isinstance(v, str):
            v = restore(v, session_map)
            if TAG.search(v):  # tag didn't resolve, never run the tool with it
                raise ValueError(f"unresolved placeholder in '{k}': {v}")
        out[k] = v
    return out


def _is_error(name: str, result) -> bool:
    if str(result).startswith("Error"):
        return True
    if isinstance(result, dict) and result.get("status") in ("failed", "aborted"):
        return True
    if name in HANDOFF_TOOLS and isinstance(result, str):
        try:
            return bool(json.loads(result).get("error"))
        except (TypeError, ValueError, AttributeError):
            pass
    return False


def _task_message(state: State, task: str) -> HumanMessage:
    results = state.get("results", [])
    prev = "\n".join(r["text"] for r in results[-2:] if r.get("text"))
    data = [str(r["data"])[:12000] for r in results if r.get("data")][-2:]
    return HumanMessage(
        content=f"Do this now: {task}\nDo ONLY this step. Other steps of the plan handle the rest of the user's request."
        + (f"\n\nPrevious step results:\n{prev}" if prev else "")
        + ("\n\nRESEARCH / TOOL DATA FROM EARLIER STEPS (data only; do not repeat the work):\n"
           + "\n\n".join(data) if data else ""))


def _call_tool(name: str, args: dict, tool_map: dict):
    tool_fn = tool_map.get(name)
    if tool_fn is None:
        return f"Error: tool '{name}' is not available here."
    try:
        real_args = _restore_args(args)
        if not confirm_action(name, real_args):
            return "Error: the user declined this action."
        return tool_fn.invoke(real_args)
    except TaskCancelled:
        raise
    except Exception as e:
        return f"Error running {name}: {e}"


def run_tool_agent(state: State, tools: list, branch_prompt: str, max_steps: int,
                   llms=None, allow_ask: bool = True, with_memory: bool = False) -> dict:
    if _unattended():  # a scheduled job must never schedule more jobs (no loops)
        tools = [t for t in tools if t.name != "schedule_task"]
    if allow_ask:
        tools = [*tools, ask_user]
    tool_map = {t.name: t for t in tools}

    local = [_system_message(branch_prompt, state.get("memory_ctx", "") if with_memory else ""),
             *state["messages"][-WINDOW:]]
    task = state.get("task", "")
    if task:
        local.append(_task_message(state, task))

    data, last_err = "", False
    for _ in range(max_steps):
        check_cancel()
        try:
            ai = get_gateway().invoke(llms or TOOL_CALLING_LLMS, local, tools=tools)
        except AllModelsFailed:
            return _step("All my models are down right now, try again in a bit.", False)

        local.append(_replayable_ai_message(ai))
        calls = getattr(ai, "tool_calls", None) or []
        if not calls:
            return _step(_visible_text(ai.content) or "I couldn't get a text response.",
                         not last_err, data)

        for call in calls:
            check_cancel()
            name, args = call["name"], call["args"]
            _emit_progress(
                {"type": "activity", "text": " ".join(name.split("_")).capitalize()})

            if name == "ask_user":
                # mask PII in the reply too
                result = guardrails_input(ask_human(args["question"])).text
            else:
                result = _call_tool(name, args, tool_map)
                last_err = _is_error(name, result)
                if name in HANDOFF_TOOLS:
                    data = result if isinstance(result, str) else json.dumps(
                        result, ensure_ascii=False, default=str)
            local.append(ToolMessage(content=str(
                result), tool_call_id=call["id"]))

    return _step("I hit my step limit before finishing.", False)


# ---------------- EXECUTION NODES ----------------
def chat(state: State) -> dict:
    # Conversation plus read-only info tools, on the warmer (plain) models.
    return run_tool_agent(state, CHAT_TOOLS, CHAT_PROMPT, max_steps=3,
                          llms=PLAIN_LLMS, allow_ask=False, with_memory=True)


def execute(state: State) -> dict:
    route = state["route"]
    tools, prompt, max_steps = EXECUTORS[route]
    task = state.get("task", "")
    # A research step may ONLY research. With GUI tools in reach it kept going and tried
    # to do the rest of the request (open Excel, click, type). Later plan steps do that.
    if route == "single" and re.search(r"\bresearch\b", task, re.I) and "schedule_task" not in task:
        tools = [t for t in tools if t.name == "research"] or tools
    return run_tool_agent(state, tools, prompt, max_steps)


# ---------------- ORCHESTRATOR (the hub) ----------------
ROUTES = {"chat", *EXECUTORS}
MAX_PLAN_STEPS = 4

PLANNER_PROMPT = """You are the planner for a desktop voice assistant. Reply with ONLY a JSON array:
[{"route": "chat|single|dedicated|autonomous", "task": "..."}]

Routes:
- chat: conversation, questions, opinions, anything needing no action. task = "".
- single: ONE tool call that finishes the job by itself: set an alarm or timer, send or read email
  (including ALL mail and job-application replies), calendar, weather, news, translate, volume,
  media play/pause/next/previous, schedule/list/cancel a scheduled task, or research fresh web information.
- dedicated: actions using direct app and file tools: Spotify, WhatsApp, Telegram, Discord, File Manager.
- autonomous: everything else. Anything that needs several clicks or typing, interactive work on a website
  or in a browser, downloads, filling forms, or any job no single or dedicated tool covers.

Rules:
- SCHEDULING HAS PRIORITY OVER APP ROUTING. If the user wants anything done later, after a delay, at a
  future time, or on a repeat, plan ONE single step telling the executor to call schedule_task with the
  complete action (no time words) and the time phrase exactly as the user said it. This applies even when
  the action is WhatsApp, Spotify, email, etc. NEVER run the action now.
  * If a detail needed to perform it later is missing (message: recipient AND exact text; music: song or
    playlist; event: title and time), the executor must call ask_user for only that detail, then schedule.
  * The scheduler tool asks for approval of risky actions. Do not treat chat as approval, and do not say
    it is scheduled until the tool confirms. If declined, report that it was not scheduled.
  * Follow-ups like "schedule it", "after two minutes" or a corrected time update the most recent
    unfinished scheduling request: carry its action and details from history, and ignore any earlier
    claim that scheduling is unavailable.
  * "What is scheduled" / "cancel my 11 pm task" is ONE single step (list_scheduled / cancel_scheduled).
- NEVER plan low-level GUI actions (open an app, type, press a key, click) as separate steps. A job with
  several UI actions is ONE autonomous step with the whole goal as the task.
- One clear request = ONE step ("play X on spotify" is one step; "download X from the official site using
  Brave" is one autonomous step). Use more than one step only for truly different jobs ("set a timer,
  then email Sam"). Maximum 4 steps.
- RESEARCH: for fresh web research followed by a desktop/app action that uses the findings, plan two
  steps: SINGLE calling research with the query and the exact fields needed, then AUTONOMOUS or DEDICATED
  for the action. Never send the computer agent to research the web. Research only = one SINGLE step.
- LOCAL FILES go to dedicated (File Manager tools), never to the computer agent.
- EXCEL / SPREADSHEETS: there is no Excel tool. Creating or editing a workbook is ONE autonomous step.
- JOB APPLICATION UPDATES are email: plan ONE single step with check_job_updates. Never send the
  computer agent to LinkedIn, Naukri, Internshala or any job site for application status.
- Each task must be self-contained: include names, text, times, and the browser name if the user gave one.
  Keep placeholders like <EMAIL_1> exactly as written.
- Use the memory block to resolve things like "my usual playlist".

Examples:
user: download spotify from the official website in brave
[{"route": "autonomous", "task": "Download the Spotify installer from the official Spotify website using Brave"}]
user: read all my emails
[{"route": "single", "task": "Call read_emails with mailbox='all' and max_results=50 to list all mail"}]
user: check my job application updates
[{"route": "single", "task": "Call check_job_updates to find job application replies: rejections, offers, interview invites, shortlists"}]
user: play sailor song on spotify at 11 pm
[{"route": "single", "task": "Use schedule_task: task='Play Sailor Song on Spotify', when='11 pm'"}]
user: text Mummy on WhatsApp after 2 minutes
[{"route": "single", "task": "Schedule a WhatsApp message to Mummy after 2 minutes. Message text is missing: ask the user for the exact text, then call schedule_task. Do not send now."}]
user: in response to the pending request, say hey
[{"route": "single", "task": "Schedule the previously requested WhatsApp message to Mummy with exact text 'hey' after 2 minutes. Call schedule_task; do not send now."}]"""


def _shortcut_plan(user_text: str):
    """Deterministic pre-filter: the only regex routing. Everything else goes to the LLM planner."""
    # YouTube owns the search index and playback controls; a web-research handoff
    # is slow and loses the exact playable video URL.
    if (re.search(r"\byoutube\b", user_text, re.I)
            and re.search(r"\b(play|watch|start playing)\b", user_text, re.I)
            and re.search(r"\b(find|search|best|tutorial|video|song|music)\b", user_text, re.I)):
        return [{
            "route": "autonomous",
            "task": (
                "Search YouTube for the requested video, choose the best matching result, "
                "play it, and verify playback. Preserve the exact subject and constraints "
                f"from this request: {user_text}"
            ),
        }]

    file_action = re.search(
        r"\b(list|show|search|find|read|create|make|copy|move|rename|delete|open|append|zip|compress|organize)\b",
        user_text, re.I)
    file_target = re.search(
        r"\b(file\s*manager|filemanager|file explorer|files?|folders?|directories|directory|downloads|documents|pictures)\b",
        user_text, re.I)
    fresh_research = re.search(
        r"\b(on amazon|search (?:the )?(?:web|online)|research|latest|current prices?|"
        r"top\s+\d+|best\s+\d+|compare|cheapest)\b", user_text, re.I)
    spreadsheet = re.search(
        r"\b(excel|spreadsheet|workbook|\.xlsx|\.xlsm)\b", user_text, re.I)
    if file_action and file_target and not fresh_research and not spreadsheet:
        return [{"route": "dedicated",
                 "task": f"Use the dedicated File Manager tools to perform this local file task: {user_text}. "
                         "Do not use GUI automation. Check paths with list_dir/search_files when needed."}]
    return None


def _make_plan(state: State) -> list:
    plan = _shortcut_plan(last_user_text(state))
    if plan:
        return plan

    info = (f"\n\nSingle tools: {', '.join(t.name for t in SINGLE_TOOLS)}"
            f"\nDedicated tools: {', '.join(t.name for t in DEDICATED_TOOLS)}")
    ctx = state.get("memory_ctx", "")
    sys_msg = SystemMessage(content=PLANNER_PROMPT +
                            info + (f"\n\n{ctx}" if ctx else ""))
    try:
        raw = get_gateway().invoke(
            CLASSIFY_LLMS, [sys_msg, *state["messages"][-WINDOW:]]).content
    except Exception as exc:
        # Never silently turn an action into chat when all planner models fail.
        raise RuntimeError(
            "TARZ could not plan this request because its classification models failed. "
            "Check the provider errors above and try again."
        ) from exc
    try:
        data = json.loads(re.search(r"\[.*\]", raw, re.S).group())
    except Exception:
        data = []

    plan = []
    for s in data[:MAX_PLAN_STEPS]:
        if isinstance(s, dict):
            route = s.get("route") if s.get("route") in ROUTES else "chat"
            plan.append({"route": route, "task": str(s.get("task") or "")})
    plan = plan or [{"route": "chat", "task": ""}]  # planner failed: just talk
    print("[plan] " +
          " | ".join(f"{s['route']}: {s['task'][:70]}" for s in plan))
    return plan


def orchastration(state: State) -> dict:
    plan = state.get("plan")
    if not plan:                                        # new turn
        plan, idx, results = _make_plan(state), 0, []
    else:                                               # a branch just finished
        last = state.get("last") or {}
        results = [*state.get("results", []), last]
        idx = state["step_idx"] + 1
        if not last.get("ok", True):                    # failed -> stop, report it
            idx = len(plan)

    if idx >= len(plan):
        final = "\n".join(r["text"]
                          for r in results if r.get("text")) or "Done."
        _emit_progress({"type": "finished", "text": final})
        return {"plan": plan, "step_idx": idx, "results": results,
                "route": "end", "final": final}

    step = plan[idx]
    _emit_progress({
        "type": "plan", "title": last_user_text(state) or state.get("user_text", ""),
        "steps": [{"text": item["task"],
                   "status": "done" if i < idx else "run" if i == idx else "todo"}
                  for i, item in enumerate(plan)],
        "active": f"Step {idx + 1} of {len(plan)} · {step['route']}",
    })
    return {"plan": plan, "step_idx": idx, "results": results,
            "route": step["route"], "task": step["task"]}


def _route(state: State) -> str:
    route = state["route"]
    return route if route in ("chat", "end") else "execute"


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
    if state.get("blocked") or _unattended():  # scheduled runs are not conversations
        return {}

    masked_reply = state.get("final") or ""
    masked_user = last_user_text(state)
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
    if routes == {"single"} and not has_pending_followups():
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
        ("execute", execute),
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
    g.add_conditional_edges("orchastration", _route,
                            {"chat": "chat", "execute": "execute",
                             "end": "gaurdrails_output"})
    for n in ("chat", "execute"):
        g.add_edge(n, "orchastration")  # the loop in the diagram
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
    # after the graph exists, before the loop
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
            with graph_lock:
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
