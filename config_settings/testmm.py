import os
import pathlib
import sys
import tempfile
import time
import uuid
from datetime import datetime
from statistics import mean, median

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ["TARZ_MEMORY_DB"] = tempfile.mkdtemp()

import Memory_and_Retrieval.memory as memory  # noqa: E402
import Memory_and_Retrieval.rag as retrieval  # noqa: E402

LOGIC_ONLY = "--logic" in sys.argv
TUNE = "--tune" in sys.argv
PLAIN = "--plain" in sys.argv  # seed memories WITHOUT keywords, to compare
LLM = "--llm" in sys.argv  # also test keyword extraction with the real LLM
NOW = datetime(2026, 10, 26, 9, 0)
memory._now = lambda: NOW
passed = failed = 0


def check(cond, msg):
    global passed, failed
    passed += bool(cond)
    failed += not cond
    print(("  ✅ " if cond else "  ❌ ") + msg)


# ---------------------------------------------------------------- Part A: logic (no Jina, no database)
ORIG = (memory.get_relevant_memories,
        memory.get_important_memories, memory.get_due_followups)
calls = {"search": 0}


def cand(id, text, score=0.0, kind="preference", importance=3, timestamp="2026-10-20T10:00:00", **extra):
    return {"id": id, "text": text, "score": score, "kind": kind, "importance": importance, "timestamp": timestamp, **extra}


def patch(relevant=(), core=(), due=()):
    def search(q, limit=5, min_importance=1):
        calls["search"] += 1
        if relevant == "boom":
            raise RuntimeError("search failed")
        return list(relevant)
    memory.get_relevant_memories = search
    memory.get_important_memories = lambda min_importance=4, limit=10: list(
        core)
    memory.get_due_followups = lambda limit=3: list(due)


QUERY = "what should I eat tonight please"
INTERVIEW = cand("f", "User has a job interview at 10:00 on 25 Oct 2026", kind="event", importance=4,
                 event_at="2026-10-25T10:00", follow_up_at="2026-10-25T13:00")

print("--- A1. Floor and relative window")
patch(relevant=[cand("a", "User likes tea", 0.40),
      cand("b", "User likes JavaScript", 0.70)])
check([m["id"] for m in retrieval.build_memory_context(QUERY).relevant]
      == ["b"], "score below the floor is dropped")
pool = [cand("c80", "x", 0.80), cand("c78", "x", 0.78), cand("c60", "x", 0.60)]
check([m["id"] for m in retrieval.select_relevant(pool, set(), NOW, floor=0.5, window=0.10)]
      == ["c80", "c78"], "a match far below the best one is dropped (window)")
check(retrieval.select_relevant([cand("n1", "x", 0.57), cand("n2", "x", 0.56)], set(
), NOW, floor=0.58) == [], "all-noise scores return nothing")
check(len(retrieval.select_relevant(pool, set(), NOW, floor=0.5,
      window=1.0)) == 3, "window 1.0 switches the window off")

print("--- A2. No duplicates, and old events leave the core")
goal = cand("g", "User's goal is a GenAI job", importance=4)
patch(core=[goal], relevant=[{**goal, "score": 0.9},
      cand("b", "User likes JavaScript", 0.7)])
ctx = retrieval.build_memory_context(QUERY)
check([m["id"] for m in ctx.relevant] == ["b"] and ctx.text.count(
    "GenAI job") == 1, "a core memory is shown once")
old_ev = cand("e1", "old event", kind="event",
              importance=4, event_at="2026-10-01T10:00")
recent_ev = cand("e2", "recent event", kind="event",
                 importance=4, event_at="2026-10-25T10:00")
up_ev = cand("e3", "upcoming event", kind="event",
             importance=4, event_at="2026-10-30T10:00")
old_pref = cand("p1", "old preference", importance=5,
                timestamp="2024-01-01T00:00:00")
