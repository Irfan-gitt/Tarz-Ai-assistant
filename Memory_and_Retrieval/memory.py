"""
TARZ long-term memory (local Qdrant).

What it does
------------
- Extracts memory-worthy facts from each exchange (LLM).
- Stores every memory with: text, kind, emotion, importance (1-5), timestamp.
- Detects contradictions / corrections / "forget this" requests and marks the
  old memory as `superseded` instead of deleting it. Active retrieval ignores
  superseded memories, so "I hate Python" removes "I like Python" from the
  user's preferences, but the old memory stays available as history.

Install:
    pip install qdrant-client requests

Jina model, URL and API keys come from config_settings.config (JINA_API_KEY in .env).

Note: Qdrant local mode (path=...) allows ONE process at a time to open the
folder. If TARZ ever runs several processes, switch to a Qdrant server.
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))  # noqa
import json
import re
import uuid
from datetime import datetime

import requests
from qdrant_client import QdrantClient, models

from config_settings.config import API_KEYS, CLASSIFY_LLMS, EMBEDDING_MODELS, MODEL_CONFIGS
from config_settings.llm_gateway import get_gateway

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
DB_PATH = "./tarz_memory_qdrant"
COLLECTION = "tarz_memory"
# defined in config_settings/config.py
_JINA = MODEL_CONFIGS[EMBEDDING_MODELS[0]]
JINA_URL = _JINA["base_url"]
EMBED_MODEL = _JINA["model"]
JINA_KEYS = API_KEYS["jina"]
# v4 default/max (if you switch the config model to v3, set 1024)
VECTOR_SIZE = 2048
# symmetric similarity, best fit for short memory sentences
EMBED_TASK = "text-matching"

# (tuned for Jina; lower it if conflicts get missed) how close an old memory must be to be checked for conflict
SIMILARITY_THRESHOLD = 0.65
MAX_CANDIDATES = 5

KINDS = ["preference", "goal", "personal", "fact", "event", "tool_result"]
EMOTIONS = ["neutral", "happy", "sad", "excited",
            "stressed", "angry", "proud", "deep"]

ACTIVE = "active"
SUPERSEDED = "superseded"

# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------
client = QdrantClient(path=DB_PATH)

if not client.collection_exists(COLLECTION):
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=models.VectorParams(
            size=VECTOR_SIZE, distance=models.Distance.COSINE),
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _embed(text: str) -> list[float]:
    """Embed via Jina. Tries each configured key in order (same idea as the LLM gateway)."""
    if not JINA_KEYS:
        raise RuntimeError("No Jina API key configured (JINA_API_KEY)")
    last_error = None
    for key in JINA_KEYS:
        try:
            resp = requests.post(
                JINA_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": EMBED_MODEL, "task": EMBED_TASK,
                      "dimensions": VECTOR_SIZE, "input": [text]},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
        except requests.RequestException as e:
            last_error = e
    raise last_error


def _embed_or_none(text: str) -> list[float] | None:
    """For read paths: a Jina/network failure should never crash the main loop."""
    try:
        return _embed(text)
    except Exception as e:
        print(f"[memory] Embedding failed: {type(e).__name__}: {e}")
        return None


def _ask_json(prompt: str):
    """Call the LLM and parse JSON, tolerating ```json fences."""
    raw = get_gateway().invoke(CLASSIFY_LLMS, prompt).content.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    return json.loads(raw)


def _status_filter(status: str, min_importance: int = 1) -> models.Filter:
    must = [models.FieldCondition(
        key="status", match=models.MatchValue(value=status))]
    if min_importance > 1:
        must.append(models.FieldCondition(key="importance",
                    range=models.Range(gte=min_importance)))
    return models.Filter(must=must)


def _to_dict(point, score: float | None = None) -> dict:
    data = {"id": str(point.id), **point.payload}
    if score is not None:
        data["score"] = round(score, 3)
    return data


# --------------------------------------------------------------------------
# Step 1: extract
# --------------------------------------------------------------------------
def extract_memories(user_input: str, ai_reply: str) -> list[dict]:
    prompt = f"""Extract memory-worthy facts about the USER from this exchange.
Return ONLY a JSON list. Return [] if nothing is worth remembering.

Each item:
{{"text": "<one short, self-contained sentence>",
  "kind": one of {KINDS},
  "emotion": one of {EMOTIONS},
  "importance": 1-5 (5 = life-defining, 3 = useful, 1 = trivial),
  "retract": true/false}}

Rules:
- Only durable facts, or a tool action that really completed this turn. Skip one-off requests and small talk.
- Split a correction into separate items: "I hate Python, use JavaScript" -> "User dislikes Python" AND "User prefers JavaScript".
- If the user asks to forget/remove something ("forget that I like Python"), emit that fact with "retract": true.
- Write in third person ("User ..."), no raw conversation copying.

User: {user_input}
AI: {ai_reply}
"""
    items = _ask_json(prompt)
    clean = []
    for m in items if isinstance(items, list) else []:
        text = str(m.get("text", "")).strip()
        if not text:
            continue
        clean.append({
            "text": text,
            "kind": m.get("kind") if m.get("kind") in KINDS else "fact",
            "emotion": m.get("emotion") if m.get("emotion") in EMOTIONS else "neutral",
            "importance": min(5, max(1, int(m.get("importance", 3)))),
            "retract": bool(m.get("retract", False)),
        })
    return clean


# --------------------------------------------------------------------------
# Step 2: resolve conflicts against existing active memories
# --------------------------------------------------------------------------
def _find_similar(vector: list[float]) -> list:
    result = client.query_points(
        collection_name=COLLECTION,
        query=vector,
        query_filter=_status_filter(ACTIVE),
        limit=MAX_CANDIDATES,
        score_threshold=SIMILARITY_THRESHOLD,
        with_payload=True,
    )
    return result.points


def _resolve(new_mem: dict, candidates: list) -> dict:
    existing = "\n".join(
        f'- id={c.id}: {c.payload["text"]}' for c in candidates)
    prompt = f"""A new memory is about to be saved. Compare it with existing active memories.

New memory: "{new_mem['text']}" (retract={new_mem['retract']})

Existing:
{existing}

Decide:
- "supersede_ids": ids of existing memories that the new one contradicts, corrects, replaces, or (if retract=true) asks to forget.
- "add": false if the new memory is a duplicate of an existing one, or if retract=true. Otherwise true.
Only supersede when they truly conflict. Different but compatible facts both stay.

Return ONLY JSON: {{"supersede_ids": [], "add": true}}"""
    decision = _ask_json(prompt)
    valid_ids = {str(c.id) for c in candidates}
    return {
        "supersede_ids": [i for i in decision.get("supersede_ids", []) if i in valid_ids],
        "add": bool(decision.get("add", True)) and not new_mem["retract"],
    }


# --------------------------------------------------------------------------
# Step 3: store
# --------------------------------------------------------------------------
def _store(mem: dict, user_input: str) -> dict | None:
    vector = _embed(mem["text"])
    candidates = _find_similar(vector)
    decision = _resolve(mem, candidates) if candidates else {
        "supersede_ids": [], "add": not mem["retract"]}

    new_id = str(uuid.uuid4()) if decision["add"] else None
    now = datetime.now().isoformat(timespec="seconds")

    if decision["add"]:
        client.upsert(
            collection_name=COLLECTION,
            points=[models.PointStruct(
                id=new_id,
                vector=vector,
                payload={
                    "text": mem["text"],
                    "kind": mem["kind"],
                    "emotion": mem["emotion"],
                    "importance": mem["importance"],
                    "status": ACTIVE,
                    "timestamp": now,
                    "source_input": user_input,
                },
            )],
        )

    if decision["supersede_ids"]:
        # Soft-delete: kept as history, hidden from active retrieval.
        client.set_payload(
            collection_name=COLLECTION,
            payload={"status": SUPERSEDED,
                     "superseded_by": new_id, "superseded_at": now},
            points=decision["supersede_ids"],
        )

    if not decision["add"] and not decision["supersede_ids"]:
        return None  # duplicate, nothing to do

    return {"id": new_id, "kind": mem["kind"], "superseded": len(decision["supersede_ids"])}


def save_imp_context(user_input: str, ai_reply: str) -> list[dict]:
    """Main entry point. Best-effort: never raises into the main loop."""
    try:
        memories = extract_memories(user_input, ai_reply)
    except Exception as e:
        print(f"[memory] Extraction skipped: {type(e).__name__}: {e}")
        return []

    saved = []
    for mem in memories:
        try:
            result = _store(mem, user_input)
            if result:
                saved.append(result)
        except Exception as e:
            print(
                f"[memory] Could not store '{mem['text']}': {type(e).__name__}: {e}")
    return saved


# --------------------------------------------------------------------------
# Retrieval (for the main file)
# --------------------------------------------------------------------------
def get_relevant_memories(query: str, limit: int = 5, min_importance: int = 1) -> list[dict]:
    """Active memories most related to `query`."""
    vector = _embed_or_none(query)
    if vector is None:
        return []
    result = client.query_points(
        collection_name=COLLECTION,
        query=vector,
        query_filter=_status_filter(ACTIVE, min_importance),
        limit=limit,
        with_payload=True,
    )
    return [_to_dict(p, p.score) for p in result.points]


def get_important_memories(min_importance: int = 4, limit: int = 10) -> list[dict]:
    """Most important active memories, newest first within the same importance."""
    points = _scroll_all(_status_filter(ACTIVE, min_importance))
    items = [_to_dict(p) for p in points]
    items.sort(key=lambda m: (m["importance"], m["timestamp"]), reverse=True)
    return items[:limit]


def get_history(query: str, limit: int = 5) -> list[dict]:
    """Old (superseded) memories related to `query`, e.g. 'what did I used to like?'"""
    vector = _embed_or_none(query)
    if vector is None:
        return []
    result = client.query_points(
        collection_name=COLLECTION,
        query=vector,
        query_filter=_status_filter(SUPERSEDED),
        limit=limit,
        with_payload=True,
    )
    return [_to_dict(p, p.score) for p in result.points]


# --------------------------------------------------------------------------
# Debug view
# --------------------------------------------------------------------------
def _scroll_all(flt: models.Filter | None = None) -> list:
    points, offset = [], None
    while True:
        batch, offset = client.scroll(
            collection_name=COLLECTION, scroll_filter=flt,
            limit=100, offset=offset, with_payload=True,
        )
        points.extend(batch)
        if offset is None:
            return points


def show_all_context(include_history: bool = False):
    points = _scroll_all(None if include_history else _status_filter(ACTIVE))

    print("\n========== SAVED MEMORY ==========")
    if not points:
        print("No memories saved yet.")
        return

    for p in sorted(points, key=lambda p: p.payload["timestamp"]):
        d = p.payload
        print(f"\nID: {p.id}")
        print(f"Memory: {d['text']}")
        print(
            f"Kind: {d['kind']} | Emotion: {d['emotion']} | Importance: {d['importance']}/5")
        print(f"Time: {d['timestamp']} | Status: {d['status']}")
        if d["status"] == SUPERSEDED:
            print(
                f"Replaced at: {d.get('superseded_at')} (by {d.get('superseded_by')})")
    print("\n==================================")
