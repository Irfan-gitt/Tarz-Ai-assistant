import os
import subprocess
import time
import pyautogui
from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import tool
from langchain.agents import create_react_agent, AgentExecutor

load_dotenv()

# ---------------- LLM ---------------- #

llm = ChatGroq(
    api_key=os.getenv("groq_api"),
    model="llama-3.3-70b-versatile",
    temperature=0
)

# ---------------- TOOLS (NEW STYLE) ---------------- #


@tool
def open_spotify() -> str:
    """Open the Spotify application"""
    subprocess.Popen("spotify", shell=True)
    time.sleep(4)
    pyautogui.hotkey("alt", "tab")
    return "Spotify opened"


@tool
def search_and_play(song: str) -> str:
    """Search for a song on Spotify and play the first result"""

    pyautogui.hotkey("ctrl", "l")
    time.sleep(1)

    pyautogui.write(song, interval=0.05)
    pyautogui.press("enter")

    time.sleep(2)

    # exit suggestion dropdown
    pyautogui.press("esc")
    time.sleep(0.5)

    # move to real results
    pyautogui.press("tab")
    pyautogui.press("tab")
    pyautogui.press("down")

    pyautogui.press("enter")

    return f"Playing {song}"


tools = [open_spotify, search_and_play]

# ---------------- PROMPT ---------------- #

prompt = PromptTemplate.from_template("""
You are an AI assistant that controls a computer.

Available tools:
{tools}

Tool names:
{tool_names}

RULES:
- If user asks to open spotify → use open_spotify
- If user asks to play a song → use search_and_play
- Extract song name properly

Use this format:

Question: {input}
Thought: think
Action: tool name
Action Input: input
Observation: result
Final Answer: response to user

{agent_scratchpad}
""")

# ---------------- AGENT ---------------- #

agent = create_react_agent(llm, tools, prompt)

agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    verbose=True
)

# ---------------- MAIN ---------------- #


def main():
    while True:
        user = input("You: ")
        result = agent_executor.invoke({"input": user})
        print("Agent:", result["output"])


if __name__ == "__main__":
    main()