patch(core=[old_ev, recent_ev, up_ev, old_pref])
check([m["id"] for m in retrieval.build_memory_context("ok").core] == ["e2", "e3",
      "p1"], "old past event leaves the core; recent, upcoming and old preference stay")

print("--- A3. Ranking: importance and recency")
patch(relevant=[cand("low", "low importance", 0.7, importance=2),
      cand("high", "high importance", 0.7, importance=5)])
check([m["id"] for m in retrieval.build_memory_context(QUERY).relevant]
      == ["high", "low"], "same relevance: more important first")
old_e = cand("old", "old event", 0.7, kind="event",
             event_at="2026-09-01T10:00")
new_e = cand("new", "new event", 0.7, kind="event",
             event_at="2026-10-25T10:00")
patch(relevant=[old_e, new_e])
check([m["id"] for m in retrieval.build_memory_context(QUERY).relevant]
      == ["new", "old"], "same relevance: recent event first")
best = cand("best", "clearly better match, low importance", 0.70, importance=2)
weak = cand("weak", "weaker match, high importance", 0.66, importance=5)
check([m["id"] for m in retrieval.select_relevant([weak, best], set(), NOW, floor=0.5, window=0.10)]
      == ["best", "weak"], "a clearly better match beats a more important but weaker one")
near_a = cand("a", "near tie, low importance", 0.70, importance=2)
near_b = cand("b", "near tie, high importance", 0.69, importance=5)
check([m["id"] for m in retrieval.select_relevant([near_a, near_b], set(), NOW, floor=0.5,
      window=0.10)] == ["b", "a"], "importance still decides between near-equal matches")
check(retrieval._recency(cand("p", "x", timestamp="2024-01-01T00:00:00"),
      NOW) == 1.0, "old preference does not fade")
check(retrieval._recency(old_e, NOW) < 0.25, "old event fades")
check(retrieval._recency(cand("u", "x", kind="event",
      event_at="2026-10-30T10:00"), NOW) == 1.0, "upcoming event is fully fresh")

print("--- A4. Relative day words")
phrases = {"2026-10-26": "today", "2026-10-27": "tomorrow",
           "2026-10-25": "yesterday", "2026-10-29": "in 3 days", "2026-10-23": "3 days ago"}
check(all(retrieval._day_phrase(datetime.fromisoformat(d), NOW) == w for d,
      w in phrases.items()), "today / tomorrow / yesterday / in N days / N days ago")

print("--- A5. Follow-ups")
patch(due=[INTERVIEW])
ctx = retrieval.build_memory_context("hello there my friend")
check("Follow up" in ctx.text and "yesterday" in ctx.text and "Ask how it went" in ctx.text,
      "follow-up hint with relative day")
check(ctx.followup_ids == ["f"], "ids returned so you can mark them asked")
ctx = retrieval.build_memory_context(
    "hello there my friend", include_followups=False)
check("Follow up" not in ctx.text and ctx.followups ==
      [], "include_followups=False gives no hint")

print("--- A6. Token cap")
long_items = [cand(f"r{i}", "x" * 200, 0.7 + i / 100) for i in range(5)]
patch(relevant=long_items, core=[cand("c1", "short core one", importance=5), cand(
    "c2", "short core two", importance=4)], due=[INTERVIEW])
ctx = retrieval.build_memory_context(QUERY, max_tokens=120)
check(retrieval._tokens(ctx.text) <= 120,
      f"block fits the cap ({retrieval._tokens(ctx.text)} tokens)")
check("Follow up" in ctx.text and ctx.core,
      "follow-up and core are kept, weakest relevant dropped first")

print("--- A7. Short messages skip the search")
patch(core=[goal], due=[INTERVIEW])
calls["search"] = 0
ctx = retrieval.build_memory_context("ok")
check(calls["search"] == 0 and "GenAI job" in ctx.text and "Follow up" in ctx.text,
      "'ok' makes no search, core and follow-up still come")
retrieval.build_memory_context(QUERY)
check(calls["search"] == 1, "a normal message makes exactly one search")

