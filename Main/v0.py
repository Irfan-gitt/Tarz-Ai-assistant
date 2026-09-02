"""OLD VERSION OF TARZ - NOT USED ANYMORE"""
import re
import sys  # noqa
import os  # noqa
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa
import time
import os
from datetime import datetime
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_cerebras import ChatCerebras
from Prompts.prompt import SYSTEM
from Vison.vision import describe_screen
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from Tools.gmail import send_email, read_emails, search_emails
from Tools.calendar_tool import create_event, list_events, delete_event
from Tools.real_time_data import rt_data
from Audio.stt import listen as stt_listen
from Audio.tts import speak
print("on tools")  # noqa
from Actions.execute_action import type_text, press_key, open_app, read_screen, volume_control, news_update, wether_app, use_shortcut, set_alarm, set_timer, translate, remember, clipboard, detect_mood, correct_memory, wait, done
from Tools.memory import save_task, retrieve_similar_task, retrieve_similar_chats, get_recent_tasks, get_all_preferences, save_conversation, get_recent_conversations, build_memory_context, auto_extract_memories

from Actions.execute_action import click
print("[Init] 3 - rag...")

load_dotenv()


api_key = os.getenv("groq_api")
api_or = os.getenv("OPENROUTER_KEY")
api_cb = os.getenv("CEREBRAS_API_KEY")


TOOLS = [click, type_text, press_key, open_app,
         read_screen, news_update, wether_app, volume_control, use_shortcut, set_alarm, send_email, read_emails, search_emails, set_timer, translate, correct_memory, rt_data, detect_mood, clipboard, create_event, list_events, delete_event, remember, wait, done]


FAKE_TOOL_PATTERN = re.compile(r'\b[a-z_]+\([^)]*\)')
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


def should_use_tool_router(user_input: str) -> bool:
    """Fast keyword router for tool tasks the LLM router often mislabels as chat."""
    text = user_input.lower()

    calendar_keywords = [
        "calendar", "schedule", "appointment", "meeting", "event",
        "remind me on", "what's on my calendar", "upcoming events",
        "delete event", "cancel meeting",
    ]
    realtime_keywords = [
        "today's date", "todays date", "what date", "current date",
        "what day is it", "current time", "what time is it",
        "latest", "current", "right now", "today", "yesterday",
        "tomorrow", "price", "exchange rate", "stock", "crypto",
        "who is the president", "who is the prime minister",
        "ceo of", "released", "ipl", "score", "points table",
    ]

    return any(k in text for k in calendar_keywords + realtime_keywords)


print("[Init] Setting up LLMs...")
llm_tools = ChatCerebras(
    model="gpt-oss-120b",
    api_key=api_cb,
    temperature=0.2
).bind_tools(TOOLS)
try:
    llm_plain = ChatGroq(
        api_key=os.getenv("groq_api"),
        temperature=0.7,
        model="llama-3.3-70b-versatile"
    )

except Exception as e:
    print(f"An error occurred while setting up LLMs: {e}")
    llm_plain = ChatGroq(
        api_key=os.getenv("groq_api"),
        temperature=0.7,
        model="llama-3.3-70b-versatile"
    )


api_openai = os.getenv("GITHUB_TOKEN")


def listen():
    user_input = input("You:")
    return user_input
    # return stt_listen()


def is_computer_task(user_input: str) -> bool:

    memory_keywords = ["do you remember", "did i ask", "what did i",
                       "what task", "do you know my", "what is my"]
    if any(k in user_input.lower() for k in memory_keywords):
        return False
    if should_use_tool_router(user_input):
        return True
    response = llm_plain.invoke([
        SystemMessage("""Reply only YES or NO.

Should this use computer control tools?
YES for: opening apps, clicking, typing, searching web, playing music, news lookup, real-time facts, today's date/time, calendar tasks, Gmail/email reading or sending, volume, any task on computer
NO for: pure conversation, jokes, math, general knowledge questions with no action needed

Be generous with YES - when in doubt say YES."""),
        HumanMessage(user_input)
    ])
    return "YES" in response.content.upper()


