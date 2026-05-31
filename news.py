from ddgs import DDGS
from langchain_core.tools import tool
from langchain_groq import ChatGroq
import os
from langchain_groq import ChatGroq
import os
import webbrowser

from dotenv import load_dotenv
load_dotenv()


llm = ChatGroq(
    api_key=os.getenv("groq_api"),
    model="llama-3.3-70b-versatile"
)


@tool
def search_news(query: str) -> str:
    """
    Search for latest news and return summary + article links.
    Use when user asks about current events, news, wars, politics, sports, anything happening in the world.

    Examples:
    "what's happening with iran and america" → search_news("iran america war update")
    "latest AI news" → search_news("artificial intelligence news today")
    """
    try:
        results = DDGS().news(query, max_results=5, timelimit="w")

        if not results:
            return "No news found for that topic"

        # Format output
        output = f"\n📰 NEWS: {query.upper()}\n"
        output += "=" * 50 + "\n\n"

        for i, r in enumerate(results, 1):
            output += f"{i}. {r['title']}\n"
            output += f"   {r['body'][:200]}...\n"
            output += f"   🔗 {r['url']}\n\n"

        webbrowser.open(results[0]['url'])
        output += "🌐 Opening top article in browser...\n\n"
        # LLM summary

        context = "\n".join([f"- {r['title']}: {r['body']}" for r in results])

        summary = llm.invoke(f"""
Summarize these news articles in 3-4 sentences.
Be factual, clear and concise.

Articles:
{context}
""").content

        output += "=" * 50 + "\n"
        output += f"📋 SUMMARY:\n{summary}\n"

        return output

    except Exception as e:
        return f"News search failed: {e}"
