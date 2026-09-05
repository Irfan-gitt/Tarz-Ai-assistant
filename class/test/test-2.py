"""
TARZ News Briefing
- Fetches news via RSS (BBC, Times of India)
- LLM generates spoken brief
- TTS reads it out
- Visualization displays while speaking

Run: python news.py
Open: http://127.0.0.1:5000
"""

from duckduckgo_search import DDGS
from flask import Flask, render_template_string, request, jsonify
from bs4 import BeautifulSoup
from groq import Groq
import feedparser
import requests
import pyttsx3
import threading
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
client = Groq(api_key=os.getenv("groq_api"))

# ── RSS Feeds ──────────────────────────────────────────────────────────────────
RSS_FEEDS = {
    "world":      "http://feeds.bbci.co.uk/news/world/rss.xml",
    "technology": "http://feeds.bbci.co.uk/news/technology/rss.xml",
    "tech":       "http://feeds.bbci.co.uk/news/technology/rss.xml",
    "india":      "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms",
    "science":    "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "business":   "http://feeds.bbci.co.uk/news/business/rss.xml",
    "sports":     "http://feeds.bbci.co.uk/sport/rss.xml",
    "health":     "http://feeds.bbci.co.uk/news/health/rss.xml",
    "ai":         "http://feeds.bbci.co.uk/news/technology/rss.xml",
    "top":        "http://feeds.bbci.co.uk/news/rss.xml",
}

# ── News fetcher ───────────────────────────────────────────────────────────────


BROAD_CATEGORIES = {"world", "technology", "tech", "india",
                    "science", "business", "sports", "health", "top"}


def get_news(topic: str = "top", count: int = 8) -> list:
    topic = topic.lower().strip()

    if topic in BROAD_CATEGORIES:
        print(f"[News] RSS feed for '{topic}'")
        return _fetch_rss(topic, count)

    print(f"[News] DuckDuckGo search for '{topic}'")
    return _fetch_ddg(topic, count)


def _fetch_rss(topic: str, count: int) -> list:
    feed_url = RSS_FEEDS.get(topic, RSS_FEEDS["top"])
    try:
        feed = feedparser.parse(feed_url)
        articles = []
        for entry in feed.entries[:count]:
            articles.append({
                "title":   entry.get("title", "No title"),
                "summary": BeautifulSoup(
                    entry.get("summary", ""), "html.parser"
                ).get_text()[:300],
                "url":    entry.get("link", "#"),
                "source": feed.feed.get("title", "BBC News"),
                "date":   entry.get("published", "")[:16]
                if entry.get("published") else ""
            })
        print(f"[News] Got {len(articles)} articles via RSS")
        return articles
    except Exception as e:
        print(f"[RSS] Error: {e}")
        return []


def _fetch_ddg(topic: str, count: int) -> list:
    try:
        with DDGS() as ddgs:
            results = ddgs.news(topic, max_results=count)
            articles = []
            for r in results:
                articles.append({
                    "title":   r.get("title",  "No title"),
                    "summary": r.get("body",   "")[:300],
                    "url":     r.get("url",    "#"),
                    "source":  r.get("source", ""),
                    "date":    r.get("date",   "")[:16]
                    if r.get("date") else ""
                })
        print(f"[News] Got {len(articles)} articles via DuckDuckGo")
        return articles
    except Exception as e:
        print(f"[DDG] Error: {e}")
        return []

# ── LLM Brief ──────────────────────────────────────────────────────────────────


def generate_brief(articles: list, topic: str) -> str:
    """Generate a spoken news brief using Groq."""
    if not articles:
        return f"I couldn't find any news about {topic} right now."

    content = "\n\n".join([
        f"HEADLINE: {a['title']}\nSUMMARY: {a['summary']}"
        for a in articles[:5]
    ])

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": f"""You are TARZ, an AI assistant giving a spoken news briefing.
Read these {topic} news articles and give a natural 4-5 sentence spoken brief.
Sound like a professional news anchor. No bullet points. No markdown. No headers.
Start directly with the news.