def build_conversation_history():
    past = get_recent_conversations(10)
    history = []
    for meta, _ in past:
        history.append({"role": "user", "content": meta["user"]})
        history.append({"role": "assistant", "content": meta["tarz"]})
    return history


print("[Init] Building conversation history...")
conversation_history = [
    SystemMessage(content=SYSTEM_PROMPT)
] + build_conversation_history()

TOOL_LLMS = [

    ChatOpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=api_openai,
        model="gpt-4o-mini"
    ),

    ChatCerebras(
        model="gpt-oss-120b",
        api_key=os.getenv("CEREBRAS_API_KEY"),
        temperature=0.2
    ),




    ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=os.getenv("GEMINI_KEY_5"),
        temperature=0.2
    ),
]


current_llm_idx = 0


def get_llm_tools():
    global current_llm_idx  # 🙂
    llm = TOOL_LLMS[current_llm_idx]
    return llm.bind_tools(TOOLS)


_pending_messages = None


def think(user_input):

    global _pending_messages  # iknow its bad practise iam just lazyyyy🙂

    similar_tasks = retrieve_similar_task(user_input, n=3)
    similar_chats = retrieve_similar_chats(user_input, n=5)
    recent_tasks = get_recent_tasks(5)
    prefs = get_all_preferences()
    smart_memory_context = build_memory_context(user_input)
    local_now = datetime.now().strftime("%A, %d %B %Y, %I:%M %p")

    # Build memory context(Ai code - to clean up)

    tasks_text = "\n".join([
        f"- '{t['task']}' → {t['steps']}"
        for t in recent_tasks
    ]) if recent_tasks else "None"

    similar_text = "\n".join([
        f"- '{t['task']}' → steps: {t['steps']}"
        for t in similar_tasks if isinstance(t, dict) and t.get("success") == "True"
    ]) if similar_tasks else "None"

    similar_chats_text = "\n".join([
        f"- User: {chat.get('user', '')} | TARZ: {chat.get('tarz', '')}"
        for chat in similar_chats if isinstance(chat, dict)
    ]) if similar_chats else "None"

    prefs_text = "\n".join([
        f"- {k}: {v}" for k, v in prefs.items()
    ]) if prefs else "None"

    SYSTEM_WITH_MEMORY = SYSTEM + f"""

Current local date/time for Irfan:
{local_now} (Asia/Kolkata)

User Preferences:
{prefs_text}

Recent completed tasks (for context only, don't copy steps exactly):
{chr(10).join([f"- {t['task']}" for t in recent_tasks])
     if recent_tasks else "None"}

Similar completed tasks:
{similar_text}

Similar past conversations:
{similar_chats_text}

{smart_memory_context}

"""
    if _pending_messages:
        messages = _pending_messages
        messages.append(HumanMessage(content=user_input))
    else:
        messages = [
            SystemMessage(content=SYSTEM_WITH_MEMORY),
            HumanMessage(content=user_input)
        ]
    if not is_computer_task(user_input):

        recent_tasks = get_recent_tasks(10)
        tasks_text = "\n".join([
            f"- {t['task']}" for t in recent_tasks
        ]) if recent_tasks else "None"

        conversation_history[0] = SystemMessage(content=SYSTEM_WITH_MEMORY + f"""

Tasks you have completed for this user:
{tasks_text}

Use this when user asks what you did, what tasks were completed, or you can use it to make more accurate results next time ,etc.
"""
                                                )

        conversation_history.append({"role": "user", "content": user_input})
        response = clean_assistant_text(
            llm_plain.invoke(conversation_history).content)
        conversation_history.append({"role": "assistant", "content": response})
        save_conversation(user_input, response)
        auto_extract_memories(user_input, response)
        return response
    print("[Router] Task detected → tool LLM")
    completed_steps = []
    last_result = ""

    global current_llm_idx

    for step in range(15):
        for attempt in range(len(TOOL_LLMS)):
            try:
                llm = get_llm_tools()
                response = llm.invoke(messages)
                break
            except Exception as e:
                if "429" in str(e) or "rate" in str(e).lower():
                    current_llm_idx = (current_llm_idx + 1) % len(TOOL_LLMS)
                    print(
                        f"[Rate limit] Switching to model {current_llm_idx + 1}")
                    time.sleep(1)
                    continue
                print(f"[LLM Error] {e}")
                break

        messages.append(response)

        if not response.tool_calls:
            if FAKE_TOOL_PATTERN.search(response.content) and not completed_steps:
                print("[Guard] Hallucinated tool syntax detected — forcing retry")
                messages.append(HumanMessage(
                    content="Do not write function syntax as text. Either call the actual tool, or ask a plain question with no code-like text."
                ))
                continue

            if should_use_tool_router(user_input) and not completed_steps:
                final_text = clean_assistant_text(response.content)
                if not final_text or any(phrase in final_text.lower() for phrase in ["let me check", "checking", "one sec", "wait a sec"]):
                    print(
                        "[Guard] Realtime/calendar task got no tool call — forcing tool use")
                    lower_input = user_input.lower()
                    calendar_related = any(k in lower_input for k in [
                        "calendar", "schedule", "appointment", "meeting", "event",
                        "my events", "upcoming events",
                    ])

                    if any(k in lower_input for k in ["what's on my calendar", "my calendar", "my events", "upcoming events"]):
                        result = list_events.invoke({"max_results": 5})
                        completed_steps.append(
                            "list_events({'max_results': 5})")
                    elif calendar_related:
                        messages.append(HumanMessage(
                            content="This is a calendar task. Use create_event, list_events, or delete_event. If required details are missing, ask one short question."
                        ))
                        continue
                    else:
                        result = rt_data.invoke({"query": user_input})
                        completed_steps.append(
                            f"rt_data({{'query': {user_input!r}}})")

                    final_text = clean_assistant_text(str(result))
                    save_task(user_input=user_input,
                              steps=completed_steps, success=True)
                    auto_extract_memories(user_input, final_text)
                    return final_text if final_text else "Done."

            final_text = clean_assistant_text(response.content)
            if completed_steps:
                save_task(user_input=user_input,
                          steps=completed_steps, success=True)
                auto_extract_memories(user_input, final_text)
                print(f"[Memory] Auto-saved: {user_input}")
            return final_text if final_text else ("Done." if completed_steps else "")

        for tool_call in response.tool_calls:
            name = tool_call["name"]
            args = tool_call["args"]
            completed_steps.append(f"{name}({args})")

            tool_fn = next((t for t in TOOLS if t.name == name), None)
            result = tool_fn.invoke(
                args) if tool_fn else f"Unknown tool: {name}"
            last_result = result

            print(f" -> {result}")

            if name == "done":
                save_task(user_input=user_input,
                          steps=completed_steps, success=True)
                final_text = clean_assistant_text(args.get("summary", result))
                auto_extract_memories(user_input, final_text)
                print(f"[Memory] Saved: {user_input}")
                return final_text if final_text else "Done."

            messages.append(ToolMessage(content=str(result),
                            tool_call_id=tool_call["id"]))
            time.sleep(0.5)
    if not response.tool_calls:
        final_text = clean_assistant_text(response.content)
        # LLM asking a question — keep messages for next turn
        if "?" in final_text:
            _pending_messages = messages  # ← save state
        else:
            _pending_messages = None
        return final_text if final_text else "Done."
    save_task(user_input=user_input, steps=completed_steps, success=True)
    final_text = clean_assistant_text(last_result)
    auto_extract_memories(user_input, final_text)
    return final_text if final_text else "Max steps reached"


def main():
    while True:
        user_input = listen()
        if not user_input:
            continue
        result = think(user_input)
        print(f"TARZ: {result}")
        if any(tool in result for tool in ["wether_app()", "news_update()",
                                           "open_app()", "click()"]):
            pass
        else:
            print(result)


if __name__ == "__main__":
    main()
