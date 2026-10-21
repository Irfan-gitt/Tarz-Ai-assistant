import subprocess
import time
import pyautogui
from Main.llm_gateway import get_gateway, AllModelsFailed
from Main.config import TOOL_CALLING_LLMS
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool


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

def main():
    while True:
        user = input("You: ")
        messages = [HumanMessage(content=user)]
        try:
            for _ in range(8):
                response = get_gateway().invoke(
                    TOOL_CALLING_LLMS, messages, tools=tools)
                messages.append(response)
                if not response.tool_calls:
                    print("Agent:", response.content)
                    break
                for call in response.tool_calls:
                    tool_fn = next((item for item in tools
                                    if item.name == call["name"]), None)
                    result = tool_fn.invoke(call["args"]) if tool_fn else "Unknown tool"
                    messages.append(ToolMessage(
                        content=str(result), tool_call_id=call["id"]))
        except AllModelsFailed as e:
            print(f"Agent: Model calls failed: {e}")

if __name__ == "__main__":
    main()
