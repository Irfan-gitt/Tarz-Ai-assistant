"""
TARZ retrieval: turns stored memories into one small text block for the prompt.

Three parts (all optional, empty parts are skipped):
  1. About the user      - most important memories, always included, not searched
  2. Related to message  - top matches for the recent messages, ranked by
                           relevance + importance + recency (events fade, facts do not)
  3. Follow up           - planned events that are due ("how was the interview?")

Use:
    ctx = build_memory_context(recent_user_texts, include_followups=first_message_of_session)
    system_prompt += "\n\n" + ctx.text
    ... after TARZ really asked the follow-up: mark_followups_asked(ctx.followup_ids)

Dates are stored absolute and shown relative here ("yesterday"), computed fresh.
Tune MIN_RELEVANCE and the weights with test_retrieval.py.
"""

from dataclasses import dataclass, field
from datetime import datetime

from Memory_and_Retrieval import memory as mem

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
# ranking weights (relevance matters most)
W_REL, W_IMP, W_REC = 0.6, 0.25, 0.15
# Jina cosine score below this is treated as unrelated (tune it!)
MIN_RELEVANCE = 0.55
CANDIDATES = 15  # how many matches to fetch before re-ranking
MAX_RELEVANT = 5
CORE_MIN_IMPORTANCE = 4
MAX_CORE = 6
MAX_FOLLOWUPS = 2
MAX_CONTEXT_TOKENS = 400  # rough: 1 token ~ 4 characters
HALF_LIFE_DAYS = 14  # events lose half of their recency boost every 14 days
# preferences, goals and facts never fade
DECAY_KINDS = {"event", "tool_result"}
# shorter queries skip the search (no Jina call), core + follow-ups still work
MIN_QUERY_WORDS = 3
HISTORY_MESSAGES = 3  # use the last 3 user messages so "it" and "that" make sense
MAX_QUERY_CHARS = 400


@dataclass
class MemoryContext:
    text: str = ""
    core: list = field(default_factory=list)
    relevant: list = field(default_factory=list)
    followups: list = field(default_factory=list)

    @property
    def followup_ids(self) -> list[str]:
        return [f["id"] for f in self.followups]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def build_query(messages) -> str:
    """Last few USER messages (strings or message objects), newest first."""
    items = [messages] if isinstance(messages, str) else list(messages)
    texts = [str(getattr(m, "content", m)).strip() for m in items]
    texts = [t for t in texts if t][-HISTORY_MESSAGES:]
    return " . ".join(reversed(texts))[:MAX_QUERY_CHARS]


def _day_phrase(when: datetime, now: datetime) -> str:
    days = (when.date() - now.date()).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days == -1:
        return "yesterday"
    return f"in {days} days" if days > 1 else f"{-days} days ago"


def _recency(item: dict, now: datetime) -> float:
    if item.get("kind") not in DECAY_KINDS:
        return 1.0
    ref = mem._parse_time(item.get("event_at") or item.get("timestamp"))
    if ref is None or ref >= now:  # upcoming events are fully "fresh"
        return 1.0
    return 0.5 ** ((now - ref).total_seconds() / 86400 / HALF_LIFE_DAYS)


def _rank_score(item: dict, now: datetime) -> float:
    return W_REL * item["score"] + W_IMP * item.get("importance", 3) / 5 + W_REC * _recency(item, now)


def _line(item: dict, now: datetime) -> str:
    when = mem._parse_time(item.get("event_at"))
    return f"- {item['text']}" + (f" ({_day_phrase(when, now)})" if when else "")


def _hint(item: dict, now: datetime) -> str:
    when = mem._parse_time(item.get("event_at") or item.get("follow_up_at"))
    phrase = f" ({_day_phrase(when, now)})" if when else ""
    return f"- {item['text']}{phrase}. Ask how it went. Skip it if the user already told you."


def _render(core: list, relevant: list, followups: list, now: datetime) -> str:
    parts = []
    if core:
        parts.append("About the user:\n" + "\n".join(_line(m, now)
                     for m in core))
    if relevant:
        parts.append("Related to this message:\n" +
                     "\n".join(_line(m, now) for m in relevant))
    if followups:
        parts.append("Follow up (once, after the current task is done):\n" +
                     "\n".join(_hint(m, now) for m in followups))
    if not parts:
        return ""
    return "Memory (use it naturally, never recite it):\n" + "\n\n".join(parts)


def _tokens(text: str) -> int:
    return len(text) // 4 + 1


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def build_memory_context(messages, include_followups: bool = True, max_tokens: int = MAX_CONTEXT_TOKENS) -> MemoryContext:
    """Never raises: on any problem it returns an empty context so the main loop keeps working."""
    try:
        return _build(messages, include_followups, max_tokens)
    except Exception as e:
        print(f"[retrieval] Skipped: {type(e).__name__}: {e}")
        return MemoryContext()


def _build(messages, include_followups: bool, max_tokens: int) -> MemoryContext:
    now = mem._now()
    query = build_query(messages)

    core = mem.get_important_memories(
        min_importance=CORE_MIN_IMPORTANCE, limit=MAX_CORE)
    core_ids = {m["id"] for m in core}

    relevant = []
    if len(query.split()) >= MIN_QUERY_WORDS:
        for item in mem.get_relevant_memories(query, limit=CANDIDATES):
            if item["id"] in core_ids or item["score"] < MIN_RELEVANCE:
                continue
            relevant.append(
                {**item, "rank_score": round(_rank_score(item, now), 3)})
        relevant.sort(key=lambda m: m["rank_score"], reverse=True)
        relevant = relevant[:MAX_RELEVANT]

    followups = mem.get_due_followups(
        limit=MAX_FOLLOWUPS) if include_followups else []

    text = _render(core, relevant, followups, now)
    while _tokens(text) > max_tokens and (relevant or len(core) > 1):  # drop the weakest first
        if relevant:
            relevant.pop()
        else:
            core.pop()
        text = _render(core, relevant, followups, now)

    return MemoryContext(text=text, core=core, relevant=relevant, followups=followups)
