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
from Actions.execute_action import click, type_text, press_key, open_app, read_screen, volume_control, news_update, wether_app, use_shortcut, set_alarm, set_timer, translate, remember, clipboard, wait, done
from Tools.memory import get_recent_tasks

load_dotenv()

api_key = os.getenv("groq_api")
api_or = os.getenv("OPENROUTER_KEY")
api_cb = os.getenv("CEREBRAS_API_KEY")

TOOLS = [click, type_text, press_key, open_app,
         read_screen, news_update, wether_app, volume_control, use_shortcut, set_alarm, set_timer, translate, clipboard, remember, wait, done]


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


SYSTEM = """\
You are TARZ, an AI that controls a Windows computer.
Use the provided tools to complete the user's goal step by step.
Call done() only when the task is confirmed complete.
Always open_app() first if an app needs to be launched, then wait(5).

Spotify search flow — always follow this exact order:
1. open_app("spotify")
2. wait(3)
3. use_shortcut(app="spotify", action="search")
4. type_text("song name")
5. press_key("enter")
6. wait(2)
7. click("green play button")   ← always click green play button to play
8. wait(2)
9. read_screen to confirm playing
10. done()

News flow:
- ALWAYS use search_news() for ANY question about current events
- "latest news", "what's happening", "update on X" → search_news()
- Never browse manually for news, always use search_news tool

"""


def listen():
    user = input("You: ").strip()
    return user


def is_computer_task(user_input: str) -> bool:
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


def think(user_input):

    past = retrieve_similar_task(user_input)

    recent_tasks = get_recent_tasks(5)
    tasks_text = "\n".join([f"- {t['task']} (steps: {t['steps']})"
                            for t in recent_tasks]) if recent_tasks else "None"
    if past and past["success"] == "True":
        memory_hint = f"""
    IMPORTANT - Similar task found in memory:
    Task: '{past['task']}'
    Steps that worked: {past['steps']}
    Follow these exact steps for this task.
    """
    else:
        memory_hint = ""

    # 2. Get user preferences
    prefs = get_all_preferences()
    prefs_text = "\n".join(
        [f"- {k}: {v}" for k, v in prefs.items()]) if prefs else "None"

    # 3. Build system prompt with memory
    SYSTEM_WITH_MEMORY = SYSTEM + f"""
    
User Preferences:
{prefs_text}

Recent Tasks:
{tasks_text}        


{memory_hint}"""

    messages = [
        SystemMessage(content=SYSTEM_WITH_MEMORY),
        HumanMessage(content=user_input)
    ]
    if not is_computer_task(user_input):
        # Get recent tasks for context
        recent_tasks = get_recent_tasks(10)
        tasks_text = "\n".join([
            f"- {t['task']}" for t in recent_tasks
        ]) if recent_tasks else "None"

        # Inject into conversation history system message
        conversation_history[0] = SystemMessage(content=SYSTEM_PROMPT + f"""

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

    for step in range(15):
        try:
            response = llm_tools.invoke(messages)
        except Exception as e:
            print(f"[ERROR] {e}")
            try:
                return llm_plain.invoke(messages).content
            except:
                return "Something went wrong."

        messages.append(response)

        # No tool calls → LLM gave text response
        if not response.tool_calls:
            # Save task if tools were actually used
            if completed_steps:
                save_task(
                    user_input=user_input,
                    steps=completed_steps,
                    success=True
                )
                print(f"[Memory] Auto-saved: {user_input}")
            return response.content

        for tool_call in response.tool_calls:
            name = tool_call["name"]
            args = tool_call["args"]
            completed_steps.append(f"{name}({args})")

            tool_fn = next((t for t in TOOLS if t.name == name), None)
            result = tool_fn.invoke(
                args) if tool_fn else f"Unknown tool: {name}"

            print(f" -> {result}")

            if name == "done":
                save_task(user_input=user_input,
                          steps=completed_steps, success=True)
                print(f"[Memory] Saved: {user_input}")
                return args.get("summary", result)

            messages.append(ToolMessage(content=str(result),
                            tool_call_id=tool_call["id"]))
            time.sleep(0.5)
            last_result = completed_steps[-1] if completed_steps else "Task completed"
            save_task(user_input=user_input,
                      steps=completed_steps, success=False)
            return last_result


def main():
    while True:
        user_input = listen()
        if not user_input:
            continue
        result = think(user_input)
        print(f"TARZ: {result}")


main()
