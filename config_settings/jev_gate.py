"""
Jev gate: is the user's latest message chat or action? One label, nothing else.

Jev only labels. It does not remember anything, so we hand it the context:
the last messages and the open question (pending). If unsure, or if Jev fails, the answer is "action":
sending a command to the chat path would lose it, sending chat to the action path costs nothing.
"""
import logging

log = logging.getLogger("tarz.jev")

JEV_MODEL = "typesafe/jev-1.13"
JEV_URL = "https://openrouter.ai/api"

INSTRUCTIONS = (
    "You are the gate of TARZ, a desktop AI assistant. Decide if the user's LATEST message needs TARZ to DO "
    "something on the computer (open, play, send, call, download, search the web, set, mute...) or only to talk.\n"
    "Use RECENT and PENDING. A short reply that answers TARZ's last question ('sailor song', 'yes', 'the second one') "
    "continues that task, so it is action.\n"
    "If you are unsure, choose action."
)
CRITERIA = {
    "chat": ("Talk only: greetings, questions, explanations, opinions, small talk, feelings. "
             "'hey', 'I am bored', 'what is a decorator', 'suggest some songs', 'which browser is better'."),
    "action": ("Wants something done: 'play blinding lights', 'open youtube', 'send Rahul a message', "
               "'download the paper', 'search this on the web', 'set a timer', 'mute discord'. "
               "Also a reply that answers TARZ's question about such a task: 'sailor song', 'yes do it'."),
}


def jev_gate(message: str, recent: str = "", pending: str = "") -> str:
    """Returns 'chat' or 'action'. Never raises."""
    try:
        from typesafe_sdk import Choice, TypeSafeClient

        from config_settings.config import JEV_ALL_KEYS
    except Exception as e:
        log.warning("jev gate unavailable: %s", e)
        return "action"
    state = {"message": message[:500],
             "recent": recent[:600], "pending": pending[:200]}
    for key in JEV_ALL_KEYS:
        try:
            client = TypeSafeClient(api_key=key, base_url=JEV_URL)
            resp = client.system_one(model=JEV_MODEL, state=state,
                                     questions={"intent": Choice(instructions=INSTRUCTIONS, criteria=CRITERIA)})
            label = resp.answers["intent"].choice
            return label if label in CRITERIA else "action"
        except Exception as e:
            log.warning("jev gate failed on one key: %s", type(e).__name__)
    return "action"
