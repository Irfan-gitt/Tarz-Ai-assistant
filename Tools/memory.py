import chromadb
import os
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
    conv_id = f"conv_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    conversation_collection.add(
        documents=[f"User: {user_msg} | TARZ: {tarz_msg}"],
        metadatas=[{
            "user": user_msg,
            "tarz": tarz_msg,
            "timestamp": datetime.now().isoformat()
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


def save_task(user_input: str, steps: list, success: bool = True):
    """Save a completed task to memory"""

    task_id = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    tasks_collection.add(
        documents=[user_input],
        metadatas=[{
            "steps": ",".join(steps),
            "success": str(success),
            "timestamp": datetime.now().isoformat()
        }],
        ids=[task_id]
    )

    print(f"[Memory] Saved task: {user_input}")


def retrieve_similar_task(user_input: str, n=1) -> dict | None:
    """Find similar past task"""

    try:
        results = tasks_collection.query(
            query_texts=[user_input],
            n_results=n
        )

        if results["documents"][0]:
            return {
                "task": results["documents"][0][0],
                "steps": results["metadatas"][0][0]["steps"].split(","),
                "success": results["metadatas"][0][0]["success"]
            }
    except:
        pass

    return None


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
