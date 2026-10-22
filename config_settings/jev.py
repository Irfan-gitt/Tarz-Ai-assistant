
# Category -> tools the worker is allowed to use (from the TARZ prompt)
from dataclasses import dataclass
from config import TOOL_CALLING_LLMS, CLASSIFY_LLMS, PLAIN_LLMS, jev_client, Choice


TOOL_GROUPS = {
    "spotify":        ["spotify_play_song", "spotify_play_playlist"],
    "whatsapp":       ["whatsapp_send_message", "whatsapp_call_contact", "whatsapp_end_call"],
    "discord":        ["discord_send_message", "discord_toggle_mute", "discord_toggle_deafen",
                       "discord_answer_call", "discord_decline_call"],
    "email":          ["send_email", "read_emails", "search_emails"],
    "calendar":       ["create_event", "list_events", "delete_event"],
    "browser":        ["browser_open_url"],
    "realtime_info":  ["rt_data"],
    "weather":        ["wether_app"],          # spelled as in the TARZ prompt
    "news":           ["news_update"],
    "translate":      ["translate"],
    "system_control": ["volume_control", "set_alarm", "set_timer",
                       "media_play_pause", "media_next_track", "media_previous_track"],
    "screen_read":    ["read_screen"],
    "app_control":    ["open_app", "click", "type_text", "press_key", "use_shortcut",
                       "clipboard", "wait", "read_screen", "done"],
    "memory":         [],                      # fill in your memory tool names
    "chat_only":      [],
}

CONFIDENCE_SCORE = {"high": 0.9, "medium": 0.6, "low": 0.3}
MIN_SCORE = 0.5   # below this, let your fallback LLM router decide


@dataclass
class ToolRoute:
    category: str
    score: float
    tools: list


