
import uuid
from dotenv import load_dotenv
import json
from langchain_groq import ChatGroq
import chromadb
import os
import re
from datetime import datetime

client = chromadb.PersistentClient(path="./tarz_memory")


client = chromadb.PersistentClient(path="./tarz_memory")
context_collection = client.get_or_create_collection("context_memory")


load_dotenv()
llm = ChatGroq(

    temperature=0,
    model="llama-3.3-70b-versatile")


def context_agent(user_input, ai_reply):
    PROMPT = f"""Extract memory-worthy information from this exchange. Return ONLY valid JSON, nothing else.

    Categories:
    - "preference": stable facts about how the user likes things done — habits, tool choices, tastes. Not one-off requests.
    - "tool_result": a tool/action actually completed this turn, and its outcome. Only if a real action happened.
    - "long_term": durable facts about identity, goals, relationships, education — stays true for months/years.

    Rules:
    - Leave a category as "" if nothing in this exchange fits it — don't force every category to have content.
    - One short line per fact, not a paragraph. No repeating the raw conversation.

    Exchange:
    User: {user_input}
    AI: {ai_reply}

    Return exactly this JSON shape:
    {{"preference": "", "tool_result": "", "long_term": ""}}
    """
    response = llm.invoke(PROMPT)
    return json.loads(response.content)


def save_context(ctx: dict, user_input: str):
    saved = []
    for kind, value in ctx.items():
        if not value:
            continue
        context_collection.add(
            documents=[value],
            metadatas=[{
                "kind": kind,
                "value": value,
                "source_input": user_input,
                "timestamp": datetime.now().isoformat()
            }],
            ids=[f"{kind}_{uuid.uuid4().hex[:8]}"]
        )
        saved.append(kind)
    return saved


def save_imp_context(user_input: str, ai_reply: str):
    ctx = context_agent(user_input, ai_reply)
    return save_context(ctx, user_input)
