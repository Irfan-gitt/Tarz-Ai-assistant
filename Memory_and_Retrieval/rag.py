"""
TARZ retrieval: turns stored memories into one small text block for the prompt.

Three parts (all optional, empty parts are skipped):
  1. About the user      - important memories, always included, not searched
                           (old past events drop out, facts and preferences stay)
  2. Related to message  - top matches for the recent messages. A match must pass an
                           absolute floor AND be close to the best match, then it is
                           ranked by relevance (vs the best match) + importance + recency
  3. Follow up           - planned events that are due ("how was the interview?")

Use:
    ctx = build_memory_context(recent_user_texts, include_followups=first_message_of_session)
    system_prompt += "\n\n" + ctx.text
    ... after TARZ really asked the follow-up: mark_followups_asked(ctx.followup_ids)

Dates are stored absolute and shown relative here ("yesterday"), computed fresh.
Re-check MIN_RELEVANCE after changing the embedding model or the memory text:
run  python testmm.py  and read section B2b.
"""

from dataclasses import dataclass, field
from datetime import datetime

from Memory_and_Retrieval import memory as mem

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
# ranking weights (relevance matters most)
W_REL, W_IMP, W_REC = 0.6, 0.25, 0.15
# absolute floor on the Jina cosine score (set from testmm.py B2b)
MIN_RELEVANCE = 0.585
# also stay within this distance of the best match
RELATIVE_WINDOW = 0.10
CANDIDATES = 15  # how many matches to fetch before filtering
MAX_RELEVANT = 5
CORE_MIN_IMPORTANCE = 4
MAX_CORE = 6
# a past event stays in "About the user" this long, then only search finds it
CORE_EVENT_MAX_DAYS = 3
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


def _rank_score(item: dict, now: datetime, top: float, span: float) -> float:
    """Relevance is measured against the best match (1.0 = best, 0.0 = `span` below it).
    Raw Jina scores sit in a narrow band, so using them directly let importance beat a clearly better match."""
    rel = max(0.0, min(1.0, 1 - (top - item["score"]) / span))
    return W_REL * rel + W_IMP * item.get("importance", 3) / 5 + W_REC * _recency(item, now)


def _core_ok(item: dict, now: datetime) -> bool:
    if item.get("kind") not in DECAY_KINDS:
        return True
    ref = mem._parse_time(item.get("event_at") or item.get("timestamp"))
    return ref is None or ref >= now or (now - ref).days <= CORE_EVENT_MAX_DAYS


def pick_core(now: datetime) -> list[dict]:
    items = mem.get_important_memories(
        min_importance=CORE_MIN_IMPORTANCE, limit=MAX_CORE * 3)
    return [m for m in items if _core_ok(m, now)][:MAX_CORE]


def select_relevant(candidates: list[dict], core_ids: set, now: datetime, floor: float | None = None,
                    window: float | None = None) -> list[dict]:
    """Drop core duplicates, apply the absolute floor and the window below the best match, then rank."""
    floor = MIN_RELEVANCE if floor is None else floor
    window = RELATIVE_WINDOW if window is None else window
    pool = [c for c in candidates if c["id"] not in core_ids]
    if not pool:
        return []
    top = max(c["score"] for c in pool)
    keep = [c for c in pool if c["score"] >= max(floor, top - window)]
    if not keep:
        return []
    span = max(min(window, top - floor), 0.05)
    ranked = [
        {**c, "rank_score": round(_rank_score(c, now, top, span), 3)} for c in keep]
    ranked.sort(key=lambda m: m["rank_score"], reverse=True)
    return ranked[:MAX_RELEVANT]


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

    core = pick_core(now)
    relevant = []
    if len(query.split()) >= MIN_QUERY_WORDS:
        relevant = select_relevant(mem.get_relevant_memories(
            query, limit=CANDIDATES), {m["id"] for m in core}, now)

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
