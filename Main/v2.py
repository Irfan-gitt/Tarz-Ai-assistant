
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from typing import Annotated, Literal
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict
import re
import sys  # noqa
import os  # noqa

# Windows terminals often default to cp1252, while startup logs and prompts
# contain Unicode characters. Configure output before importing modules that
# print their own startup messages.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa
import time
import os
from datetime import datetime
from dotenv import load_dotenv
from langsmith import traceable
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_cerebras import ChatCerebras
from Prompts.prompt import SYSTEM_PROMPT, SYSTEM
from Vison.vision import describe_screen, vision_verify_system
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
from Tools.gmail import send_email, read_emails, search_emails
from Tools.calendar_tool import create_event, list_events, delete_event
from Tools.real_time_data import rt_data
from Audio.stt import listen as stt_listen
from Audio.tts import speak
from Tools.Dedicated_Tools.spotify_gui import spotify_play_song, spotify_play_playlist
print("on tools")  # noqa
from Actions.execute_action import type_text, press_key, open_app, read_screen, volume_control, news_update, wether_app, use_shortcut, set_alarm, set_timer, translate, clipboard, wait, done
from Tools.memory import save_imp_context
from Tools.rag import hybrid_retrieve
from Tools.media_control import media_play_pause, media_next_track, media_previous_track
from Actions.execute_action import click
from pydantic import BaseModel, Field

print("[Init] 3 - rag...")

load_dotenv()

# Keep trace structure and source metadata without uploading prompts, retrieved
# memories, screen text, or tool results. Set either value to "false" in .env
# only when full payload capture is explicitly wanted.
os.environ.setdefault("LANGSMITH_HIDE_INPUTS", "false")
os.environ.setdefault("LANGSMITH_HIDE_OUTPUTS", "false")


api_key = os.getenv("GROQ_API_KEY")
api_or = os.getenv("OPENROUTER_KEY")
api_cb = os.getenv("CEREBRAS_API_KEY")


DOMAIN_TOOLS = {
    "gui_control":       [click, type_text, press_key, open_app, use_shortcut, clipboard, wait],
    "system":   [volume_control, set_alarm, set_timer],
    "communication":    [send_email, read_emails, search_emails],
    "calendar": [create_event, list_events, delete_event],
    "info":     [news_update, wether_app, rt_data, translate],

    "media_control":    [media_play_pause, media_previous_track, media_next_track],

    "dedicated_tool_spotify":    [spotify_play_song, spotify_play_playlist],

    "dedicated_tool_telegram":    [spotify_play_song, spotify_play_playlist],
    "dedicated_tool_discord":    [spotify_play_song, spotify_play_playlist],
    "dedicated_tool_browser":    [spotify_play_song, spotify_play_playlist],

}

print("[Init] Setting up LLMs...")

try:
    llm_plain = ChatGroq(
        api_key=api_key,
        temperature=0.7,
        model="openai/gpt-oss-120b"
    )

except Exception as e:
    print(f"An error occurred while setting up LLMs: {e}")
    llm_plain = ChatGroq(
        api_key=api_key,
        temperature=0.7,
        model="openai/gpt-oss-120b"
    )

router_llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=os.getenv("GEMINI_KEY_5"),
    temperature=0
)


def listen():
    user_input = input("You:")
    return user_input
    # return stt_listen()


TOOL_LLMS = [

    ChatGroq(
        model="openai/gpt-oss-120b",
        api_key=os.getenv("GROQ_API_KEY"),
    ),


    ChatCerebras(
        model="gpt-oss-120b",
        api_key=os.getenv("CEREBRAS_API_KEY2"),
        temperature=0.2
    ),

    # Independent fallback when both Cerebras accounts are out of quota.
    ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=os.getenv("GEMINI_KEY_5"),
        temperature=0.2
    ),

]


class TarzState(TypedDict):
    messages: Annotated[list, add_messages]
    category: str
    steps: int
    worker_steps: int
    next: str


