import chromadb
import os
import re
from datetime import datetime

client = chromadb.PersistentClient(path="./tarz_memory")


tasks_collection = client.get_or_create_collection(
    name="tarz_tasks",
    metadata={"hnsw:space": "cosine"}
)

preferences_collection = client.get_or_create_collection(
    name="tarz_preferences",
    metadata={"hnsw:space": "cosine"}
)

conversation_collection = client.get_or_create_collection(
    name="tarz_conversations",
    metadata={"hnsw:space": "cosine"}
)

memory_collection = client.get_or_create_collection(
    name="tarz_core_memories",
    metadata={"hnsw:space": "cosine"}
)


def _now() -> str:
    return datetime.now().isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return slug[:80] or "memory"


def _query(collection, query: str, n: int) -> dict | None:
    try:
        count = collection.count()
        if count <= 0:
            return None
        return collection.query(query_texts=[query], n_results=min(n, count))
    except Exception as e:
        print(f"[Memory] Query error: {e}")
        return None


def get_recent_tasks(n=5) -> list:
    """Get recently completed tasks"""
    try:
        results = tasks_collection.get()
        if not results["metadatas"]:
            return []

        items = list(zip(results["documents"], results["metadatas"]))
        items.sort(key=lambda x: x[1]["timestamp"])
        recent = items[-n:]

        return [{
            "task": doc,
            "steps": meta["steps"],
            "success": meta["success"]
        } for doc, meta in recent]
    except:
        return []


def save_conversation(user_msg: str, tarz_msg: str):
    """Save conversation exchange"""
    conv_id = _new_id("conv")
    conversation_collection.add(
        documents=[f"User: {user_msg} | TARZ: {tarz_msg}"],
        metadatas=[{
            "user": user_msg,
            "tarz": tarz_msg,
            "timestamp": _now()
        }],
        ids=[conv_id]
    )


def get_recent_conversations(n=10) -> list:
    """Get recent conversations"""
    try:
        results = conversation_collection.get()

        items = list(zip(
            results["metadatas"],
            results["documents"]
        ))
        items.sort(key=lambda x: x[0]["timestamp"])
        return items[-n:]
    except:
        return []


def retrieve_similar_chats(query: str, n=5) -> list:
    """Find semantically similar past conversations"""
    results = _query(conversation_collection, query, n)
    if not results:
        return []

    try:
        if results["documents"][0]:
            chats = []
            for i, _ in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i]
                chats.append({
                    "user": meta.get("user", ""),
                    "tarz": meta.get("tarz", "")
                })
            return chats
    except:
        pass

    return []


def save_task(user_input: str, steps: list, success: bool = True):
    """Save a completed task to memory"""

    task_id = _new_id("task")

    tasks_collection.add(
        documents=[user_input],
        metadatas=[{
            "steps": ",".join(steps),
            "success": str(success),
            "timestamp": _now()
        }],
        ids=[task_id]
    )

    print(f"[Memory] Saved task: {user_input}")


def retrieve_similar_task(user_input: str, n=1) -> list:
    """Find similar past tasks"""
    results = _query(tasks_collection, user_input, n)
    if not results:
        return []

    try:
        if results["documents"][0]:
            tasks = []
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i]
                tasks.append({
                    "task": doc,
                    "steps": meta["steps"].split(","),
                    "success": meta["success"]
                })
            return tasks
    except:
        pass

    return []


def save_preference(key: str, value: str):
    """Save user preference"""

    preferences_collection.upsert(
        documents=[f"{key}: {value}"],
        metadatas=[{"key": key, "value": value}],
        ids=[f"pref_{key}"]
    )

    print(f"[Memory] Saved preference: {key} = {value}")


def get_preference(key: str) -> str | None:
    """Get user preference"""

    try:
        result = preferences_collection.get(ids=[f"pref_{key}"])
        if result["metadatas"]:
            return result["metadatas"][0]["value"]
    except:
        pass

    return None


def get_all_preferences() -> dict:
    """Get all saved preferences"""

    try:
        results = preferences_collection.get()
        prefs = {}
        for meta in results["metadatas"]:
            prefs[meta["key"]] = meta["value"]
        return prefs
    except:
        return {}


def save_memory(
    kind: str,
    key: str,
    value: str,
    importance: int = 5,
    source: str = "manual",
    status: str = "active"
) -> str:
    """Save a structured long-term memory."""
    kind = (kind or "note").strip().lower()
    key = (key or value[:60] or "memory").strip()
    value = (value or "").strip()

    if not value:
        return "No memory saved: empty value"

    stable_kinds = {"person", "preference", "identity", "goal", "boundary"}
    mem_id = f"mem_{kind}_{_slug(key)}" if kind in stable_kinds else _new_id(f"mem_{kind}")
    document = f"{kind.upper()} | {key}: {value}"

    memory_collection.upsert(
        ids=[mem_id],
        documents=[document],
        metadatas=[{
            "kind": kind,
            "key": key,
            "value": value,
            "importance": int(max(1, min(10, importance))),
            "source": source,
            "status": status,
            "timestamp": _now(),
        }]
    )

    print(f"[Memory] Saved {kind}: {key} = {value}")
    return f"Remembered {kind}: {key}"


