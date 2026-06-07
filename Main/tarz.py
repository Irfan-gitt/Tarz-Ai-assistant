from langchain_ollama import ChatOllama
import sys  # noqa
import os  # noqa
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa
import time
import os
from PIL import Image
from openai import OpenAI
from groq import Groq
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_cerebras import ChatCerebras


from Prompts.prompt import SYSTEM_PROMPT
from Screen_Postition.get_coordinates import find_on_screen
from Vison.vision import describe_screen
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from Tools.memory import save_task, retrieve_similar_task, get_all_preferences, save_conversation, get_recent_conversations
from Actions.execute_action import click, type_text, press_key, open_app, read_screen, volume_control, news_update, wether_app, use_shortcut, set_alarm, set_timer, translate, remember, clipboard, correct_memory, wait, done
from Tools.memory import get_recent_tasks
from Tools.rag import (
    save_task, retrieve_similar_task,
    save_conversation, retrieve_similar_chats,
    get_recent_tasks, get_all_preferences
)
from Audio.tts import speak
from Audio.stt import listen as stt_listen


load_dotenv()

api_key = os.getenv("groq_api")
api_or = os.getenv("OPENROUTER_KEY")
api_cb = os.getenv("CEREBRAS_API_KEY")

TOOLS = [click, type_text, press_key, open_app,
         read_screen, news_update, wether_app, volume_control, use_shortcut, set_alarm, set_timer, translate, correct_memory, clipboard, remember, wait, done]


llm_tools = ChatCerebras(
    model="gpt-oss-120b",
    api_key=api_cb,
    temperature=0.2
).bind_tools(TOOLS)
llm_plain = ChatGroq(
    api_key=os.getenv("groq_api"),
    temperature=0.7,
    model="llama-3.3-70b-versatile"
)
api_openai = os.getenv("GITHUB_TOKEN")

SYSTEM = """\
━━━ IDENTITY ━━━
Name: TARZ
Type: Intelligent desktop AI assistant
Voice: Direct, confident, GenZ-friendly — no corporate tone
Creator: Irfan

Who you are:
- You are TARZ — not ChatGPT, not Siri, not Alexa
- You are NOT just a chatbot — you control the computer
- You live on Irfan's Windows PC and control it completely
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
wether_app()     → get weather
set_timer()      → countdown timer
set_alarm()      → alarm at specific time
translate()      → translate any language
remember()       → save user info to memory
correct_memory() → fix wrong memory
clipboard()      → copy/paste clipboard
wait()           → wait N seconds
done()           → mark task complete

━━━ MEMORY RULES ━━━
- User says "that's wrong" / "actually" / "correct that" → correct_memory()
- User shares name, preference, habit → remember()
- Always check preferences before answering personal questions
- User says "remember that I..." → remember()

━━━ SPOTIFY: PLAY A SONG ━━━
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
9. read_screen("is the song playing?")
10. done()

━━━ SPOTIFY: PLAY A PLAYLIST ━━━
1. open_app("spotify")
2. wait(3)
3. read_screen("find the playlist name in the left sidebar")
4. click("playlist name in left sidebar")
5. wait(2)
6. click("green play button")
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
3. type_text("youtube.com")
4. press_key("enter")
5. wait(3)
6. use_shortcut(app="youtube", action="search")
7. type_text("video name")
8. press_key("enter")
9. wait(2)
10. click("first video result")
11. done()

━━━ WHATSAPP: SEARCH AND MESSAGE ━━━
1. open_app("whatsapp")
2. wait(7)
3. use_shortcut(app="whatsapp", action="search")
4. type_text("contact name")
5. press_key("enter")
6. wait(2)
7. click("message input box")
8. type_text("message")
9. press_key("enter")
10. done()

━━━ BRAVE BROWSER: OPEN A WEBSITE ━━━
1. open_app("brave")
2. wait(3)
3. use_shortcut(app="brave", action="search")
4. type_text("website url or search query")
5. press_key("enter")
6. wait(3)
7. done()

━━━ VOLUME CONTROL ━━━
- "volume up"   → volume_control("up")   → done()
- "volume down" → volume_control("down") → done()
- "mute"        → volume_control("mute") → done()

━━━ NEWS ━━━
- Any current events / "what's happening" / "update on X" → news_update()
- Never browse manually for news

━━━ WEATHER ━━━
- Any weather question → wether_app(city="city name") → done()

━━━ TIMER / ALARM ━━━
- "timer for 5 minutes"   → set_timer(minutes=5)         → done()
- "alarm at 7:30"         → set_alarm(alarm_time="07:30") → done()

━━━ RULES ━━━
- Always open_app() first → then wait(3) before next step
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
    response = llm_plain.invoke([
        SystemMessage("""Reply only YES or NO.

Should this use computer control tools?
YES for: opening apps, clicking, typing, searching web, playing music, news lookup, volume, any task on computer
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


conversation_history = [
    SystemMessage(content=SYSTEM_PROMPT)
] + build_conversation_history()

TOOL_LLMS = [

    ChatCerebras(
        model="gpt-oss-120b",
        api_key=os.getenv("CEREBRAS_API_KEY"),
        temperature=0.2
    ),

    ChatOpenAI(
        base_url="https://models.inference.ai.azure.com",
        api_key=api_openai,
        model="gpt-4o-mini"
    ),



    ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=os.getenv("GEMINI_KEY_5"),
        temperature=0.2
    ),
]


current_llm_idx = 0


def get_llm_tools():
    global current_llm_idx
    llm = TOOL_LLMS[current_llm_idx]
    return llm.bind_tools(TOOLS)


def think(user_input):

    similar_tasks = retrieve_similar_task(user_input, n=3)
    similar_chats = retrieve_similar_chats(user_input, n=5)
    recent_tasks = get_recent_tasks(5)
    prefs = get_all_preferences()

    # Build memory context(Ai code - to clean up)

    tasks_text = "\n".join([
        f"- '{t['task']}' → {t['steps']}"
        for t in recent_tasks
    ]) if recent_tasks else "None"

    similar_text = "\n".join([
        f"- '{t['task']}' → steps: {t['steps']}"
        for t in similar_tasks if t["success"] == "True"
    ]) if similar_tasks else "None"

    prefs_text = "\n".join([
        f"- {k}: {v}" for k, v in prefs.items()
    ]) if prefs else "None"

    SYSTEM_WITH_MEMORY = SYSTEM + f"""

User Preferences:
{prefs_text}

Recent completed tasks (for context only, don't copy steps exactly):
{chr(10).join([f"- {t['task']}" for t in recent_tasks])
     if recent_tasks else "None"}
"""
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
        response = llm_plain.invoke(conversation_history).content
        conversation_history.append({"role": "assistant", "content": response})
        save_conversation(user_input, response)
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
                break  # success
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
            if completed_steps:
                save_task(user_input=user_input,
                          steps=completed_steps, success=True)
                print(f"[Memory] Auto-saved: {user_input}")
            return response.content

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
                print(f"[Memory] Saved: {user_input}")
                return args.get("summary", result)

            messages.append(ToolMessage(content=str(result),
                            tool_call_id=tool_call["id"]))
            time.sleep(0.5)

    save_task(user_input=user_input, steps=completed_steps, success=True)
    return last_result if last_result else "Max steps reached"


def main():
    while True:
        user_input = listen()
        if not user_input:
            continue
        result = think(user_input)
        print(f"TARZ: {result}")
        speak(result)


main()
