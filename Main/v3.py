
from langchain_core.messages import ToolMessage
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
from config_settings.config import TOOL_CALLING_LLMS, PLAIN_LLMS, VISION_LLMS
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
from Tools.Autonomous_layer.autonomous_agent import computer_agent

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


guard = Guardrails(ask=jev_ask)
session_map: dict[str, str] = {}

# Tools
# --------------------------------------------------------------------------


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
    spotify_play_playlist, spotify_search_and_play,
    whatsapp_send_message, whatsapp_call,
    discord_send_message, discord_toggle_mute, discord_toggle_deafen, discord_answer_call, discord_decline_call,
    news_update,
    telegram_message_user,
    computer_agent
]


AUTONOMOUS_TOOLS = [
    computer_agent,
]


SINGLE_TOOLS = [

    click, type_text, press_key, open_app, use_shortcut, read_screen, clipboard, wait,

    volume_control, set_alarm, set_timer,
    send_email, read_emails, search_emails,
    create_event, list_events, delete_event,
    wether_app, rt_data, translate, news_update,
    media_play_pause, media_previous_track, media_next_track,
]


DEDICATED_TOOLS = [
    spotify_play_playlist, spotify_search_and_play,
    whatsapp_send_message, whatsapp_call,
    discord_send_message, discord_toggle_mute, discord_toggle_deafen,
    discord_answer_call, discord_decline_call,
    telegram_message_user,
]

TOOLS_BY_ROUTE = {
    "autonomous": AUTONOMOUS_TOOLS,
    "single": SINGLE_TOOLS,
    "dedicated": DEDICATED_TOOLS,
}


class Statex(TypedDict):
    pass


class State(TypedDict):
    messages: Annotated[list, add_messages]
    route: str


def _bind_and_call(messages, tools):
    # TODO: adjust to your gateway's signature, this is the one line I can't see
    return get_gateway().invoke(TOOL_CALLING_LLMS, messages, tools=tools)


def run_tool_agent(state: State, tools: list, branch_prompt: str, max_steps: int) -> dict:
    now = datetime.now().strftime("%A, %d %B %Y, %I:%M %p")
    sys_msg = SystemMessage(
        content=f"{SYSTEM}\n\n{branch_prompt}\nCurrent date/time: {now}")

    tools = [*tools, ask_user]
    tool_map = {t.name: t for t in tools}

    local = [sys_msg, *state["messages"][-WINDOW:]]

    for _ in range(max_steps):
        check_cancel()
        try:
            ai = _bind_and_call(local, tools)
        except AllModelsFailed:
            return {"messages": [AIMessage(content="All my models are down right now, try again in a bit.")]}

        local.append(ai)
        calls = getattr(ai, "tool_calls", None) or []
        if not calls:
            return {"messages": [AIMessage(content=ai.content)]}  # done

        for call in calls:
            check_cancel()
            name, args = call["name"], call["args"]

            if name == "ask_user":
                # pause graph, wait for the user's answer, then continue
                answer = interrupt(
                    {"question": args["question"], "pending": args.get("pending", "")})
                local.append(ToolMessage(content=str(
                    answer), tool_call_id=call["id"]))
                continue

            tool_fn = tool_map.get(name)
            if tool_fn is None:
                result = f"Error: tool '{name}' is not available here."
            else:
                try:
                    result = tool_fn.invoke(args)
                except TaskCancelled:
                    raise
                except Exception as e:
                    result = f"Error running {name}: {e}"
            local.append(ToolMessage(content=str(
                result), tool_call_id=call["id"]))

    return {"messages": [AIMessage(content="I hit my step limit before finishing. Check what got done and tell me if you want me to continue.")]}


def listen():
    return input("You: ")


def guardrails_input(user_text: str):
    global session_map
    result = guard.check(user_text, session_map)
    session_map = result.pii_map
    return result


def jev_category():
    return


def chat(messages):
    return get_gateway().invoke(PLAIN_LLMS, messages).content


def retrieval(state: dict) -> dict:
    users = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    # first turn of the session
    first_message = len(state["messages"]) == 1
    ctx = build_memory_context(users, include_followups=first_message)
    return {"memory_ctx": ctx.text, "followup_ids": ctx.followup_ids}


_last_save: threading.Thread | None = None


def memory_extraction(state: dict) -> dict:
    global _last_save
    if state.get("blocked"):
        return {}

    masked_reply = state.get("final") or " ".join(
        r for r in state.get("results", []) if r)
    masked_user = next((m.content for m in reversed(state["messages"])
                        if isinstance(m, HumanMessage)), "")
    if not masked_user or not masked_reply:
        return {}

    # skip pure single-tool turns (alarms, volume...), nothing worth remembering
    routes = {p["route"] for p in state.get("plan", [])}
    if routes and routes == {"single"}:
        return {}

    _last_save = save_imp_context_background(masked_user, masked_reply)
    return {}


def orchastration():
    return


def single_tool(state: State):
    return run_tool_agent(state, SINGLE_TOOLS, SINGLE_PROMPT, max_steps=5)


def tools_dedicated(state: State):
    return run_tool_agent(state, DEDICATED_TOOLS, DEDICATED_PROMPT, max_steps=4)


def agent_autonomous(state: State):
    return run_tool_agent(state, AUTONOMOUS_TOOLS, AUTONOMOUS_PROMPT, max_steps=5)


def human_in_the_loop():
    return


def gaurdrails_output():
    return


def speak_output():
    return


def main():
    return


if __name__ == "__main__":
    state = {"messages": [HumanMessage(content="what do I have tomorrow")]}
    out = retrieval(state)
    print(out["memory_ctx"])
    print("followup ids:", out["followup_ids"])