def tool_category(question, last_result=None):
    state = {"question": question}
    if last_result:                       # optional: supervisor feedback on a failed step
        state["previous_tool_result"] = last_result

    response = jev_client.system_one(
        model="typesafe/jev-1.13",
        state=state,
        questions={
            "category": Choice(
                instructions=(
                    "Pick exactly one category for the FIRST action the user needs. "
                    "If the request has several steps, classify the first one only. "
                    "Decide in this order:\n"
                    "1. If it asks for an action inside Spotify, WhatsApp, Discord, email "
                    "or the calendar -> that app's category (spotify, whatsapp, discord, "
                    "email, calendar). A dedicated tool always beats manual app control, "
                    "even if the user says 'open Spotify and play X'.\n"
                    "2. Else, if it points at the screen with 'this'/'that' and no "
                    "earlier named subject ('what's this say', 'explain this error', "
                    "'summarize this') -> screen_read.\n"
                    "3. Else, if it asks for weather, news, a translation, or a current "
                    "or changing fact (date, time, scores, prices, 'who is the current "
                    "X') -> weather / news / translate / realtime_info.\n"
                    "4. Else, if it is a system action (volume, alarm, timer, or "
                    "play/pause/next/previous with no app named) -> system_control.\n"
                    "5. Else, if it asks to remember something, recall something the "
                    "user told TARZ, or corrects a stored fact -> memory.\n"
                    "6. Else, if it asks to open a website/URL or search the web in a "
                    "browser -> browser.\n"
                    "7. Else, if it needs the PC itself (open an app, click, type, press "
                    "a key, shortcut, copy/paste) and no dedicated tool covers it, "
                    "including YouTube and Telegram -> app_control.\n"
                    "8. Else, for plain conversation, general knowledge with a named "
                    "subject, opinions, venting or advice -> chat_only.\n"
                    "If you are unsure between a dedicated category and app_control, "
                    "choose the dedicated category. If unsure between app_control and "
                    "chat_only, choose chat_only unless the user clearly wants an action."
                ),
                criteria={
                    "spotify": (
                        "Play a specific song or playlist on Spotify, or music on Spotify. "
                        "Examples: 'play Sailor Song', 'play my liked songs', 'put on the "
                        "workout playlist', 'open Spotify and play Blinding Lights'. "
                        "NOT this: 'pause the music' or 'next song' (system_control), "
                        "'open Spotify' alone (app_control), 'play X on YouTube' (app_control)."
                    ),
                    "whatsapp": (
                        "Send a WhatsApp message, start a WhatsApp call or end one. "
                        "Examples: 'message Rahul on WhatsApp saying I'm late', 'call mom "
                        "on whatsapp', 'end the call'. "
                        "NOT this: 'open WhatsApp' alone (app_control), 'send an email' (email)."
                    ),
                    "discord": (
                        "Send a Discord message, mute/unmute, deafen/undeafen, or answer or "
                        "decline a Discord call. "
                        "Examples: 'tell Arjun on discord we start at 9', 'mute me on "
                        "discord', 'decline the call'. "
                        "NOT this: 'open Discord' alone (app_control), system volume (system_control)."
                    ),
                    "email": (
                        "Read, search or send email/Gmail. Never a browser task. "
                        "Examples: 'check my inbox', 'any unread mail', 'emails from "
                        "Amazon', 'send an email to Priya about the report'. "
                        "NOT this: 'open Gmail in Chrome' is still email, not browser."
                    ),
                    "calendar": (
                        "Create, list or delete calendar events. "
                        "Examples: 'what's on my schedule', 'add a meeting tomorrow at 4', "
                        "'delete the dentist event'. "
                        "NOT this: 'set an alarm for 6' or 'timer for 10 minutes' (system_control)."
                    ),
                    "browser": (
                        "Open a URL or run a web search in Brave or Chrome. "
                        "Examples: 'open github.com', 'search for best laptops under 50k', "
                        "'open this link in Chrome'. "
                        "NOT this: news briefing (news), current facts like scores or "
                        "prices (realtime_info), email (email)."
                    ),
                    "realtime_info": (
                        "A current or changing fact: date, time, sports result, election "
                        "outcome, price, exchange rate, software release, company "
                        "announcement, 'who is the current CEO/PM of X'. Also any event "
                        "whose date may have already passed. "
                        "Examples: 'what time is it', 'who won the match yesterday', 'bitcoin "
                        "price', 'is iOS 27 out'. "
                        "NOT this: weather (weather), a news briefing (news), timeless "
                        "facts like 'capital of France' (chat_only)."
                    ),
                    "weather": (
                        "Weather or temperature for a place, or whether to carry an "
                        "umbrella or jacket. "
                        "Examples: 'weather in Delhi', 'is it going to rain', 'should I "
                        "take a jacket'."
                    ),
                    "news": (
                        "A news briefing or headlines. "
                        "Examples: 'what's the news', 'give me today's headlines', 'tech "
                        "news update'. "
                        "NOT this: a single current fact (realtime_info)."
                    ),
                    "translate": (
                        "Translate given text into another language. "
                        "Examples: 'translate hello how are you to Spanish', 'say thank "
                        "you in Japanese'. "
                        "NOT this: explaining grammar or what a word means (chat_only)."
                    ),
                    "system_control": (
                        "PC volume, alarms, timers, or generic media keys when no app is "
                        "named. "
                        "Examples: 'turn it up', 'mute', 'set an alarm for 7am', 'timer "
                        "for 15 minutes', 'pause', 'skip this song', 'previous track'. "
                        "NOT this: 'play a song' (spotify), 'mute me on discord' (discord)."
                    ),
                    "screen_read": (
                        "The user refers to what is on screen using 'this' or 'that' with "
                        "no named subject earlier in the conversation, or asks what is "
                        "currently displayed. "
                        "Examples: 'what's this paper about', 'explain this error', "
                        "'summarize this page', 'what am I looking at', 'read this out'. "
                        "NOT this: 'explain quantum computing' (chat_only), a follow-up "
                        "about something already named earlier in the chat."
                    ),
                    "memory": (
                        "Store, recall or correct personal information or past "
                        "conversations. "
                        "Examples: 'remember my exam is on Friday', 'what did I tell you "
                        "about my brother', 'that's wrong, my laptop is an ASUS', 'what "
                        "was my last Spotify song'."
                    ),
                    "app_control": (
                        "Operating the PC directly: open an app, click, type, press a key, "
                        "use a shortcut, copy/paste, or any app with no dedicated tool "
                        "(YouTube, Telegram, Notepad, VS Code, Settings). If the user says "
                        "'open <app>', this is app_control unless a dedicated action "
                        "follows. "
                        "Examples: 'open Chrome', 'open Telegram', 'play lofi on YouTube', "
                        "'type hello in notepad', 'press enter', 'copy that'. "
                        "NOT this: anything a dedicated category above can do directly."
                    ),
                    "chat_only": (
                        "No tool needed: casual talk, greetings, general knowledge with a "
                        "named subject, opinions, advice, venting, or emotional support. "
                        "Examples: 'hey', 'explain how TCP handshakes work', 'what do you "
                        "think of Python vs Go', 'I'm so stressed about exams', 'capital "
                        "of Japan'. "
                        "NOT this: anything that needs the screen, live data or the PC."
                    ),
                },
            ),
            "confidence": Choice(
                instructions=(
                    "How clearly does the request fit the chosen category? "
                    "high = one category clearly fits. medium = a second category is "
                    "plausible. low = ambiguous, multi-step with unclear first step, or "
                    "missing information."
                ),
                criteria={
                    "high": "The request clearly matches exactly one category.",
                    "medium": "The request fits the chosen category but another is also plausible.",
                    "low": "The request is ambiguous or underspecified.",
                },
            ),
        },
    )

    category = response.answers["category"].choice
    score = CONFIDENCE_SCORE[response.answers["confidence"].choice]
    return ToolRoute(category, score, TOOL_GROUPS[category])


print(tool_category("iam kinda bored play"))