print("--- A8. Query building")
check(retrieval.build_query(["m1", "m2", "m3", "m4"])
      == "m4 . m3 . m2", "last 3 messages, newest first")
check(retrieval.build_query(
    [type("M", (), {"content": "hi there"})()]) == "hi there", "message objects work")

print("--- A9. Safety")
patch(relevant="boom")
check(retrieval.build_memory_context(QUERY).text == "",
      "a failing search returns an empty context, no crash")
patch()
check(retrieval.build_memory_context(QUERY).text ==
      "", "no memories -> empty text")

print("--- A10. Keywords")
check(memory._clean_keywords([" Sports ", "sports", "Weekend", ""]) == [
      "sports", "weekend"], "keywords: lowercase, trimmed, no duplicates, no empty")
check(len(memory._clean_keywords([f"k{i}" for i in range(
    10)])) == memory.MAX_KEYWORDS, "keywords: capped")
check(memory.embedding_text("User likes tea", [
      "drink", "beverage"]) == "User likes tea (keywords: drink, beverage)", "embedding text = text + keywords")
check(memory.embedding_text("User likes tea", []) ==
      "User likes tea", "no keywords -> text only")

memory.get_relevant_memories, memory.get_important_memories, memory.get_due_followups = ORIG

# ---------------------------------------------------------------- Part B: quality (real Jina, temporary database)
if not LOGIC_ONLY:
    embed_calls = {"n": 0}
    raw_embed = memory._embed
    embed_cache = {}

    def real_embed(text):
        """Jina can be slow on the free tier: retry a few times, and never embed the same text twice."""
        key = (memory.EMBED_TASK, text)
        if key not in embed_cache:
            for attempt, pause in enumerate((0, 3, 8, 15), start=1):
                time.sleep(pause)
                try:
                    embed_cache[key] = raw_embed(text)
                    break
                except requests.RequestException as e:
                    print(
                        f"  ⏳ Jina did not answer ({type(e).__name__}), attempt {attempt}/4")
            else:
                raise RuntimeError(
                    "Jina did not answer after 4 tries: check the key and the internet, or wait a minute (rate limit)")
        return embed_cache[key]

    def counting_embed(text):
        embed_calls["n"] += 1
        return real_embed(text)

    memory._embed = counting_embed

    ACTIVE_TEXTS = []

    def seed(text, kind="fact", importance=3, saved="2026-10-20T10:00:00", status="active", kw=(), **extra):
        kws = [] if PLAIN else list(kw)
        payload = {"text": text, "kind": kind, "emotion": "neutral", "importance": importance, "status": status,
                   "timestamp": saved, "source_input": "seed", "user_id": memory.USER_ID, **extra}
        if kws:
            payload["keywords"] = kws
        memory.client.upsert(memory.COLLECTION, points=[memory.models.PointStruct(
            id=str(uuid.uuid4()), vector=real_embed(memory.embedding_text(text, kws)), payload=payload)])
        if status == "active":
            ACTIVE_TEXTS.append(text)

    print("\n--- B0. Seeding memories" +
          (" (WITHOUT keywords)" if PLAIN else " (with keywords, like the extraction makes them)"))
    seed("User prefers JavaScript for everything", "preference",
         kw=["programming language", "coding", "web development"])
    seed("User dislikes Python", "preference", kw=[
         "programming language", "coding", "dislike"])
    seed("User loves Python", "preference",
         status="superseded", kw=["programming language"])
    seed("User loves pizza and considers it their favourite food",
         "preference", kw=["food", "favorite food", "dinner"])
    seed("User's goal is to get a GenAI developer job at a startup",
         "goal", 4, kw=["career", "job", "ai"])
    seed("User likes short answers without long explanations",
         "preference", 4, kw=["response style", "communication", "concise"])
    seed("User lives in Trivandrum, Kerala", "personal",
         3, kw=["home", "city", "location", "india"])
    seed("User is studying a BA in English through distance learning",
         "personal", 3, kw=["education", "college", "degree", "study"])
    seed("User listens to chill lofi music while coding",
         "preference", 2, kw=["music", "coding", "playlist"])
    seed("User uses Qdrant and LangGraph in the TARZ project", "fact", 3,
         kw=["vector database", "database", "agent framework", "project"])
    seed("User went to the market on 02 Oct 2026 and bought mangoes", "event",
         1, saved="2026-10-02T10:00:00", kw=["shopping", "fruit", "groceries"])
    seed("User has a doctor appointment on 26 Oct 2026 at 17:00", "event", 2,
         saved="2026-10-24T10:00:00", event_at="2026-10-26T17:00", kw=["health", "appointment", "schedule"])
    seed("User got through to the second round of the interview", "event", 3,
         saved="2026-10-25T20:00:00", kw=["job interview", "career", "good news"])
    seed("User has a job interview at 10:00 on 25 Oct 2026", "event", 4, saved="2026-10-24T11:00:00",
         event_at="2026-10-25T10:00", follow_up_at="2026-10-25T13:00", follow_up_status="open", kw=["job", "career", "schedule"])
    seed("User uses a Windows laptop for development", "fact",
         3, kw=["operating system", "computer", "dev setup"])
    seed("User wants to work remotely for a startup", "goal",
         3, kw=["career", "job preference", "work from home"])
    seed("User is learning to build agentic AI systems with LangGraph",
         "fact", 3, kw=["learning", "ai agents", "framework"])
    seed("User drinks black coffee every morning", "preference",
         2, kw=["drink", "morning routine", "beverage"])
    seed("User's friend Rahul lives in Bangalore",
         "personal", 2, kw=["friend", "contact", "city"])
    seed("User prefers dark mode in every app", "preference",
         3, kw=["theme", "ui preference", "appearance"])
    seed("User plans to get cloud certifications this year",
         "goal", 3, kw=["learning", "career", "cloud"])
    seed("User plays football with friends every Sunday",
         "fact", 2, kw=["sports", "weekend", "hobby"])
    seed("User felt very sad after failing an exam on 05 Oct 2026", "event",
         3, saved="2026-10-05T18:00:00", kw=["education", "bad news", "feelings"])
    print(f"  seeded {len(ACTIVE_TEXTS)} active memories + 1 superseded")

    QUESTIONS = [  # (what the user says, words that must appear in a right answer; [] = nothing should come back)
        ("which programming language should I use for my project",
         ["JavaScript"]),
        ("what should I order for dinner tonight", ["pizza"]),
        ("what music should I play while I code", ["lofi"]),
        ("where do I live", ["Trivandrum"]),
        ("tell me about my vector database setup", ["Qdrant"]),
        ("do I have anything planned for today", ["doctor"]),
        ("what am I studying", ["BA in English"]),
        (["I had my interview yesterday", "it went well"], ["second round"]),
        ("should I use a dark theme in this app", ["dark mode"]),
        ("what do I usually drink in the morning", ["coffee"]),
        ("which operating system am I on", ["Windows"]),
        ("do I want to work remotely or in an office", ["remotely"]),
        ("what am I building with LangGraph", ["LangGraph"]),
        ("do I have any certifications planned", ["certifications"]),
        ("who is Rahul", ["Rahul"]),
        ("what do I do on weekends", ["football"]),
        ("why was I upset earlier this month", ["failing an exam"]),
        ("which vector database do I use for memory", ["Qdrant"]),
        ("what's the weather like in Tokyo", []),
        ("explain how photosynthesis works", []),
        ("what is the capital of France", []),
        ("how many legs does a spider have", []),
        ("translate good morning to Spanish", []),
    ]

    def matches(text, gold):
        return any(g.lower() in text.lower() for g in gold)

    def label(q):
        return q if isinstance(q, str) else " | ".join(q)

    print("\n--- B1. Does the right memory come back?")
    asked = [(q, g) for q, g in QUESTIONS if g]
    hit3 = hit5 = 0
    for q, gold in asked:
        ctx = retrieval.build_memory_context(q, include_followups=False)
        rank = next((i + 1 for i, m in enumerate(ctx.relevant)
                    if matches(m["text"], gold)), None)
        hit3 += bool(rank and rank <= 3)
        hit5 += bool(rank)
        extra = len(ctx.relevant) - (1 if rank else 0)
        print(
            f"  {'✅' if rank else '❌'} '{label(q)}' -> rank {rank}, {extra} extra memories in the block")
    n = len(asked)
    check(hit3 / n >= 0.85, f"Hit Rate@3 = {hit3}/{n}")
    check(hit5 / n >= 0.9, f"Hit Rate@5 = {hit5}/{n}")

    print("\n--- B2. Unrelated questions should bring nothing")
    core_ids = {m["id"] for m in retrieval.pick_core(NOW)}
    for q, gold in [(q, g) for q, g in QUESTIONS if not g]:
        ctx = retrieval.build_memory_context(q, include_followups=False)
        top = max((r["score"] for r in memory.get_relevant_memories(
            q, limit=15) if r["id"] not in core_ids), default=0)
        check(ctx.relevant == [
        ], f"'{q}' -> nothing related (best score outside core {top:.2f}, floor {retrieval.MIN_RELEVANCE})")

    print("\n--- B3. Core, follow-ups, history")
    ctx = retrieval.build_memory_context(
        "play something nice for me", include_followups=True)
    check("GenAI developer job" in ctx.text and "short answers" in ctx.text,
          "important memories are always included")
    check("Follow up" in ctx.text and "interview" in ctx.text and "yesterday" in ctx.text,
          "interview follow-up with 'yesterday'")
    memory.mark_followups_asked(ctx.followup_ids)
    check("Follow up" not in retrieval.build_memory_context(
        "play something nice for me").text, "after mark_asked the follow-up is gone")
    all_text = " ".join(retrieval.build_memory_context(
        q, include_followups=False).text for q, _ in QUESTIONS)
    check("loves Python" not in all_text, "superseded memory never appears")

    print("\n--- B4. Jina calls")
    embed_calls["n"] = 0
    retrieval.build_memory_context("ok")
    check(embed_calls["n"] == 0, "'ok' makes 0 Jina calls")
    retrieval.build_memory_context("what should I order for dinner tonight")
    check(embed_calls["n"] == 1, "a normal message makes 1 Jina call")

    if LLM:
        print("\n--- B5. Keywords from the real extraction LLM")
        for sentence in ["I play football with my friends every Sunday", "I use Qdrant for the vector database of my project", "I drink black coffee every morning"]:
            items = memory.extract_memories(sentence, "Nice!")
            kws = items[0]["keywords"] if items else []
            print(f"  '{sentence}' -> {kws}")
            check(1 <= len(kws) <= memory.MAX_KEYWORDS and all(k == k.lower()
                  for k in kws), "got 1 to 6 lowercase keywords")

    print("\n--- Example prompt block")
    print(retrieval.build_memory_context(
        ["I had my interview yesterday", "it went well"], include_followups=False).text)

    # ------------------------------------------------------------ Part C: tuning (python testmm.py --tune)
    if TUNE:
        import numpy as np

        points = memory._scroll_all(memory._status_filter("active"))
        core = retrieval.pick_core(NOW)
        core_ids, core_texts = {m["id"]
                                for m in core}, {m["text"] for m in core}
        FLOORS = [x / 100 for x in range(45, 71)]
        WINDOWS = [0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 1.0]

        def f1(gold, cands, floor, window):
            got = [m["text"] for m in retrieval.select_relevant(
                cands, core_ids, NOW, floor, window)]
            if not gold:
                return 1.0 if not got else 0.0
            targets = {t for t in ACTIVE_TEXTS if matches(
                t, gold) and t not in core_texts}
            right = {t for t in got if t in targets}
            if not got or not right:
                return 0.0
            p, r = len(right) / len(got), len(right) / len(targets)
            return 2 * p * r / (p + r)

        def tune(raw):
            """Try every floor x window. Returns (F1 at your current values, best F1, middle of the best group, group size)."""
            results = [(mean(f1(g, c, fl, w) for g, c in raw), fl, w)
                       for fl in FLOORS for w in WINDOWS]
            top = max(r[0] for r in results)
            # F1 on a small set is noisy: anything within 0.02 is a tie
            plateau = [r for r in results if r[0] >= top - 0.02]
            mid_floor = median(r[1] for r in plateau)
            mid_win = median(WINDOWS.index(r[2]) for r in plateau)
            best = min(plateau, key=lambda r: abs(
                r[1] - mid_floor) / 0.25 + abs(WINDOWS.index(r[2]) - mid_win) / 6)
            current = mean(f1(g, c, retrieval.MIN_RELEVANCE,
                           retrieval.RELATIVE_WINDOW) for g, c in raw)
            return current, top, best, len(plateau)

        def embed_variant(query_task, doc_task):
            saved = memory.EMBED_TASK
            try:
                memory.EMBED_TASK = doc_task
                docs = np.array([real_embed(memory.embedding_text(
                    p.payload["text"], p.payload.get("keywords"))) for p in points])
                memory.EMBED_TASK = query_task
                queries = [np.array(real_embed(retrieval.build_query(q)))
                           for q, _ in QUESTIONS]
            finally:
                memory.EMBED_TASK = saved
            return docs, queries

        def candidates(docs, qv):
            sims = docs @ qv / (np.linalg.norm(docs, axis=1)
                                * np.linalg.norm(qv))
            order = np.argsort(-sims)[: retrieval.CANDIDATES]
            return [{"id": str(points[i].id), "score": float(sims[i]), **points[i].payload} for i in order], sims

        print("\n--- C1. Compare Jina tasks, each one with its own best floor and window")
        variants = {"text-matching for both (now)": ("text-matching", "text-matching"),
                    "retrieval.query + retrieval.passage": ("retrieval.query", "retrieval.passage")}
        outcome = {}
        for name, (query_task, doc_task) in variants.items():
            try:
                docs, queries = embed_variant(query_task, doc_task)
            except Exception as e:
                print(f"  ⚠️  {name}: {type(e).__name__}: {e}")
                continue
            raw, weakest_right, best_noise = [], [], []
            for (q, gold), qv in zip(QUESTIONS, queries):
                cands, sims = candidates(docs, qv)
                raw.append((gold, cands))
                outside = [(s, p.payload["text"]) for s, p in zip(
                    sims, points) if p.payload["text"] not in core_texts]
                if gold:
                    weakest_right.append(
                        max(s for s, t in outside if matches(t, gold)))
                else:
                    best_noise.append(max(s for s, _ in outside))
            current, top, best, size = tune(raw)
            outcome[name] = top
            print(f"  {name}")
            print(
                f"     weakest right answer {min(weakest_right):.2f} | best unrelated {max(best_noise):.2f} | margin {min(weakest_right) - max(best_noise):+.2f}")
            print(
                f"     F1 with your current floor/window: {current:.2f} | best possible: {top:.2f} ({size} settings are about equally good)")
            print(
                f"     middle of the best group -> MIN_RELEVANCE = {best[1]:.2f}, RELATIVE_WINDOW = {best[2]}")

        if len(outcome) == 2:
            first, second = outcome.values()
            gain = second - first
            print(f"\n  👉 retrieval task gain in best F1: {gain:+.2f}")
            print("     " + ("Clear gain: worth storing a second vector per memory for search." if gain >= 0.05
                             else "Small or no gain: keep text-matching and use its floor/window above."))
        print(
            f"  ℹ️  {len(QUESTIONS)} questions is a small set: add your own real questions and re-run before you decide.")

print(f"\nResult: {passed} passed, {failed} failed")
