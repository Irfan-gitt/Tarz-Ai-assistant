from Main.llm_gateway import get_gateway, AllModelsFailed
from Main.config import PLAIN_LLMS
import pyautogui
import time
from Screen_Postition.get_coordinates import find_on_screen
from Vison.vision import describe_screen


def _ask_llm(prompt):
    try:
        return get_gateway().invoke(PLAIN_LLMS, [prompt]).content
    except AllModelsFailed as e:
        print(f"[LLM] All models failed: {e}")
        return ""


def execute_step(decision, user_input, target):
        if decision == "CLICK":
            result = find_on_screen(target)

            if result["found"]:
                screen_w, screen_h = pyautogui.size()
                if 0 < result["x"] < screen_w and 0 < result["y"] < screen_h:
                    pyautogui.moveTo(result["x"], result["y"], duration=0.3)
                    pyautogui.click()
                    print("Clicked!")
                    return _ask_llm(f"""
                        I just clicked '{target}' for the user.
                        Ask a helpful follow up in a chill genz way.
                        Example: found the search bar, what do you wanna search boss?
                    """)
                else:
                    return "Coordinates out of bounds, couldn't click"  # ✅
            else:
                return f"Couldn't find '{target}' on screen"

        elif decision == "READ":
            return describe_screen(user_input)

        elif decision == "ACTION":
            target = _ask_llm(f""" User said: {user_input}
            Find app name from Userinput and only return app name no extra nothing just app name 
            Example:
                user:'hey iam bored open spotify' 
                you: spotify""").strip()
            pyautogui.press("win")
            time.sleep(1)

            pyautogui.write(target)
            time.sleep(1)
            pyautogui.press("enter")
            return target
        elif decision == "TYPE":
            text = _ask_llm(f"""
            User said: "{user_input}"
            What text do they want to type?
            Reply with ONLY the text to type, nothing else.
            """).strip()

            pyautogui.write(text, interval=0.05)
            return f"Typed: {text}"

        elif decision == "PRESS":
            key = _ask_llm(f"""
            User said: "{user_input}"
            What key to press?
            Reply ONE key name only. Examples: enter, esc, space, tab,win
            """).strip().lower()

            pyautogui.press(key)
            return f"Pressed: {key}"
