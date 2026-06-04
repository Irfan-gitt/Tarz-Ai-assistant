"""
RAG — Vector memory for TARZ.
Stores tasks + conversations as embeddings.
Retrieves semantically similar context before each task.
"""

import chromadb
from chromadb.utils import embedding_functions
import json
import time
import os

os.makedirs("memory", exist_ok=True)

client = chromadb.PersistentClient(path="memory/chroma")
embedder = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2")

tasks_col = client.get_or_create_collection(
    name="tasks",
    embedding_function=embedder
)
chats_col = client.get_or_create_collection(
    name="conversations",
    embedding_function=embedder
)


def save_task(user_input: str, steps: list, success: bool):
    """Store a completed task with its steps."""
    doc_id = f"task_{int(time.time())}"
    tasks_col.add(
        ids=[doc_id],
        documents=[user_input],
        metadatas=[{
            "task":    user_input,
            "steps":   json.dumps(steps),
            "success": str(success),
            "time":    str(int(time.time()))
        }]
    )
    print(f"[RAG] Saved task: {user_input}")


def save_conversation(user: str, tarz: str):
    """Store a conversation exchange."""
    doc_id = f"chat_{int(time.time())}"
    chats_col.add(
        ids=[doc_id],
        documents=[user],
        metadatas=[{
            "user": user,
            "tarz": tarz,
            "time": str(int(time.time()))
        }]
    )


# ── Retrieve ───────────────────────────────────────────────────────────────────

def retrieve_similar_task(query: str, n: int = 3) -> list:
    """Find semantically similar past tasks."""
    try:
        results = tasks_col.query(
            query_texts=[query],
            n_results=min(n, tasks_col.count())
        )
        if not results["documents"][0]:
            return []

        tasks = []
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i]
            tasks.append({
                "task":    meta["task"],
                "steps":   json.loads(meta["steps"]),
                "success": meta["success"]
            })
        return tasks

    except Exception as e:
        print(f"[RAG] Retrieve error: {e}")
        return []


def retrieve_similar_chats(query: str, n: int = 5) -> list:
    """Find semantically similar past conversations."""
    try:
        results = chats_col.query(
            query_texts=[query],
            n_results=min(n, chats_col.count())
        )
        if not results["documents"][0]:
            return []

        chats = []
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i]
            chats.append({
                "user": meta["user"],
                "tarz": meta["tarz"]
            })
        return chats

    except Exception as e:
        print(f"[RAG] Chat retrieve error: {e}")
        return []


def get_recent_tasks(n: int = 5) -> list:
    """Get most recent tasks by timestamp."""
    try:
        all_tasks = tasks_col.get()
        if not all_tasks["ids"]:
            return []

        combined = list(zip(
            all_tasks["metadatas"],
            all_tasks["documents"]
        ))
        # Sort by time desc
        combined.sort(key=lambda x: int(x[0].get("time", 0)), reverse=True)

        return [{"task": m["task"], "steps": json.loads(m["steps"])}
                for m, _ in combined[:n]]

    except Exception as e:
        print(f"[RAG] Recent tasks error: {e}")
        return []


def get_all_preferences() -> dict:
    """Get stored user preferences."""
    try:
        results = chats_col.get(where={"type": {"$eq": "preference"}})
        prefs = {}
        for meta in results["metadatas"]:
            prefs[meta.get("key", "")] = meta.get("value", "")
        return prefs
    except:
        return {}


def save_preference(key: str, value: str):
    """Save a user preference."""
    doc_id = f"pref_{key}"
    try:
        chats_col.delete(ids=[doc_id])
    except:
        pass
    chats_col.add(
        ids=[doc_id],
        documents=[f"{key}: {value}"],
        metadatas=[{"type": "preference", "key": key, "value": value}]
    )
