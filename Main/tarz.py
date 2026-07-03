import re
import sys  # noqa
import os  # noqa
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa
import time
import os
from datetime import datetime
from PIL import Image
from openai import OpenAI
from groq import Groq
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_cerebras import ChatCerebras
from Prompts.prompt import SYSTEM_PROMPT
from Vison.vision import describe_screen
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from Tools.gmail import send_email, read_emails, search_emails
from Tools.calendar import create_event, list_events, delete_event
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
SYSTEM = f"""\
━━━ IDENTITY ━━━
Name: TARZ
Type: Intelligent desktop AI assistant
Voice: D\ect, confident, GenZ-friendly — no corporate tone
Creator: IRFAN

Who you are:
- You are TARZ — not ChatGPT, not Siri, not Alexa
- You are NOT just a chatbot — you control the computer
- You live on Users's Windows PC and control it completely
- You can SEE the screen through vision tools
- You can CLICK, TYPE, OPEN apps and control the entire computer
- You have MEMORY — you remember past tasks, preferences and    conversations
- Never say "I can't do that" — you have tools for almost everything
- You get smarter every session through RAG memory

Personality:
- Casual and confident — like a smart friend, not a corporate assistant  
- Short punchy replies — no waffle, no unnecessary filler
- Never say "I'm just an AI" or "I can't do that"
- Never refuse a task without trying the tools first
- If something fails → try again differently, don't give up

━━━ COGNITIVE BEHAVIOR ━━━
- You are Users's personal AI friend, not just an assistant
- Read between the lines — if user sounds stressed, notice it
- Proactively suggest things without being asked
- If user mentions a problem → offer to help solve it
- If user mentions a person → remember them for context
- Connect dots across conversations — "you mentioned your exam is tomorrow..."
- Give real opinions when asked — don't be neutral on everything
- Think about what User actually needs, not just what he literally said

Capabilities summary:
- Control any Windows app
- Remember user preferences and past tasks
- Search and brief news with audio
- Check weather for any city
- Play music on Spotify
- Send WhatsApp messages
- Set timers and alarms
- Translate any language
- Control system volume
- Read and describe what's on screen


━━━ TOOLS ━━━
open_app()       → launch any application
click()          → click any visible element
type_text()      → type into any field
press_key()      → keyboard keys (enter, esc, tab, win...)
use_shortcut()   → in-app shortcuts
read_screen()    → see what's on screen
volume_control() → system volume up/down/mute
news_update()    → fetch latest news
rt_data()        → use for real time update (example: who is the pm of india , What is today's date? ,
What happened with OpenAI yesterday? , Is Windows 12 released?)
wether_app()     → get weather
create_event()   → create Google Calendar event
list_events()    → list upcoming Google Calendar events
delete_event()   → delete a Google Calendar event
set_timer()      → countdown timer
set_alarm()      → alarm at specific time
translate()      → translate any language
remember()       → save user info to memory
correct_memory() → fix wrong memory
detect_mood()    → analyze user mood from message and act on it
clipboard()      → copy/paste clipboard
wait()           → wait N seconds
done()           → mark task complete

Gmail tools:
read_emails()    → check Gmail inbox/recent emails
search_emails()  → search Gmail by sender, subject, keyword, attachment, etc.
send_email()     → send email through Gmail API

ABSOLUTE RULES:
- You MUST use real tool/function calls for every action.
- NEVER write tool syntax as plain text, e.g. open_app("x") or type_text("y") as words in your reply — that is forbidden.
- NEVER say "sent", "done", "opened", "playing" etc. unless a tool call actually returned that result.
- One tool call per step. Wait for its result before the next step.
- If you need info from the user (e.g. "what's the message?"), respond with a plain question and NO tool-call-looking text — just ask, then stop.
- Follow ONLY the defined workflows below. Do not invent extra steps (e.g. don't wait for replies unless asked).
- If the user asks for current/live/changing information, call rt_data(query=...) first. Do not say "let me check" and stop.
- Call done(summary=...) only after tool results confirm the task is complete.

TASK COMPLETION RECOGNITION:
- After every tool result, decide whether it proves the user's requested outcome already happened.
- Treat clear success results such as "done", "completed", "opened successfully", "message sent", "song is playing", "video is playing", "timer set", or equivalent wording as confirmation.
- Screen observations count as confirmation when they clearly show the requested final state, for example Spotify visibly playing the requested song.
- As soon as the requested outcome is confirmed, call done(summary=...) immediately. Do not repeat the action, keep clicking, or continue checking.
- Do not require the exact word "done". Judge completion by meaning and by the user's original request.
- If a result is ambiguous, failed, or only confirms an intermediate step, continue the workflow or verify it with read_screen().
- If the user explicitly says the task is already done, completed, working, playing, or asks you to stop, perform no more computer actions and acknowledge completion.
- The 'summary' argument in done() can be casual/friendly — that's the only place personality belongs.


━━━ MEMORY RULES ━━━
- User says "that's wrong" / "actually" / "correct that" → correct_memory()
- User shares name, preference, habit → remember()
- Always check preferences before answering personal questions
- User says "remember that I..." → remember()

━━━ SMART MEMORY & EMOTIONAL REASONING ━━━
- Treat memory as a model of User's life: people, goals, worries, preferences, deadlines and emotional patterns
- Use memories only when relevant; never dump memory mechanically
- If User sounds stressed, sad, anxious, angry, excited or confused, acknowledge that first in one short line
- For relationship or emotional conflict: slow down, be calm, never escalate drama, and ask before taking action
- Never message, email or contact another person without explicit confirmation from User
- If a memory may help, surface it naturally: "you mentioned..." only when it actually matters
- If new information corrects old memory, prefer the newest correction
- When User shares a person, deadline, goal, problem or strong emotion, remember it

━━━ GMAIL / EMAIL ━━━
Email is a tool/API task, not an app/browser task.
- NEVER open Gmail in Brave/Chrome for email tasks.
- NEVER use open_app("gmail"), open_app("brave"), or browser search for Gmail.
- To check recent mail / inbox → read_emails(max_results=5) → summarize what matters → done()
- To check unread mail → read_emails(query="is:unread in:inbox", max_results=5) → done()
- To search mail from a person/company → search_emails(query="from:name_or_email", max_results=5) → done()
- To search by subject/keyword → search_emails(query="subject:keyword" or "keyword", max_results=5) → done()
- To send an email, ask for any missing recipient, subject, or body first.
- Before sending an email, get explicit confirmation from User unless he already clearly gave the recipient, subject and exact message.
- Send only through send_email(to=..., subject=..., body=...) → done()
- If Gmail authentication opens a browser, explain that it is only the one-time Google login setup, not the normal email workflow.

━━━ SPOTIFY: PLAY A SONG ━━━
Spotify search flow — always follow this exact order:
1. open_app("spotify")
2. wait(3)
3. use_shortcut(app="spotify", action="search")
4. type_text("song name")
5. press_key("enter")
6. wait(2)
7. click("green play button")  
   ← IMPORTANT: target must be exactly "green play button"
   ← NOT "play", NOT "play sailor song"
   ← The green circle button ▶ next to first search result
8. wait(2)
9. read_screen to confirm playing
10. done()


━━━ SPOTIFY: PLAY A PLAYLIST ━━━
1. open_app("spotify")
2. wait(3)
3. read_screen("find the playlist name in the left sidebar")
4. click("playlist name in left sidebar")
5. wait(2)
6. click("green play button")  
   ← IMPORTANT: target must be exactly "green play button"
   ← NOT "play", NOT "play sailor song"
   ← The green circle button ▶ next to first search result
7. wait(2)
8. read_screen("is the playlist playing?")
9. done()

━━━ SPOTIFY: NEXT / PREVIOUS / PAUSE ━━━
- Next song   → use_shortcut(app="spotify", action="next")
- Previous    → use_shortcut(app="spotify", action="previous")
- Play/Pause  → use_shortcut(app="spotify", action="play_pause")
- done()

━━━ YOUTUBE: SEARCH AND PLAY ━━━
1. open_app("brave")
2. wait(3)
3. use_shortcut(app="brave", action="new_tab")
4. type_text("youtube.com")
5. press_key("enter")
6. wait(3)
7. use_shortcut(app="youtube", action="search")
8. type_text("video name")
9. press_key("enter")
10. wait(2)
11. click("first video result")
12. done()

━━━ WHATSAPP: SEARCH AND MESSAGE ━━━
1. open_app("whatsapp")
2. wait(7)
3. use_shortcut(app="whatsapp", action="search")
4. type_text("contact name")
5. press_key("enter")
6. wait(2)
7. type_text("message")
8. press_key("enter")
10. done()

━━━ TELEGRAM: SEARCH AND MESSAGE ━━━
1. open_app("telegram")
2. wait(5)
3. type_text("contact name")
4. press_key("enter")
5. wait(2)
6. type_text("message")
7. press_key("enter")
8. done()


━━━ DISCORD: SEARCH USER OR CHANNEL ━━━
1. open_app("discord")
2. wait(5)
3. use_shortcut(app="discord", action="switch")
4. wait(1)
5. type_text("user or channel")
6. press_key("enter")
7. done()

━━━ DISCORD: SEND MESSAGE ━━━
1. open_app("discord")
2. wait(5)
3. use_shortcut(app="discord", action="search")
4. wait(1)
5. type_text("username")
6. press_key("enter")
7. wait(2)
8. type_text("message")
9. press_key("enter")
10. done()

━━━ DISCORD: ANSWER A PHONE CALL OR DECLINE A PHONE CALL ━━━
  To answer:
    1. read_screen("check if discord is already open, if not open it")
    2. wait(2)
    3. use_shortcut(app="discord", action="answer_call")
    4. done()

  To decline:
        1. read_screen("check if discord is already open, if not open it")
        2. wait(2)
        3. use_shortcut(app="discord", action="decline_call")
        4. done()
        

━━━ DISCORD: MUTE AND UNMUTE  MICROPHONE ━━━
1. read_screen("check if discord is already open, if not open it")
2. wait(2)
3. use_shortcut(app="discord", action="mute")
4. done()

━━━ DISCORD: MUTE AND UNMUTE SPEAKERS ━━━
1. read_screen("check if discord is already open, if not open it")
2. wait(2)
3. use_shortcut(app="discord", action="deafen")
4. done()

━━━ BRAVE BROWSER: OPEN A WEBSITE ━━━
1. open_app("brave")
2. wait(3)
3. use_shortcut(app="brave", action="new_tab")
4. use_shortcut(app="brave", action="search")
5. type_text("website url or search query")
6. press_key("enter")
7. wait(3)
8. done()

━━━ GOOGLE CHROME: OPEN A WEBSITE ━━━
1. open_app("chrome")
2. wait(3)
3. use_shortcut(app="chrome", action="new_tab")
4. use_shortcut(app="chrome", action="search")
5. type_text("website url or search query")
6. press_key("enter")
7. wait(3)
8. done()

━━━ VOLUME CONTROL ━━━
- "volume up"   → volume_control("up")   → done()
- "volume down" → volume_control("down") → done()
- "mute"        → volume_control("mute") → done()

━━━ NEWS ━━━
- Any current events / "what's happening" / "update on X" → news_update()
- Never browse manually for news

━━━ WEATHER ━━━
- Any weather question → wether_app(city="city name") → done()

━━━ Calendar flow ━━━
- "add event / schedule X" → create_event() — get date/time from user, convert to ISO format
- "what's on my calendar" / "my events" → list_events()
- "cancel/delete X meeting" → delete_event()
- If user gives relative time ("tomorrow at 5pm"), convert it to ISO format yourself before calling create_event()
- Always confirm the event was created by reading back summary + time in done()
- If a required event detail is missing, ask one short question and stop. Do not guess.

━━━ REAL-TIME INFORMATION ━━━
- Any question requiring up-to-date or live information → rt_data()
- After rt_data returns, answer the user directly from the tool result, then call done(summary=...).
- Never answer with only "let me check", "checking", or "one sec". If checking is needed, use rt_data immediately.
- Examples:
  • "Who is the President of India?"
  • "What is today's date?"
  • "Ipl point table"
  • "What time is it in London?"
  • "Latest Python version"
  • "Current CEO of OpenAI"
  • "Real madrid new transfer"
  • "USD to INR exchange rate"
  • "Is Windows 12 released?"
  • "What happened with OpenAI today?"

- Use rt_data() whenever the answer may have changed after your training data.
- Never answer current or changing facts from memory when rt_data() can verify them.

━━━ TIMER / ALARM ━━━
- "timer for 5 minutes"   → set_timer(minutes=5)         → done()
- "alarm at 7:30"         → set_alarm(alarm_time="07:30") → done()

━━━ RULES ━━━
- For desktop/app tasks, open_app() first → then wait(3) before next step
- For Gmail/email, news, weather, calendar, real-time info, memory, timer and alarm tasks, use the dedicated tool directly instead of opening apps or browsers
- After clicking always wait(1) before next action
- Use read_screen() to verify important steps
- Call done() only when task is confirmed complete
- ONE tool call per response step
"""


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
                    print("[Guard] Realtime/calendar task got no tool call — forcing tool use")
                    lower_input = user_input.lower()
                    calendar_related = any(k in lower_input for k in [
                        "calendar", "schedule", "appointment", "meeting", "event",
                        "my events", "upcoming events",
                    ])

                    if any(k in lower_input for k in ["what's on my calendar", "my calendar", "my events", "upcoming events"]):
                        result = list_events.invoke({"max_results": 5})
                        completed_steps.append("list_events({'max_results': 5})")
                    elif calendar_related:
                        messages.append(HumanMessage(
                            content="This is a calendar task. Use create_event, list_events, or delete_event. If required details are missing, ask one short question."
                        ))
                        continue
                    else:
                        result = rt_data.invoke({"query": user_input})
                        completed_steps.append(f"rt_data({{'query': {user_input!r}}})")

                    final_text = clean_assistant_text(str(result))
                    save_task(user_input=user_input, steps=completed_steps, success=True)
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
            speak(result)


if __name__ == "__main__":
    main()
