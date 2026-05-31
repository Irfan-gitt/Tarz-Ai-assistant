import re
import os
from duckduckgo_search import DDGS
from langchain_groq import ChatGroq
from dotenv import load_dotenv
import webbrowser
import subprocess
import pyautogui
from PIL import Image
import time
from groq import Groq
import base64
import easyocr
from rapidfuzz import fuzz

load_dotenv()

reader = easyocr.Reader(['en'])
# LLM setup-text
api_key = os.getenv("groq_api")
llm = ChatGroq(api_key=api_key, temperature=0.7,
               model="llama-3.3-70b-versatile")


# Vision model
client = Groq(
    api_key=api_key
)


def listen():
    user = input("You: ").strip()
    return user


SYSTEM_PROMPT = """
You are TARZ, an AI that controls a computer.

For EVERY response, choose ONE action:

OPEN_APP: app_name
SCREENSHOT: take screenshot to see screen
CLICK_TEXT: text to find and click
TYPE: text to type
PRESS: key name
HOTKEY: key1+key2
WAIT: seconds
CHAT: your response to user
TASK_COMPLETE: task is done

Examples:
User: "open [any app] and play/search/do [anything]"

Step 1: OPEN_APP: [app]
Step 2: WAIT: 4
Step 3: SCREENSHOT: take screenshot
Step 4: CLICK_TEXT: [relevant UI element]
Step 5: TYPE: [relevant text]
Step 6: PRESS: enter
Step 7: SCREENSHOT: take screenshot
Step 8: CLICK_TEXT: [result element]
Step 9: TASK_COMPLETE: [what was accomplished]

RULES:
- ONE action per response
- Always SCREENSHOT before clicking
- Always WAIT after opening apps
- Adapt steps based on actual task
- TASK_COMPLETE when done
"""

conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]


def think(user_input):

    conversation_history.append({
        "role": "user",
        "content": user_input
    })

    final_response = ""

    # ReAct loop
    for step in range(15):

        print(f"\n[Step {step+1}]")

        # LLM decides next action
        decision = llm.invoke(conversation_history).content.strip()
        print(f"Action: {decision}")

        conversation_history.append({
            "role": "assistant",
            "content": decision
        })

        # Task complete
        if decision.startswith("TASK_COMPLETE:"):
            final_response = decision.split("TASK_COMPLETE:")[1].strip()
            break

        # Just chatting
        if decision.startswith("CHAT:"):
            final_response = decision.split("CHAT:")[1].strip()
            break

        # Execute action
        result = execute_step(decision)
        print(f"Result: {result}")

        # Feed result back
        conversation_history.append({
            "role": "user",
            "content": f"Result: {result}. Continue."
        })

        time.sleep(0.5)  # Small delay between steps

    return final_response


def execute_step(decision):
    """Parse LLM decision and execute"""

    decision = decision.strip()

    if decision.startswith("OPEN_APP:"):
        app = decision.split("OPEN_APP:")[1].strip().lower()
        apps = {
            "spotify": "spotify",
            "chrome": "chrome",
            "vscode": "code",
            "notepad": "notepad"
        }
        if app in apps:
            subprocess.Popen(apps[app], shell=True)
            return f"Opened {app}"

    elif decision.startswith("SCREENSHOT:"):
        take_screenshot()
        # Send screenshot to vision model
        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe what's on screen in one short sentence."},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{get_screenshot_base64()}"}}
                ]
            }]
        )
        description = response.choices[0].message.content
        return f"Screen shows: {description}"
    elif decision.startswith("CLICK_TEXT:"):
        text = decision.split("CLICK_TEXT:")[1].strip()

        # Try OCR first
        result = find_text_coordinates(text)

        if result["found"]:
            pyautogui.click(result["x"], result["y"])
            return f"Clicked {text} at {result['x']},{result['y']}"

        # Vision fallback with better prompt
        take_screenshot()
        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"""Find '{text}' on screen.
                        Return coordinates of CENTER of that element.
                        Format:
                        X: number
                        Y: number
                        Only numbers. No explanation."""
                    },
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{get_screenshot_base64()}"}}
                ]
            }]
        )

        result_text = response.choices[0].message.content
        x_match = re.search(r"X:\s*(\d+)", result_text)
        y_match = re.search(r"Y:\s*(\d+)", result_text)

        if x_match and y_match:
            x, y = int(x_match.group(1)), int(y_match.group(1))
            pyautogui.click(x, y)
            return f"Clicked {text} at {x},{y}"

        return f"Could not find {text}"

    elif decision.startswith("TYPE:"):
        text = decision.split("TYPE:")[1].strip()
        pyautogui.write(text, interval=0.05)
        return f"Typed: {text}"

    elif decision.startswith("PRESS:"):
        key = decision.split("PRESS:")[1].strip()
        pyautogui.press(key)
        return f"Pressed {key}"

    elif decision.startswith("HOTKEY:"):
        keys = decision.split("HOTKEY:")[1].strip()
        pyautogui.hotkey(*keys.split("+"))
        return f"Hotkey: {keys}"

    elif decision.startswith("WAIT:"):
        seconds = int(decision.split("WAIT:")[1].strip())
        time.sleep(seconds)
        return f"Waited {seconds} seconds"

    elif decision.startswith("CHAT:"):
        return decision.split("CHAT:")[1].strip()

    elif decision.startswith("TASK_COMPLETE:"):
        return "DONE"

    return "Unknown action"


