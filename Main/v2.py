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
from dotenv import load_dotenv
from langsmith import traceable
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_cerebras import ChatCerebras
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
from Actions.execute_action import click, type_text, press_key, read_screen, open_app, volume_control, wether_app, use_shortcut, set_alarm, set_timer, translate, clipboard, wait, done
from Tools.memory import save_imp_context
from Tools.rag import hybrid_retrieve
from Tools.media_control import media_play_pause, media_next_track, media_previous_track
from pydantic import BaseModel, Field

print("[Init] 3 - rag...")
load_dotenv()

os.environ.setdefault("LANGSMITH_HIDE_INPUTS", "false")
os.environ.setdefault("LANGSMITH_HIDE_OUTPUTS", "false")

api_key = os.getenv("GROQ_API_KEY")
api_cb = os.getenv("CEREBRAS_API_KEY")


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
    browser_open_url,
    telegram_message_user,


]

print("[Init] Setting up LLMs...")

llm_plain = ChatGroq(
    api_key=api_key,
    temperature=0.7,
    model="openai/gpt-oss-120b"
)


def listen():
    return input("You:")


TOOL_LLMS = [
    ChatGroq(model="openai/gpt-oss-120b", api_key=os.getenv("GROQ_API_KEY")),
    ChatGoogleGenerativeAI(model="gemini-2.5-flash",
                           google_api_key=os.getenv("GEMINI_KEY_5"), temperature=0.2),
    ChatCerebras(model="gpt-oss-120b",
                 api_key=os.getenv("CEREBRAS_API_KEY2"), temperature=0.2),

]


class TarzState(TypedDict):
    messages: Annotated[list, add_messages]
    steps: int
    worker_steps: int
    next: str
    supervisor_instruction: str


MAX_WORKER_STEPS = 6
MAX_STEPS = 12


class SupervisorDecision(BaseModel):
    next: Literal["finished", "route"] = Field(
        description="Whether the task is finished or another action is required."
    )
    reasoning: str = Field(
        description="Brief explanation of why this decision was made.")
    instruction: str = Field(
        description="If another action is needed, describe what should be attempted next.")


def verify_completion(expected_outcome: str) -> dict:
    answer = vision_verify_system(
        f"Is this currently true on screen: {expected_outcome}? "
        f"Answer yes or no, then briefly explain why."
    )
    confirmed = answer.strip().upper().startswith("YES")
    return {"confirmed": confirmed, "detail": answer}


# take a messy mix of SM/HM/AIM /ToolM/ and flatten it so every single message ends up as a plain readable string, safe to join
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
                out.append(AIMessage(content=extract_text(m.content)))
        elif isinstance(m, ToolMessage):
            name = getattr(m, "name", None) or "tool"
            out.append(AIMessage(content=f"[{name} result: {m.content}]"))
    return out


NO_VERIFY_KEYWORDS = [
    "pause", "resume", "unpause",
    "volume up", "volume down", "increase volume", "decrease volume", "play",
    "mute", "unmute", "next track", "previous track", "skip song", "skip track",
]

INFO_ONLY_TOOLS = {
    "read_screen", "rt_data", "wether_app", "news_update",
    "list_events", "search_emails", "read_emails", "translate",
}


def needs_vision_verification(user_request: str) -> bool:
    text = user_request.lower()
    return not any(phrase in text for phrase in NO_VERIFY_KEYWORDS)


