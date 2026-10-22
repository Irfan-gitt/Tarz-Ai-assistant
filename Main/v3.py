from config_settings. guardrails import Guardrails, jev_ask, restore
import keyboard
import winreg
import threading
from datetime import datetime
import time
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from typing import Annotated, Literal
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
from config_settings.llm_gateway import get_gateway, AllModelsFailed
from config_settings.config import TOOL_CALLING_LLMS, CLASSIFY_LLMS, PLAIN_LLMS, jev_client, Choice
from Prompts.prompt import SYSTEM, SUPERVISOR_PROMPT
from Vison.vision import vision_verify_system
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
from Tools.gmail import send_email, read_emails, search_emails
from Tools.calendar_tool import create_event, list_events, delete_event
from Tools.real_time_data import rt_data
from Tools.Dedicated_Tools.spotify_gui import spotify_play_song, spotify_play_playlist
from Tools.Dedicated_Tools.telegram_gui import telegram_message_user
from Tools.Dedicated_Tools.whatsapp_gui import whatsapp_send_message, whatsapp_end_call, whatsapp_call_contact
from Tools.Dedicated_Tools.discord_gui import discord_send_message, discord_toggle_mute, discord_toggle_deafen, discord_answer_call, discord_decline_call
from Tools.Dedicated_Tools.browser_gui import browser_open_url
print("on tools")  # noqa
from Actions.execute_action import click, type_text, press_key, read_screen, open_app, volume_control, wether_app, use_shortcut, set_alarm, news_update, set_timer, translate, clipboard, wait, done
from Tools.memory import save_imp_context
from Tools.rag import reranked_retrieve
from Tools.media_control import media_play_pause, media_next_track, media_previous_track
from pydantic import BaseModel, Field
from Audio.stt import live_listen
from Audio.wake_word import wait_for_wake_word, play_done_chime, wake_hotkey_event

from Tools.cancel_state import cancel_event, TaskCancelled, check_cancel
from Audio.tts import speak
from Audio.orb_overlay import get_orb
print("[Init] 3 - rag...")


os.environ.setdefault("LANGSMITH_HIDE_INPUTS", "false")
os.environ.setdefault("LANGSMITH_HIDE_OUTPUTS", "false")


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


print("[Init] Setting up LLMs...")


def listen():
    # return live_listen()
    return input("You: ")


class TarzState(TypedDict):
    messages: Annotated[list, add_messages]
    steps: int
    worker_steps: int
    next: str
    supervisor_instruction: str


MAX_WORKER_STEPS = 6
MAX_STEPS = 12


guard = Guardrails(ask=jev_ask)


class TarzState(TypedDict):
    ...  # your existing keys
    pii_map: dict[str, str]
    blocked: bool
    flagged: bool


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


def restore_args_node(state: TarzState) -> dict:
    last = state["messages"][-1]
    calls = [{**c, "args": restore(c["args"], state.get("pii_map", {}))}
             for c in last.tool_calls]
    return {"messages": [AIMessage(content=last.content, tool_calls=calls, id=last.id)]}