class MessageClassifier(BaseModel):
    message_category: Literal[
        "chat", "system", "gui_control", "info",
        "communication", "dedicated_tool_spotify", "calendar", "media"
    ] = Field(
        default="chat",
        description="The single best matching category; use chat for normal conversation.",
    )


MAX_WORKER_STEPS = 6


MAX_STEPS = 12


class SupervisorDecision(BaseModel):
    next: Literal["finished", "route"] = Field(
        description="Whether the task is finished or another action is required."
    )

    reasoning: str = Field(
        description="Brief explanation of why this decision was made."
    )

    instruction: str = Field(
        description="If another action is needed, describe what should be attempted next."
    )


def verify_completion(expected_outcome: str) -> dict:
    answer = vision_verify_system(
        f"Is this currently true on screen: {expected_outcome}? "
        f"Answer yes or no, then briefly explain why."
    )
    confirmed = answer.strip().upper().startswith("YES")
    return {
        "confirmed": confirmed,
        "detail": answer
    }


def supervisor(state: dict) -> dict:
    steps = state.get("steps", 0)

    if steps >= MAX_STEPS:
        return {"next": "finished", "steps": steps + 1}

    if steps == 0:
        return {"next": "route", "steps": steps + 1}

    recent = state["messages"][-6:]
    tool_ran = any(isinstance(m, ToolMessage) for m in recent)

    if not tool_ran:
        return {"next": "finished", "steps": steps + 1}

    user_request = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)))
    answer = verify_completion(user_request)
    if answer["confirmed"]:
        return {"next": "finished", "steps": steps + 1}

    return {"next": "route", "steps": steps + 1, "worker_steps": 0}


def route_from_supervisor(state: dict) -> str:
    return state["next"]


def route_from_worker(state: TarzState) -> str:
    last = state["messages"][-1]
    if not getattr(last, "tool_calls", None):
        return "supervisor"
    if state.get("worker_steps", 0) >= MAX_WORKER_STEPS:
        return "router"
    return "tools"


def router(state: TarzState) -> dict:
    classify_llm = llm_plain.with_structured_output(MessageClassifier)
    try:
        result = classify_llm.invoke([
            SystemMessage(content=("You are TARZ's request router. Classify the user's request into the single "
                                   "best-matching category based on these descriptions:\n\n"

                                   "chat = Normal conversation, no action needed. Greetings, explanations, "
                                   "general knowledge, casual conversation.\n"
                                   "system = OS/device actions: volume, alarms, timers, clipboard.\n"
                                   "gui_control = Direct computer interaction with no dedicated tool match: "
                                   "clicking, typing, opening apps, reading the screen, shortcuts.\n"
                                   "info = External/current info: weather, news, live data, translation.\n"
                                   "communication = Email/messages when no dedicated app tool matches.\n"
                                   "dedicated_tool_spotify = Anything Spotify-specific: play a song, playlist, "
                                   "control playback.\n"
                                   "calendar = Create, list, update, or delete calendar events.\n"
                                   "media = Generic media controls not tied to one app: play/pause, next, previous.\n\n"

                                   "DEDICATED TOOL PRIORITY: prefer a dedicated tool category over a generic one "
                                   "when the request clearly matches it.\n"
                                   "Choose the single most specific matching category."
                                   )),
        ] + sanitize_for_plain_llm(state["messages"][-10:]))
        category = result.message_category if hasattr(
            result, "message_category") else result.get("message_category", "chat")
    except Exception as e:
        print(f"[router] classification failed, defaulting to chat: {e}")
        category = "chat"
    return {"category": category}


def route_edge(state: TarzState) -> str:
    return state["category"]


current_llm_idx = 0