def supervisor(state: TarzState) -> dict:
    steps = state.get("steps", 0)
    if steps >= MAX_STEPS:
        return {"next": "finished", "steps": steps + 1}
    if steps == 0:
        return {"next": "route", "steps": steps + 1, "supervisor_instruction": ""}

    recent = state["messages"][-6:]
    tool_ran = any(isinstance(m, ToolMessage) for m in recent)
    if not tool_ran:
        return {"next": "finished", "steps": steps + 1}

    ran_tool_names = {
        tc["name"]
        for m in recent if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
        for tc in m.tool_calls
    }
    if ran_tool_names and ran_tool_names.issubset(INFO_ONLY_TOOLS):
        return {"next": "finished", "steps": steps + 1, "supervisor_instruction": ""}

    user_request = next(
        (m.content for m in reversed(
            state["messages"]) if isinstance(m, HumanMessage)), ""
    )
    if not needs_vision_verification(user_request):
        print("[Supervisor] Skipping vision check — non-visual task")
        return {"next": "finished", "steps": steps + 1, "supervisor_instruction": ""}
    # fixed: removed stray trailing "\"
    time.sleep(5)
    verification = verify_completion(user_request)
    recent_text = "\n".join(m.content for m in sanitize_for_plain_llm(recent))

    try:
        decision = llm_plain.with_structured_output(SupervisorDecision).invoke([
            SystemMessage(content=SUPERVISOR_PROMPT),
            HumanMessage(content=f"""
USER GOAL:
{user_request}

RECENT EXECUTION:
{recent_text}

SCREEN VERIFICATION:
{verification["detail"]}

VERIFICATION CONFIRMED:
{verification["confirmed"]}

CURRENT STEP:
{steps}

Decide what TARZ should do next.
""")
        ])
        # fixed: removed the duplicate un-evaluated "f\"RECENT EXECUTION:...\"" literal
        # that was sitting as dead text in the old prompt body
        next_step = decision.next
        instruction = decision.instruction
        print("\n🧠 SUPERVISOR:")
        print("Reason:", decision.reasoning)
        print("Instruction:", instruction)
        print("Next:", next_step)
    except Exception as e:
        print(f"[supervisor] decision failed, defaulting to finished: {e}")
        next_step = "finished"
        instruction = ""

    return {
        "next": next_step,
        "steps": steps + 1,
        "worker_steps": 0,
        "supervisor_instruction": instruction,
    }


def route_from_supervisor(state: dict) -> str:
    return state["next"]


def route_from_worker(state: TarzState) -> str:
    last = state["messages"][-1]
    if not getattr(last, "tool_calls", None):
        return "supervisor"
    if state.get("worker_steps", 0) >= MAX_WORKER_STEPS:
        return "supervisor"
    return "tools"


current_llm_idx = 0


def make_worker(all_tools):
    bound = all_tools + [done]

    def worker(state: TarzState) -> dict:
        global current_llm_idx
        wsteps = state.get("worker_steps", 0)
        failures = []

        supervisor_instruction = state.get("supervisor_instruction", "")
        messages = list(state["messages"])
        if supervisor_instruction:
            messages.append(SystemMessage(content=f"""
SUPERVISOR INSTRUCTION:

{supervisor_instruction}

Follow this instruction while completing the user's goal.

Important:
- Use the available tools to perform the requested action.
- If the supervisor provided a specific next strategy, prioritize it.
"""))

        for _ in range(len(TOOL_LLMS)):
            try:
                llm = TOOL_LLMS[current_llm_idx].bind_tools(bound)
                response = llm.invoke(messages)
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


graph = StateGraph(TarzState)
graph.add_node("supervisor", supervisor)
graph.add_node("agent", make_worker(ALL_TOOLS))
graph.add_node("agent_tools", ToolNode(ALL_TOOLS + [done]))

graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", route_from_supervisor, {
    "route": "agent", "finished": END
})
graph.add_conditional_edges("agent", route_from_worker, {
    "tools": "agent_tools",
    "supervisor": "supervisor",
})
graph.add_edge("agent_tools", "agent")

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
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


@traceable(
    name="tarz_playground_request",
    run_type="chain",
    metadata={"application": "tarz-ai",
              "source_file": "playground/cl.py", "workflow": "langgraph"},
)
def think(user_input: str) -> str:
    memories = hybrid_retrieve(user_input, n=5)
    memory_text = "\n".join(f"- {m}" for m in memories) if memories else "None"
    local_now = datetime.now().strftime("%A, %d %B %Y, %I:%M %p")

    system = SYSTEM + f"""

Current date and time: {local_now} (Asia/Kolkata)

Relevant memories:
{memory_text}
"""
    system = SYSTEM + \
        f"\n\nRelevant memories:\n{memory_text}\nCurrent date and time: {local_now} (Asia/Kolkata)\n"

    result = app.invoke({
        "messages": [SystemMessage(content=system, id="system"), HumanMessage(content=user_input)],
        "steps": 0,
        "worker_steps": 0,
        "supervisor_instruction": "",
    }, config={"configurable": {"thread_id": "user"}})

    response = extract_text(result["messages"][-1].content)
    threading.Thread(target=save_imp_context, args=(
        user_input, response), daemon=True).start()
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
