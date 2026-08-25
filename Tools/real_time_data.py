from tavily import TavilyClient
from ddgs import DDGS
from langchain_core.tools import tool
from langchain_groq import ChatGroq
import os
import re
from datetime import datetime, timedelta, timezone


from dotenv import load_dotenv
load_dotenv()


llm = ChatGroq(
    api_key=os.getenv("groq_api"),
    model="llama-3.3-70b-versatile"
)


def _local_date_time_answer(query: str) -> str | None:
    """Answer simple date/time questions without web search."""
    text = query.lower()
    asks_date = any(phrase in text for phrase in [
        "today's date", "todays date", "current date", "what date",
        "date today", "what day is it", "day today",
    ])
    asks_time = any(phrase in text for phrase in [
        "current time", "what time", "time now", "time is it",
    ])

    if not asks_date and not asks_time:
        return None

    timezone_name = "Asia/Kolkata"
    tz = timezone(timedelta(hours=5, minutes=30))
    if any(place in text for place in ["london", "uk", "england"]):
        timezone_name = "Europe/London"
        tz = timezone.utc
    elif any(place in text for place in ["new york", "usa", "us", "america"]):
        timezone_name = "America/New_York"
        tz = timezone(timedelta(hours=-4))
    elif "dubai" in text or "uae" in text:
        timezone_name = "Asia/Dubai"
        tz = timezone(timedelta(hours=4))

    now = datetime.now(tz)

    if asks_date and asks_time:
        return f"Today is {now.strftime('%A, %d %B %Y')}, and the time is {now.strftime('%I:%M %p')} in {timezone_name}."
    if asks_date:
        return f"Today is {now.strftime('%A, %d %B %Y')} in {timezone_name}."
    return f"The current time is {now.strftime('%I:%M %p')} in {timezone_name}."


tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


def _search_web(query: str):
    """
    Returns (normalized_results, direct_answer, source).
    normalized_results: list of {"title": ..., "body": ...} regardless of provider.
    direct_answer: Tavily's synthesized answer if available, else None.
    source: "tavily" | "ddgs" | "none" — useful for debugging/logging.
    """
    try:
        result = tavily.search(
            query=f"{query} current latest",
            max_results=5,
            include_answer=True,
        )
        raw = result.get("results", [])
        if raw or result.get("answer"):
            normalized = [
                {"title": r.get("title", ""), "body": r.get("content", "")}
                for r in raw
            ]
            return normalized, result.get("answer"), "tavily"
    except Exception as e:
        print(f"[rt_data] Tavily failed, falling back to DDGS: {e}")

    try:
        raw = DDGS().text(f"{query} current latest",
                          max_results=5, timelimit="w")
        normalized = [
            {"title": r.get("title", ""), "body": r.get("body", "")}
            for r in raw
        ]
        return normalized, None, "ddgs"
    except Exception as e:
        print(f"[rt_data] DDGS also failed: {e}")
        return [], None, "none"


@tool
def rt_data(query: str) -> str:
    """
    Search the web for real-time, current, or frequently changing information.

    Use this whenever the user's request depends on information that may have
    changed after the model's training cutoff.

    Examples:
    - "who is the president of india"
    - "what's today's date"
    - "what time is it in london"
    - "latest AI news"
    - "what happened today"
    - "python latest version"
    - "bitcoin price"
    - "weather in kerala"
    - "real madrid latest transfer news"
    - "who won yesterday's match"
    - "stock price of tesla"
    - "usd to inr"
    - "is chatgpt down"
    - "latest windows update"
    - "flight AI302 status"
    - "current CEO of Intel"
    - "covid cases today"
    - "earthquake in japan today"

    This function should be used for:
    - Current events
    - Politics
    - Government positions
    - Sports
    - Weather
    - Stock & crypto prices
    - Exchange rates
    - Product prices
    - Software releases
    - AI model releases
    - Live events
    - Flight/train status
    - Traffic conditions
    - Company announcements
    - Internet trends
    - Any information that requires up-to-date knowledge.

    CRITICAL: Never assume a future-sounding event hasn't happened yet based on
    your own training knowledge. Championships, elections, and scheduled events
    become real, checkable facts once their date passes — and you do not
    reliably know today's date without checking the system prompt. If the user
    explicitly asks you to use this tool, or asks about the outcome of any
    named event (election, match, tournament, launch), call this tool — never
    refuse based on your own assumption that "it hasn't happened yet."
    """
    try:
        local_answer = _local_date_time_answer(query)
        if local_answer:
            return local_answer

        results, direct_answer, source = _search_web(query)

        if not results and not direct_answer:
            return "No news found for that topic"

        output = f"\n User: {query.upper()}\n"
        output += "=" * 50 + "\n\n"
        for i, r in enumerate(results, 1):
            output += f"{i}. {r['title']}\n"
            output += f"   {r['body'][:200]}...\n"

        if direct_answer:
            # Tavily already synthesized an answer — use it directly, skip the extra LLM call
            print("[rt_data] Using Tavily's direct answer: ")
            summary = direct_answer
        else:
            # Fell back to DDGS, which has no built-in synthesis — summarize manually
            print("[rt_data] Summarizing DDGS results with LLM...")
            context = "\n".join(
                f"- {r['title']}: {r['body']}" for r in results)
            summary = llm.invoke(f"""
You are a real-time information assistant.

The user asked:

{query}

Below are search results from the web.

Your task is to answer ONLY the user's question using the search results.

Rules:
- Answer the question directly in the first sentence.
- Do NOT summarize all search results.
- Extract only the information relevant to the user's question.
- Ignore unrelated information.
- If multiple sources agree, give a single clear answer.
- If the answer isn't present, say "The search results don't contain a clear answer."
- Keep the answer under 100 words.

Search Results:

{context}
""").content

        output += "=" * 50 + "\n"
        output += f"📋 Here is the summary Boss:\n{summary}\n"
        # remove this line once you trust it, useful while testing
        output += f"\n(source: {source})\n"

        return output

    except Exception as e:
        return f"News search failed: {e}"
