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
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_cerebras import ChatCerebras
from Prompts.prompt import SYSTEM_PROMPT, SYSTEM
from Vison.vision import describe_screen
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from Tools.gmail import send_email, read_emails, search_emails
from Tools.calendar_tool import create_event, list_events, delete_event
from Tools.real_time_data import rt_data
from Audio.stt import listen as stt_listen
from Audio.tts import speak
from Tools.spotify_gui import spotify_play_song, spotify_play_playlist
print("on tools")  # noqa
from Actions.execute_action import type_text, press_key, open_app, read_screen, volume_control, news_update, wether_app, use_shortcut, set_alarm, set_timer, translate, clipboard, wait, done
from Tools.memory import save_imp_context
from Tools.rag import hybrid_retrieve
from Actions.execute_action import click
from pydantic import BaseModel

print("[Init] 3 - rag...")

load_dotenv()


api_key = os.getenv("GROQ_API_KEY")
api_or = os.getenv("OPENROUTER_KEY")
api_cb = os.getenv("CEREBRAS_API_KEY")


TOOLS = [click, type_text, press_key, open_app,
         read_screen, news_update, wether_app, volume_control, use_shortcut, set_alarm, send_email, read_emails, search_emails, set_timer, spotify_play_song, spotify_play_playlist, translate,  rt_data, clipboard, create_event, list_events, delete_event, wait, done]


TOOL_TEXT_PATTERN = re.compile(
    r'(?<![\w.])(?:'
    + '|'.join(re.escape(tool.name) for tool in TOOLS)
    + r')\s*\([^)]*\)',
    re.IGNORECASE
)


def clean_assistant_text(text: str) -> str:
    """Remove leaked tool-call syntax from text shown/spoken to the user."""
    if not text:
        return ""
    text = TOOL_TEXT_PATTERN.sub("", str(text))
    text = re.sub(r'\s+([,.!?])', r'\1', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


print("[Init] Setting up LLMs...")
llm_tools = ChatCerebras(
    model="gpt-oss-120b",
    api_key=api_cb,
    temperature=0.2
).bind_tools(TOOLS)
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


api_openai = os.getenv("GITHUB_TOKEN")


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
    intent: Literal["tool", "chat"]


class MessageClassifier(BaseModel):
    message_category: Literal[]


def router(state: TarzState) -> dict:
    last_msg = state["messages"][-1].content
    reply = llm_plain.invoke([
        SystemMessage(content=(
            "Reply with exactly one word: TOOL or CHAT.\n"
            "TOOL = needs computer control, an action, or real-time info.\n"
            "CHAT = normal conversation, no action needed."
        )),
        HumanMessage(content=last_msg)
    ]).content.upper()
    return {"intent": "tool" if "TOOL" in reply else "chat"}


def route_edge(state: TarzState) -> str:
    return state["intent"]


current_llm_idx = 0


def agent(state: TarzState) -> dict:
    global current_llm_idx
    failures = []
    for _ in range(len(TOOL_LLMS)):
        try:
            llm = TOOL_LLMS[current_llm_idx].bind_tools(TOOLS)
            response = llm.invoke(state["messages"])
            return {"messages": [response]}
        except Exception as e:
            provider = type(TOOL_LLMS[current_llm_idx]).__name__
            err = str(e).lower()
            failures.append(f"{provider}: {str(e)[:300]}")
            if "429" in err or "rate" in err or "402" in err or "payment" in err or "quota" in err:
                current_llm_idx = (current_llm_idx + 1) % len(TOOL_LLMS)
                continue
            raise RuntimeError(f"{provider} tool model failed: {e}") from e

    # Never return None here. Returning no graph update leaves the user's
    # HumanMessage as the last message, which makes TARZ echo the input.
    raise RuntimeError(
        "All tool models are unavailable. Provider errors: " +
        " | ".join(failures)
    )


def chat(state: TarzState) -> dict:
    return {"messages": [llm_plain.invoke(state["messages"])]}


graph = StateGraph(TarzState)
graph.add_node("router", router)
graph.add_node("agent", agent)
graph.add_node("tools", ToolNode(TOOLS))
graph.add_node("chat", chat)

graph.add_edge(START, "router")
graph.add_conditional_edges("router", route_edge, {
                            "tool": "agent", "chat": "chat"})
graph.add_conditional_edges("agent", tools_condition)
graph.add_edge("tools", "agent")
graph.add_edge("chat", END)


app = graph.compile()


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


def think(user_input: str) -> str:
    memories = hybrid_retrieve(user_input, n=5)
    memory_text = "\n".join(f"- {m}" for m in memories) if memories else "None"

    system = SYSTEM + f"""

Relevant memories:
{memory_text}
"""

    result = app.invoke({"messages": [
        SystemMessage(content=system, id="system"),
        HumanMessage(content=user_input)

    ]}, config={"configurable": {"thread_id": "user"}})

    response = extract_text(result["messages"][-1].content)

    save_imp_context(user_input, response)
    return response


def main():
    while True:
        try:
            user_input = listen()
        except EOFError:
            # Allows clean exit when input is piped or the terminal closes.
            break

        if not user_input:
            continue

        print("TARZ:", think(user_input))


if __name__ == "__main__":
    main()