def get_screenshot_base64():
    with open("screen.png", "rb") as f:
        return base64.b64encode(f.read()).decode()


def find_text_coordinates(target_text):

    img = Image.open("screen.png")

    img = img.resize(
        (img.width * 2, img.height * 2)
    )

    img.save("upscaled_screen.png")

    results = reader.readtext("upscaled_screen.png")

    best_match = None
    best_score = 0

    for detection in results:    # improves accuracy

        bbox, text, confidence = detection

        score = fuzz.partial_ratio(
            target_text.lower(),
            text.lower()
        )

        if score > best_score:
            best_score = score
            best_match = detection

    if best_match and best_score > 70:

        bbox, text, confidence = best_match

        top_left = bbox[0]
        bottom_right = bbox[2]

        center_x = int(
            (top_left[0] + bottom_right[0]) / 2
        ) // 2

        center_y = int(
            (top_left[1] + bottom_right[1]) / 2
        ) // 2

        return {
            "found": True,
            "text": text,
            "score": best_score,
            "confidence": confidence,
            "x": center_x,
            "y": center_y
        }

    return {"found": False}


def take_screenshot():

    screenshot = pyautogui.screenshot()
    screenshot.save("screen.png")
    return "screen.png"


def vision_fallback(question):

    with open("screen.png", "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"""
                        {question}

                        Return ONLY in this format:
                        X: number
                        Y: number
                        """
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{img_b64}"
                        }
                    }
                ]
            }
        ]
    )

    result = response.choices[0].message.content.strip()

    x_match = re.search(r"X:\s*(\d+)", result)
    y_match = re.search(r"Y:\s*(\d+)", result)

    if x_match and y_match:

        return {
            "found": True,
            "text": "visual element",
            "x": int(x_match.group(1)),
            "y": int(y_match.group(1))
        }

    return None


def decide_mode(query):

    response = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a routing AI.\n"
                    "Decide whether the task needs:\n"
                    "- OCR (text detection)\n"
                    "- VISION (icon/UI understanding)\n\n"
                    "Reply ONLY with:\n"
                    "OCR\n"
                    "or\n"
                    "VISION"
                )
            },
            {
                "role": "user",
                "content": query
            }
        ]
    )

    return response.choices[0].message.content.strip()


def decide_action(user_input, result):
    response = llm.invoke(f"""
    User said: "{user_input}"
    Found element: "{result['text']}" at x={result['x']}, y={result['y']}
    
    What action should I do?
    Reply ONLY in this format:
    action: click
    value: none
    
    OR
    
    action: press
    value: enter
    
    OR
    
    action: type
    value: the text to type
    """).content.strip()

    # Parse response
    lines = response.lower().split("\n")
    action = "click"  # default
    value = None

    for line in lines:
        if "action:" in line:
            action = line.split("action:")[1].strip()
        if "value:" in line:
            value = line.split("value:")[1].strip()
            if value == "none":
                value = None

    return action, value


def execute_action(action, value=None, x=None, y=None):

    if action == "click":
        pyautogui.click(x, y)

    elif action == "type":
        pyautogui.write(value, interval=0.05)

    elif action == "press":
        pyautogui.press(value)

    elif action == "hotkey":
        pyautogui.hotkey(*value.split("+"))

    return f"Executed: {action}"


def main():

    while True:

        user_input = listen()

        response = think(user_input)

        print(response)
# __________________________________


if __name__ == "__main__":
    main()