{content}"""
            }],
            max_tokens=250
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"[Brief] LLM error: {e}")
        return " ".join([a["title"] for a in articles[:3]])


# ── TTS ────────────────────────────────────────────────────────────────────────
def speak(text: str):
    """Speak text in background thread."""
    def _speak():
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate",   165)
            engine.setProperty("volume", 1.0)
            # Pick a clearer voice if available
            voices = engine.getProperty("voices")
            if voices:
                engine.setProperty("voice", voices[0].id)
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            print(f"[TTS] Error: {e}")
    threading.Thread(target=_speak, daemon=True).start()


# ── HTML Template ──────────────────────────────────────────────────────────────
HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>TARZ NEWS</title>
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;600;700&family=Space+Mono:wght@400;700&family=Inter:wght@300;400&display=swap" rel="stylesheet"/>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
:root {
  --bg: #04060a; --red: #ff2244; --cyan: #00e5ff;
  --gold: #ffd700; --text: #f0f4f8; --muted: #8899aa;
  --card: #0a0f16; --border: #151f2e;
}
body {
  background: var(--bg); color: var(--text);
  font-family: 'Inter', sans-serif;
  height: 100vh; overflow: hidden;
  display: flex; flex-direction: column;
}
body::after {
  content:''; position:fixed; inset:0; pointer-events:none; z-index:100;
  background: repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,0.06) 2px,rgba(0,0,0,0.06) 4px);
}

/* Top bar */
.topbar {
  display:flex; align-items:center; padding:10px 24px;
  background:#000; border-bottom:2px solid var(--red);
  gap:16px; flex-shrink:0;
}
.live-dot { width:10px; height:10px; background:var(--red); border-radius:50%; animation:blink 1s infinite; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.2} }
.brand { font-family:'Oswald',sans-serif; font-size:1.3rem; font-weight:700; letter-spacing:0.2em; }
.brand span { color:var(--red); }
.topic-pill { padding:4px 14px; border:1px solid var(--cyan); color:var(--cyan); font-family:'Space Mono',monospace; font-size:0.7rem; letter-spacing:0.15em; border-radius:2px; }
.clock { margin-left:auto; font-family:'Space Mono',monospace; font-size:0.8rem; color:var(--muted); }

/* Layout */
.main { display:grid; grid-template-columns:1fr 340px; flex:1; overflow:hidden; }

/* Left panel */
.brief-panel { display:flex; flex-direction:column; padding:28px 32px; border-right:1px solid var(--border); position:relative; overflow:hidden; }
.brief-panel::before {
  content:''; position:absolute; top:-100px; left:-100px;
  width:400px; height:400px;
  background:radial-gradient(circle,rgba(255,34,68,0.06) 0%,transparent 70%);
  animation:drift 8s ease-in-out infinite; pointer-events:none;
}
@keyframes drift { 0%,100%{transform:translate(0,0)} 50%{transform:translate(40px,30px)} }

.section-label {
  font-family:'Space Mono',monospace; font-size:0.65rem;
  letter-spacing:0.3em; color:var(--red); margin-bottom:14px;
  display:flex; align-items:center; gap:8px;
}
.section-label::after { content:''; flex:1; height:1px; background:var(--border); }

/* Speaking indicator */
.speaking-bar {
  display:flex; align-items:center; gap:12px;
  margin-bottom:20px; opacity:0; transition:opacity 0.5s;
}
.speaking-bar.active { opacity:1; }
.waves { display:flex; align-items:center; gap:3px; height:24px; }
.wave { width:3px; background:var(--cyan); border-radius:2px; animation:wv 1s ease-in-out infinite; }
.wave:nth-child(1){animation-delay:0.0s} .wave:nth-child(2){animation-delay:0.1s}
.wave:nth-child(3){animation-delay:0.2s} .wave:nth-child(4){animation-delay:0.3s}
.wave:nth-child(5){animation-delay:0.4s}
@keyframes wv { 0%,100%{height:4px} 50%{height:20px} }
.speaking-text { font-family:'Space Mono',monospace; font-size:0.7rem; color:var(--cyan); letter-spacing:0.1em; }

/* Brief text */
.brief-text { font-size:1.2rem; line-height:1.9; color:var(--text); font-weight:300; flex:1; position:relative; z-index:1; }

/* Top story */
.top-story { margin-top:20px; padding:14px 18px; border-left:3px solid var(--red); background:rgba(255,34,68,0.05); }
.ts-label { font-family:'Space Mono',monospace; font-size:0.6rem; color:var(--red); letter-spacing:0.2em; margin-bottom:5px; }
.ts-title { font-family:'Oswald',sans-serif; font-size:1.05rem; font-weight:600; line-height:1.4; }

/* Action buttons */
.actions { display:flex; gap:10px; margin-top:18px; }
.btn {
  padding:8px 16px; background:transparent; cursor:pointer;
  font-family:'Space Mono',monospace; font-size:0.7rem; letter-spacing:0.1em;
  border-radius:2px; transition:all 0.2s;
}
.btn-ghost { border:1px solid var(--border); color:var(--muted); }
.btn-ghost:hover { border-color:var(--muted); color:var(--text); }
.btn-cyan  { border:1px solid var(--cyan); color:var(--cyan); }
.btn-cyan:hover  { background:var(--cyan); color:#000; }

/* Right panel */
.headlines-panel { display:flex; flex-direction:column; overflow:hidden; }
.panel-header { padding:14px 20px; border-bottom:1px solid var(--border); font-family:'Space Mono',monospace; font-size:0.7rem; color:var(--muted); letter-spacing:0.2em; flex-shrink:0; }
.headlines-list { flex:1; overflow-y:auto; }
.headlines-list::-webkit-scrollbar { width:3px; }
.headlines-list::-webkit-scrollbar-thumb { background:var(--border); }

.headline-item { padding:12px 20px; border-bottom:1px solid var(--border); cursor:pointer; transition:background 0.2s; display:block; text-decoration:none; animation:slideIn 0.4s ease both; }
.headline-item:hover { background:rgba(255,255,255,0.03); }
@keyframes slideIn { from{opacity:0;transform:translateX(20px)} to{opacity:1;transform:translateX(0)} }
.headline-item:nth-child(1){animation-delay:0.1s} .headline-item:nth-child(2){animation-delay:0.15s}
.headline-item:nth-child(3){animation-delay:0.2s}  .headline-item:nth-child(4){animation-delay:0.25s}
.headline-item:nth-child(5){animation-delay:0.3s}  .headline-item:nth-child(6){animation-delay:0.35s}
.headline-item:nth-child(7){animation-delay:0.4s}  .headline-item:nth-child(8){animation-delay:0.45s}
.hi-source { font-family:'Space Mono',monospace; font-size:0.6rem; color:var(--cyan); letter-spacing:0.1em; margin-bottom:3px; }
.hi-title  { font-size:0.85rem; color:var(--text); line-height:1.4; }
.hi-date   { font-size:0.7rem; color:var(--muted); margin-top:3px; }

/* Ticker */
.ticker { background:var(--red); padding:7px 0; overflow:hidden; flex-shrink:0; }
.ticker-inner { display:flex; white-space:nowrap; animation:scroll 50s linear infinite; }
@keyframes scroll { from{transform:translateX(100vw)} to{transform:translateX(-100%)} }
.ticker-item { font-family:'Oswald',sans-serif; font-size:0.85rem; font-weight:600; color:#fff; padding:0 40px; letter-spacing:0.04em; }
.ticker-item::before { content:'◆ '; color:rgba(255,255,255,0.5); }

/* Search overlay */
.overlay {
  position:fixed; inset:0; background:rgba(4,6,10,0.95);
  display:flex; align-items:center; justify-content:center;
  z-index:200; backdrop-filter:blur(4px);
}
.overlay.hidden { display:none; }
.search-box { width:580px; background:var(--card); border:1px solid var(--cyan); border-radius:4px; padding:32px; box-shadow:0 0 60px rgba(0,229,255,0.15); }
.search-box h2 { font-family:'Oswald',sans-serif; font-size:1.5rem; letter-spacing:0.1em; margin-bottom:6px; color:var(--cyan); }
.search-box p  { font-size:0.85rem; color:var(--muted); margin-bottom:20px; }
.search-box input { width:100%; background:var(--bg); border:1px solid var(--border); border-radius:2px; padding:13px 16px; color:var(--text); font-family:'Space Mono',monospace; font-size:0.95rem; outline:none; margin-bottom:10px; }
.search-box input:focus { border-color:var(--cyan); }
.search-box button { width:100%; padding:13px; background:var(--red); border:none; color:#fff; font-family:'Oswald',sans-serif; font-size:1rem; font-weight:600; letter-spacing:0.1em; cursor:pointer; transition:opacity 0.2s; }
.search-box button:hover { opacity:0.85; }
.tags { display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; }
.tag { padding:5px 12px; border:1px solid var(--border); color:var(--muted); font-family:'Space Mono',monospace; font-size:0.7rem; cursor:pointer; border-radius:2px; transition:all 0.2s; letter-spacing:0.05em; }
.tag:hover { border-color:var(--cyan); color:var(--cyan); }

/* Loading */
.loading { position:fixed; inset:0; background:var(--bg); display:flex; flex-direction:column; align-items:center; justify-content:center; z-index:300; }
.loading.hidden { display:none; }
.spinner { width:48px; height:48px; border:3px solid var(--border); border-top-color:var(--red); border-radius:50%; animation:spin 0.8s linear infinite; margin-bottom:16px; }
@keyframes spin { to{transform:rotate(360deg)} }
.loading p { font-family:'Space Mono',monospace; font-size:0.8rem; color:var(--muted); letter-spacing:0.2em; }
</style>
</head>
<body>

<div class="loading hidden" id="loading">
  <div class="spinner"></div>
  <p>FETCHING BRIEFING...</p>
</div>

<div class="overlay {% if articles %}hidden{% endif %}" id="overlay">
  <div class="search-box">
    <h2>⚡ TARZ NEWS</h2>
    <p>Enter a topic for your briefing</p>
    <input type="text" id="searchInput" placeholder="e.g. technology, india, sports, ai..." autofocus/>
    <button onclick="loadNews()">GET BRIEFING</button>
    <div class="tags">
      {% for t in ['Top News','Technology','World','India','Science','Business','Sports','Health'] %}
      <span class="tag" onclick="quickSearch('{{ t }}')">{{ t }}</span>
      {% endfor %}
    </div>
  </div>
</div>

<div class="topbar">
  <div class="live-dot"></div>
  <div class="brand"><span>TARZ</span> NEWS</div>
  {% if query %}<div class="topic-pill">{{ query | upper }}</div>{% endif %}
  <div class="clock" id="clock">--:--:--</div>
</div>

<div class="main">
  <div class="brief-panel">
    <div class="section-label">AI BRIEFING</div>

    <div class="speaking-bar {% if brief %}active{% endif %}" id="speakingBar">
      <div class="waves">
        <div class="wave"></div><div class="wave"></div><div class="wave"></div>
        <div class="wave"></div><div class="wave"></div>
      </div>
      <span class="speaking-text">TARZ IS SPEAKING</span>
    </div>

    <div class="brief-text" id="briefText">
      {% if brief %}{{ brief }}
      {% else %}<span style="color:var(--muted)">Search for a topic to get your briefing...</span>
      {% endif %}
    </div>

    {% if articles %}
    <div class="top-story">
      <div class="ts-label">TOP STORY</div>
      <div class="ts-title">{{ articles[0].title }}</div>
    </div>
    {% endif %}

    <div class="actions">
      <button class="btn btn-ghost" onclick="document.getElementById('overlay').classList.remove('hidden')">NEW SEARCH</button>
      {% if brief %}<button class="btn btn-cyan" onclick="speakBrief()">▶ REPLAY</button>{% endif %}
    </div>
  </div>

  <div class="headlines-panel">
    <div class="panel-header">LATEST HEADLINES — {{ articles|length }} STORIES</div>
    <div class="headlines-list">
      {% for a in articles %}
      <a href="{{ a.url }}" target="_blank" class="headline-item">
        <div class="hi-source">{{ a.source }}</div>
        <div class="hi-title">{{ a.title }}</div>
        <div class="hi-date">{{ a.date }}</div>
      </a>
      {% endfor %}
    </div>
  </div>
</div>

<div class="ticker">
  <div class="ticker-inner">
    {% for a in articles %}<span class="ticker-item">{{ a.title }}</span>{% endfor %}
    {% if not articles %}
    <span class="ticker-item">TARZ NEWS — REAL-TIME INTELLIGENCE FEED</span>
    <span class="ticker-item">SEARCH FOR ANY TOPIC TO BEGIN YOUR BRIEFING</span>
    {% endif %}
  </div>
</div>

<script>
function updateClock() {
  const t = new Date();
  document.getElementById('clock').textContent = t.toLocaleTimeString('en-US',{hour12:false});
}
setInterval(updateClock, 1000); updateClock();

function loadNews(q) {
  const query = q || document.getElementById('searchInput').value.trim();
  if (!query) return;
  document.getElementById('loading').classList.remove('hidden');
  window.location.href = '/?query=' + encodeURIComponent(query);
}

function quickSearch(tag) {
  document.getElementById('searchInput').value = tag;
  loadNews(tag);
}

document.getElementById('searchInput')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') loadNews();
});

window.addEventListener('load', () => {
  document.getElementById('loading').classList.add('hidden');
});

function speakBrief() {
  document.getElementById('speakingBar').classList.add('active');
  fetch('/speak', {method:'POST'})
    .then(() => setTimeout(() =>
      document.getElementById('speakingBar').classList.remove('active'), 10000));
}

{% if brief %}
window.addEventListener('load', () => setTimeout(speakBrief, 800));
{% endif %}
</script>
</body>
</html>
"""

current_brief = ""


@app.route("/")
def index():
    global current_brief
    query = request.args.get("query", "").strip()
    articles = []
    brief = ""

    if query:
        articles = get_news(query)
        if articles:
            brief = generate_brief(articles, query)
            current_brief = brief
            speak(brief)

    return render_template_string(HTML, query=query, articles=articles, brief=brief)


@app.route("/speak", methods=["POST"])
def replay():
    if current_brief:
        speak(current_brief)
    return jsonify({"ok": True})


def launch_news_briefing(query: str = "") -> str:
    """Launch from execute_action.py"""
    import subprocess
    import webbrowser
    import time
    subprocess.Popen(
        ["python", "news.py"],
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    )
    time.sleep(1.5)
    url = f"http://127.0.0.1:5000/?query={query}" if query else "http://127.0.0.1:5000"
    webbrowser.open(url)
    return f"Opened news briefing: {query or 'homepage'}"


if __name__ == "__main__":
    print("[TARZ] News running → http://127.0.0.1:5000")
    app.run(debug=False, port=5000)