def retrieve_relevant_memories(query: str, n: int = 8, kinds: list[str] | None = None) -> list:
    """Retrieve structured memories related to the user's current message."""
    results = _query(memory_collection, query, n)
    if not results:
        return []

    memories = []
    try:
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i]
            if kinds and meta.get("kind") not in kinds:
                continue
            memories.append({
                "kind": meta.get("kind", "note"),
                "key": meta.get("key", ""),
                "value": meta.get("value", doc),
                "importance": meta.get("importance", 5),
                "source": meta.get("source", ""),
                "timestamp": meta.get("timestamp", ""),
            })
    except Exception as e:
        print(f"[Memory] Relevant memory parse error: {e}")

    memories.sort(key=lambda m: int(m.get("importance", 5)), reverse=True)
    return memories


def get_recent_memories(kind: str | None = None, n: int = 5) -> list:
    """Get recent structured memories, optionally filtered by kind."""
    try:
        results = memory_collection.get()
        items = []
        for doc, meta in zip(results.get("documents", []), results.get("metadatas", [])):
            if kind and meta.get("kind") != kind:
                continue
            items.append((doc, meta))
        items.sort(key=lambda item: item[1].get("timestamp", ""))
        return [{
            "kind": meta.get("kind", "note"),
            "key": meta.get("key", ""),
            "value": meta.get("value", doc),
            "importance": meta.get("importance", 5),
            "timestamp": meta.get("timestamp", ""),
        } for doc, meta in items[-n:]]
    except Exception as e:
        print(f"[Memory] Recent memory error: {e}")
        return []


def build_memory_context(query: str) -> str:
    """Build a compact memory summary for TARZ's system prompt."""
    prefs = get_all_preferences()
    relevant = retrieve_relevant_memories(query, n=10)
    recent_moods = get_recent_memories("mood", n=3)
    active_goals = get_recent_memories("goal", n=5)
    recent_events = get_recent_memories("event", n=5)

    def format_memories(items: list) -> str:
        if not items:
            return "None"
        return "\n".join([
            f"- [{m.get('kind', 'note')}] {m.get('key', '')}: {m.get('value', '')}"
            for m in items
        ])

    prefs_text = "\n".join([f"- {k}: {v}" for k, v in prefs.items()]) if prefs else "None"

    return f"""
SMART MEMORY CONTEXT

User Preferences:
{prefs_text}

Relevant Long-Term Memories:
{format_memories(relevant)}

Recent Emotional State:
{format_memories(recent_moods)}

Active Goals / Intentions:
{format_memories(active_goals)}

Recent Important Events:
{format_memories(recent_events)}

Memory Behavior:
- Use memories only when they are relevant.
- If a memory conflicts with the user, trust the newest correction.
- Never expose memory mechanically; bring it up naturally.
- For emotional or relationship topics, slow down, ask before acting, and help the user choose a calm next step.
"""


def auto_extract_memories(user_msg: str, tarz_msg: str = "") -> list:
    """Lightweight heuristic memory capture from ordinary conversation."""
    text = (user_msg or "").strip()
    lower = text.lower()
    saved = []

    if not text:
        return saved

    mood_rules = {
        "stressed": ["stressed", "pressure", "overwhelmed", "too much", "burnt out"],
        "sad": ["sad", "hurt", "depressed", "cry", "lonely", "upset"],
        "angry": ["angry", "mad", "pissed", "annoyed"],
        "anxious": ["anxious", "scared", "worried", "panic", "nervous"],
        "excited": ["excited", "happy", "proud", "hyped"],
    }
    for mood, markers in mood_rules.items():
        if any(marker in lower for marker in markers):
            saved.append(save_memory(
                "mood",
                "recent_user_mood",
                f"User sounded {mood}. Trigger/context: {text[:180]}",
                importance=7,
                source="auto"
            ))
            break

    person_patterns = [
        (r"\bmy girlfriend\b", "girlfriend"),
        (r"\bmy boyfriend\b", "boyfriend"),
        (r"\bmy friend ([a-zA-Z]+)\b", "friend"),
        (r"\b([a-zA-Z]+) is my friend\b", "friend"),
        (r"\bmy brother\b", "brother"),
        (r"\bmy sister\b", "sister"),
    ]
    for pattern, relation in person_patterns:
        match = re.search(pattern, lower)
        if match:
            name = match.group(1).title() if match.groups() else relation
            saved.append(save_memory(
                "person",
                name,
                f"Relationship: {relation}. Context: {text[:180]}",
                importance=8,
                source="auto"
            ))
            break

    event_markers = ["exam", "deadline", "interview", "breakup", "fight", "meeting", "project", "presentation"]
    if any(marker in lower for marker in event_markers):
        title = next(marker for marker in event_markers if marker in lower)
        saved.append(save_memory(
            "event",
            title,
            text[:240],
            importance=8 if title in {"breakup", "fight", "exam", "deadline"} else 6,
            source="auto"
        ))

    goal_markers = ["i need to", "i want to", "my goal", "we need to", "i have to"]
    if any(marker in lower for marker in goal_markers):
        saved.append(save_memory(
            "goal",
            "current_goal",
            text[:240],
            importance=7,
            source="auto"
        ))

    preference_patterns = [
        (r"\bi like ([^.!,]+)", "likes"),
        (r"\bi love ([^.!,]+)", "loves"),
        (r"\bi hate ([^.!,]+)", "dislikes"),
        (r"\bi prefer ([^.!,]+)", "prefers"),
    ]
    for pattern, key in preference_patterns:
        match = re.search(pattern, lower)
        if match:
            value = match.group(1).strip()
            save_preference(key, value)
            saved.append(save_memory(
                "preference",
                key,
                value,
                importance=6,
                source="auto"
            ))
            break

    return saved
