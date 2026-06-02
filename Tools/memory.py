import chromadb
import os
from datetime import datetime

# Initialize ChromaDB
# PersistentClient = saves to disk (survives restarts)
client = chromadb.PersistentClient(path="./tarz_memory")

# Collection = like a table in normal DB
tasks_collection = client.get_or_create_collection(
    name="tarz_tasks",
    metadata={"hnsw:space": "cosine"}  # similarity measure
)

preferences_collection = client.get_or_create_collection(
    name="tarz_preferences",
    metadata={"hnsw:space": "cosine"}
)


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
