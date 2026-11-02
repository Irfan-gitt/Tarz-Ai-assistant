
from typesafe_sdk import Choice, TypeSafeClient

from pathlib import Path
from typing import Iterable
import re
import os
from dotenv import load_dotenv
from langchain_community.embeddings import JinaEmbeddings
from langchain_core.documents import Document
from langchain_core.tools import tool

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
load_dotenv()


EMBEDDING = JinaEmbeddings(model_name="jina-embeddings-v3")


OPENROUTER_API_KEY = os.getenv(
    "OPENROUTER_API_KEY2")


client = TypeSafeClient(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api",
)


def retrieval_category(question):
    response = client.system_one(
        model="typesafe/jev-1.13",
        state={"question": question},
        questions={
            "category": Choice(
                instructions=(
                    "You are the request classification layer for TARZ, "
                    "a desktop AI assistant. Classify the user's request "
                    "into exactly ONE category.\n\n"

                    "Follow these rules in order:\n\n"

                    "1. CHAT:\n"
                    "If the user is greeting, asking a question, having a "
                    "conversation, requesting an explanation, brainstorming, "
                    "or asking for information that does not require performing "
                    "an action on their computer, choose chat.\n\n"

                    "2. SINGLE_TASK:\n"
                    "If the user requests an action that can be handled by "
                    "one dedicated tool, choose single_task.\n"
                    "Prefer dedicated tools over generic browser click/type "
                    "operations whenever a dedicated tool supports the action.\n\n"

                    "3. MULTI_TASK:\n"
                    "Choose multi_task ONLY when the user explicitly requests "
                    "two or more distinct actions that must be performed as "
                    "separate tasks, and the requested actions cannot all be "
                    "handled by one dedicated tool.\n"
                    "A multitask request may combine dedicated tools, generic "
                    "browser operations, or other available desktop actions.\n\n"

                    "4. DEFAULT RULE:\n"
                    "If the request does not clearly require an action, choose "
                    "chat. If an action is required and one dedicated tool "
                    "can fulfill it, choose single_task. Use multi_task only "
                    "when multiple distinct actions are genuinely required.\n\n"

                    "IMPORTANT:\n"
                    "- Classify the user's intent, not the number of words.\n"
                    "- Do not classify a tool's internal steps as separate tasks.\n"
                    "- A single dedicated tool remains single_task even if it "
                    "internally searches, navigates, and performs an action.\n"
                    "- Do not invent tools that are not listed below.\n"
                    "- Opening a website is one task, even if the user specifies "
                    "which browser to use.\n"
                    "- If the user requests multiple independent actions, "
                    "evaluate whether they require multiple task executions.\n"
                ),
                criteria={
                    "chat": (
                        "The user wants conversation, information, an answer, "
                        "an explanation, advice, brainstorming, or a response "
                        "that does not require executing an action on the "
                        "computer.\n\n"
                        "Examples:\n"
                        "'Hey TARZ'\n"
                        "'How are you?'\n"
                        "'Explain Python decorators'\n"
                        "'What is machine learning?'\n"
                        "'Suggest some good songs'\n"
                        "'Which browser is better?'\n"
                        "'What can you do?'\n\n"
                        "Also choose chat for ambiguous requests that do not "
                        "clearly ask TARZ to perform an action."
                    ),

                    "single_task": (
                        "The user requests one actionable task that can be "
                        "handled by a single dedicated tool or one standalone "
                        "desktop action.\n\n"

                        "DEDICATED TOOLS:\n\n"

                        "Spotify:\n"
                        "- spotify_play_song(song_name): searches for and "
                        "plays a specific song.\n"
                        "- spotify_play_playlist(playlist_name): searches "
                        "for and plays a playlist.\n\n"

                        "WhatsApp:\n"
                        "- whatsapp_send_message(contact_name, message)\n"
                        "- whatsapp_call_contact(contact_name)\n"
                        "- whatsapp_end_call()\n\n"

                        "Discord:\n"
                        "- discord_send_message(target_name, message)\n"
                        "- discord_toggle_mute()\n"
                        "- discord_toggle_deafen()\n"
                        "- discord_answer_call()\n"
                        "- discord_decline_call()\n\n"

                        "Browser:\n"
                        "- browser_open_url(query_or_url, browser), where "
                        "browser is Brave or Chrome.\n\n"

                        "Examples:\n"
                        "'Play Blinding Lights on Spotify'\n"
                        "'Play my workout playlist'\n"
                        "'Send Rahul a WhatsApp message saying I am coming'\n"
                        "'Call Rahul on WhatsApp'\n"
                        "'End the current WhatsApp call'\n"
                        "'Send a message in the Discord gaming channel'\n"
                        "'Mute my Discord microphone'\n"
                        "'Open YouTube in Brave'\n"
                        "'Open github.com in Chrome'\n"
                        "'Open Spotify in Brave'\n"
                        "'Download Spotify using Brave' if this is treated "
                        "as one browser-based task by the available executor.\n\n"

                        "Choose this category whenever one dedicated tool "
                        "fully handles the requested task, even if that tool "
                        "performs multiple internal operations."
                    ),

                    "multi_task": (
                        "The user explicitly requests TWO OR MORE distinct "
                        "actions that must be executed separately. At least "
                        "one additional task is required beyond what a single "
                        "dedicated tool can accomplish.\n\n"

                        "Examples:\n"
                        "'Open Brave and then open YouTube in it'\n"
                        "'Open Brave, download Spotify, and then open Discord'\n"
                        "'Play Blinding Lights on Spotify and send Rahul "
                        "a WhatsApp message saying I am busy'\n"
                        "'Open GitHub in Chrome and mute my Discord microphone'\n"
                        "'Call Rahul on WhatsApp and then open Spotify'\n\n"

                        "IMPORTANT CONTRASTS:\n"
                        "'Play a song on Spotify' -> single_task.\n"
                        "'Play a song and send Rahul a WhatsApp message' "
                        "-> multi_task.\n"
                        "'Open YouTube in Brave' -> single_task.\n"
                        "'Open Brave and then open YouTube in it' -> "
                        "multi_task if browser startup and navigation are "
                        "implemented as separate executable actions.\n\n"

                        "Do NOT choose multi_task just because the user uses "
                        "the words 'and', 'then', or 'after'. Determine "
                        "whether the request genuinely requires multiple "
                        "separate task executions.\n\n"

                        "If one dedicated tool fully satisfies the request, "
                        "choose single_task instead."
                    ),
                },
            )
        },
    )

    return response.answers["category"].choice


while True:
    u = input("You: ")
    print(retrieval_category(u))


def orchastrator(jev_cat):
    if jev_cat == "single_task":
        return "single"

    else:
        # split the code into single tasks
        return "multi"