def make_worker(domain_tools):
    bound = domain_tools + [done]

    def worker(state: TarzState) -> dict:
        global current_llm_idx
        wsteps = state.get("worker_steps", 0)
        failures = []
        for _ in range(len(TOOL_LLMS)):
            try:
                llm = TOOL_LLMS[current_llm_idx].bind_tools(bound)
                response = llm.invoke(state["messages"])
                # ← added
                return {"messages": [response], "worker_steps": wsteps + 1}
            except Exception as e:
                provider = type(TOOL_LLMS[current_llm_idx]).__name__
                err = str(e).lower()
                failures.append(f"{provider}: {str(e)[:300]}")
                if any(x in err for x in ("429", "rate", "402", "payment", "quota")):
                    current_llm_idx = (current_llm_idx + 1) % len(TOOL_LLMS)
                    continue
                raise RuntimeError(f"{provider} tool model failed: {e}") from e
        raise RuntimeError(
            "All tool models unavailable: " + " | ".join(failures))
    return worker


def chat(state: TarzState) -> dict:
    return {"messages": [llm_plain.invoke(sanitize_for_plain_llm(state["messages"]))]}


def sanitize_for_plain_llm(messages) -> list:
    out = []
    for m in messages:
        if isinstance(m, (SystemMessage, HumanMessage)):
            out.append(m)
        elif isinstance(m, AIMessage):
            if getattr(m, "tool_calls", None):
                calls = ", ".join(
                    f"{tc['name']}({tc.get('args', {})})" for tc in m.tool_calls)
                out.append(AIMessage(content=f"[called tool(s): {calls}]"))
            elif m.content:
                out.append(AIMessage(content=m.content))
        elif isinstance(m, ToolMessage):
            name = getattr(m, "name", None) or "tool"
            out.append(AIMessage(content=f"[{name} result: {m.content}]"))
    return out


graph = StateGraph(TarzState)
graph.add_node("router", router)
graph.add_node("chat", chat)
graph.add_node("supervisor", supervisor)


for domain, tools in DOMAIN_TOOLS.items():
    if domain in ("dedicated_tool_telegram", "dedicated_tool_discord", "dedicated_tool_browser"):
        continue   # not real yet — don't route anywhere until they have real tools
    tools_node = f"{domain}_tools"
    graph.add_node(domain, make_worker(tools))
    graph.add_node(tools_node, ToolNode(tools + [done]))
    graph.add_conditional_edges(domain, route_from_worker, {
        "tools": tools_node,
        "supervisor": "supervisor",
        "router": "router",
    })
    graph.add_edge(tools_node, domain)

graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", route_from_supervisor, {
    "route": "router", "finished": END
})

graph.add_conditional_edges("router", route_edge, {
    "chat": "chat", "system": "system", "gui_control": "gui_control",
    "info": "info", "communication": "communication",
    "dedicated_tool_spotify": "dedicated_tool_spotify", "calendar": "calendar",
    "media": "media_control",
})
graph.add_edge("chat", END)

app = graph.compile(checkpointer=MemorySaver())

png = app.get_graph().draw_mermaid_png()

with open("langgraph.png", "wb") as f:
    f.write(png)

print("✅ Graph saved as langgraph.png")


def extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


@traceable(
    name="tarz_playground_request",
    run_type="chain",
    metadata={
        "application": "tarz-ai",
        "source_file": "playground/cl.py",
        "workflow": "langgraph",
    },
)
def think(user_input: str) -> str:
    """Run one playground CLI request as a traceable LangSmith workflow."""
    memories = hybrid_retrieve(user_input, n=5)
    memory_text = "\n".join(f"- {m}" for m in memories) if memories else "None"

    system = SYSTEM + f"""

Relevant memories:
{memory_text}
"""

    result = app.invoke({
        "messages": [
            SystemMessage(content=system, id="system"),
            HumanMessage(content=user_input)
        ],
        "steps": 0,
        "worker_steps": 0,
    }, config={"configurable": {"thread_id": "user"}})

    response = extract_text(result["messages"][-1].content)

    save_imp_context(user_input, response)
    return response


def main():
    while True:
        try:
            user_input = listen()
        except EOFError:

            break

        if not user_input:
            continue

        print("TARZ:", think(user_input))


if __name__ == "__main__":
    main()
