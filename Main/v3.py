
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
from Main.llm_gateway import get_gateway, AllModelsFailed
from Main.config import TOOL_CALLING_LLMS, CLASSIFY_LLMS, PLAIN_LLMS, jev_client, Choice
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
    return live_listen()
    # return input("You
    # :")


class TarzState(TypedDict):
    messages: Annotated[list, add_messages]
    steps: int
    worker_steps: int
    next: str
    supervisor_instruction: str


MAX_WORKER_STEPS = 6
MAX_STEPS = 12


def retrieval_category(question):
    response = jev_client.system_one(
        model="typesafe/jev-1.13",
        state={"question": question},
        questions={
            "category": Choice(
                instructions=(
                    "Pick exactly one category. Decide in this order:\n"
                    "1. If the question is about ONE named person's own record "
                    "(employee ID, email, phone, full name) -> lookup_search.\n"
                    "2. Else, if the question already uses the key words a company "
                    "document would use for the topic (a policy name, system name, "
                    "metric or term) and wants one fact from it -> specific_search.\n"
                    "3. Else, if the question describes a situation, problem or goal in "
                    "everyday words without naming the policy or system, or needs "
                    "several sections combined -> summarize_search.\n"
                    "If you are unsure between specific_search and summarize_search, "
                    "choose summarize_search."
                ),
                criteria={
                    "lookup_search": (
                        "The question is about ONE named person's own record or contact "
                        "details, identified by employee ID, email, phone number or full "
                        "name, and asks a fact about that individual (salary, team, "
                        "manager, joining date). It is NOT about a policy, process, "
                        "system or company-wide fact. "
                        "Examples: 'what is FINEMP1042's salary', "
                        "'find isha.chowdhury@fintechco.com', 'who is Vihaan Desai', "
                        "'what is Vihaan Desai's department'. "
                        "NOT this: 'what is the leave policy' (no person), "
                        "'why was my leave rejected' (no named person)."
                    ),
                    "specific_search": (
                        "A DIRECT question that already uses the same key words the "
                        "document would use (policy name, system name, metric, term) and "
                        "wants one fact, number, date, list or definition. The answer "
                        "sits in one section. "
                        "Examples: 'what is the leave policy', 'what was Q1 2024 revenue', "
                        "'when is the reimbursement deadline', 'what was last year's "
                        "company turnover', 'what are the RTO and RPO for disaster "
                        "recovery', 'which databases does FinSolve use', 'what is the "
                        "minimum unit test coverage'. "
                        "NOT this: a question that describes a situation or symptom "
                        "instead of naming the topic."
                    ),
                    "summarize_search": (
                        "An INDIRECT question: it describes a situation, problem, symptom "
                        "or goal in everyday words and does NOT name the policy, process "
                        "or system that answers it, so its words will not match the "
                        "document. Often starts with why / how come / who checks / what "
                        "happens if / how do we stop. Also use it for broad questions "
                        "that need several sections combined (summarize, explain end to "
                        "end, compare). Works for any department. "
                        "Examples: 'why is my leave request getting rejected', 'why "
                        "haven't I got my travel money back yet', 'our cloud bill keeps "
                        "going up, who checks it', 'a test fails randomly then passes, "
                        "what does the pipeline do', 'how do we stop old wrong data from "
                        "being shown after a change', 'a customer is worried their card "
                        "details could be stolen, what protects them', 'summarize our "
                        "security measures'. "
                        "Contrast: 'what is the cache invalidation policy' is "
                        "specific_search, but 'how do we stop old wrong data being shown "
                        "after a change' is summarize_search."
                    ),
                },
            )
        },
    )

    return response.answers["category"].choice


print(retrieval_category("who is Vihaan Desai"))
