"""Central configuration for TARZ: API keys, models, priority lists, LLM builder.

Key rotation and fallback live in llm_gateway.py (it imports from here).
"""

import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openrouter import ChatOpenRouter
from langchain_community.embeddings import JinaEmbeddings

load_dotenv()


def _keys(*names):
    return tuple(dict.fromkeys(v for v in (os.getenv(n) for n in names) if v))


# --- API keys: every provider exposes ALL its keys, the gateway picks one ---
GROQ_ALL_KEYS = _keys("GROQ_API_KEY_1", "GROQ_API_KEY_2", "GROQ_API_KEY_3")
GROQ_VISION_ALL_KEYS = _keys("GROQ_VISION_KEY_1", "GROQ_VISION_KEY_2")
GEMINI_ALL_KEYS = _keys("GEMINI_KEY_1", "GEMINI_KEY_2",
                        "GEMINI_KEY_3", "GEMINI_KEY_4", "GEMINI_KEY_5")
OPENROUTER_ALL_KEYS = _keys("OPENROUTER_API_KEY", "OPENROUTER_API_KEY2")
MOONDREAM_ALL_KEYS = _keys("MOONDREAM_API_KEY")
JEV_ALL_KEYS = OPENROUTER_ALL_KEYS
CARTESIA_ALL_KEYS = _keys("CARTESIA_API_KEY")
TAVILY_ALL_KEYS = _keys("TAVILY_API_KEY")
OPENWEATHER_ALL_KEYS = _keys("OPENWEATHER_KEY")
JINA_ALL_KEYS = _keys("JINA_API_KEY")
# Optional paid engines
OPENAI_ALL_KEYS = _keys("OPENAI_API_KEY_1", "OPENAI_API_KEY_2")
ANTHROPIC_ALL_KEYS = _keys("ANTHROPIC_API_KEY_1", "ANTHROPIC_API_KEY_2")

API_KEYS = {
    "groq": GROQ_ALL_KEYS,
    "groq_vision": GROQ_VISION_ALL_KEYS,
    "google": GEMINI_ALL_KEYS,
    "openrouter": OPENROUTER_ALL_KEYS,
    "moondream": MOONDREAM_ALL_KEYS,
    "cartesia": CARTESIA_ALL_KEYS,
    "tavily": TAVILY_ALL_KEYS,
    "openweather": OPENWEATHER_ALL_KEYS,
    "jina": JINA_ALL_KEYS,
    "openai": OPENAI_ALL_KEYS,
    "anthropic": ANTHROPIC_ALL_KEYS,
}


def require_core_chat_provider() -> str:
    """Need at least 1 key each: Groq, Gemini, OpenRouter, Jina. Everything else is optional."""
    missing = [
        env for env, keys in (
            ("GROQ_API_KEY_1", GROQ_ALL_KEYS),
            ("GEMINI_KEY_1", GEMINI_ALL_KEYS),
            ("OPENROUTER_API_KEY", OPENROUTER_ALL_KEYS),
            ("JINA_API_KEY", JINA_ALL_KEYS),
        ) if not keys
    ]
    if missing:
        raise RuntimeError(
            "TARZ needs at least one key for each of these in your .env: " + ", ".join(missing) +
            ". Extra keys, paid LLM keys, Cartesia, Tavily, weather and LangSmith are optional."
        )
    return "Groq + Gemini + OpenRouter + Jina"


# Keyed by the PROVIDER model id (not your config name).
# "shared": True = all keys of that provider share ONE bucket (Groq limits are per
# organization). Set it to False only if your Groq keys come from different accounts.
MODEL_LIMITS = {
    "openai/gpt-oss-120b": {"tpm": 8000, "rpm": 30, "shared": True},
    "qwen/qwen3.8-27b": {"tpm": 6000, "rpm": 30, "shared": True},
}
# Retries off: the gateway, not the SDK, decides when to switch key or model.
# Short timeout so a voice assistant falls back quickly instead of hanging.
_FAST = {"timeout": 12, "max_retries": 0}
_NO_RETRY = {"max_retries": 0}

# --- Every model config, one name each ---
MODEL_CONFIGS = {
    "tool_groq": {"provider": "groq", "model": "openai/gpt-oss-120b", "temperature": 0, **_FAST},
    "tool_groq_qwen": {"provider": "groq", "model": "qwen/qwen3.8-27b", "temperature": 0, **_FAST},
    "tool_gemini": {"provider": "google", "model": "gemini-2.5-flash", "temperature": 0.2, **_FAST},
    "tool_openrouter": {"provider": "openrouter", "model": "openrouter/free", "temperature": 0, **_NO_RETRY},

    "classify_groq": {"provider": "groq", "model": "openai/gpt-oss-120b", "temperature": 0, **_FAST},
    "classify_groq_qwen": {"provider": "groq", "model": "qwen/qwen3.8-27b", "temperature": 0, **_FAST},
    "classify_gemini": {"provider": "google", "model": "gemini-2.5-flash", "temperature": 0, **_FAST},
    "classify_openrouter": {"provider": "openrouter", "model": "openrouter/free", "temperature": 0, **_NO_RETRY},

    "plain_groq": {"provider": "groq", "model": "openai/gpt-oss-120b", "temperature": 0.6, **_FAST},
    "plain_groq_qwen": {"provider": "groq", "model": "qwen/qwen3.8-27b", "temperature": 0.6, **_FAST},
    "plain_gemini": {"provider": "google", "model": "gemini-2.5-flash", "temperature": 0.6, **_FAST},
    "plain_openrouter": {"provider": "openrouter", "model": "openrouter/free", "temperature": 0.6, **_NO_RETRY},

    "vision_groq": {"provider": "groq_vision", "model": "qwen/qwen3.8-27b", **_FAST},
    "vision_openrouter": {
        "provider": "openrouter",
        "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "temperature": 0.2,
        **_NO_RETRY,
    },

    "moondream": {"provider": "moondream"},

    "stt_gemini": {"provider": "google", "model": "gemini-3.5-transcribe-live"},
    "grid_gemini": {"provider": "google", "model": "gemini-2.5-flash"},
    "stt_cartesia": {"provider": "cartesia", "model": "ink-2"},
    "tts_groq": {"provider": "groq", "model": "canopylabs/orpheus-v1-english", "voice": "troy"},
    "tts_cartesia": {
        "provider": "cartesia",
        "model": "sonic-3",
        "voice": "86e30c1d-714b-4074-a1f2-1cb6b552fb49",
    },
    "jina": {"provider": "jina", "model": "jina-embeddings-v4", "base_url": "https://api.jina.ai/v1/embeddings"},
}


# --- Paid models (optional): change the model here, or set OPENAI_MODEL / ANTHROPIC_MODEL in .env ---
# Defaults are medium-high, not top tier. No temperature on purpose: many newer
# reasoning models reject custom values. Add it back if your model accepts it.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.1")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")

_PAID = {"timeout": 20, "max_retries": 0}
for _p in ("tool", "classify", "plain"):
    MODEL_CONFIGS[f"{_p}_openai"] = {
        "provider": "openai", "model": OPENAI_MODEL, **_PAID}
    MODEL_CONFIGS[f"{_p}_anthropic"] = {
        "provider": "anthropic", "model": ANTHROPIC_MODEL, **_PAID}


def _with_paid(prefix, free):
    """Paid models go first, only if the user added that key. Free ones stay as fallback."""
    paid = []
    if ANTHROPIC_ALL_KEYS:
        paid.append(f"{prefix}_anthropic")
    if OPENAI_ALL_KEYS:
        paid.append(f"{prefix}_openai")
    return paid + free


# --- Priority lists: first = preferred, the rest are fallbacks ---
TOOL_CALLING_LLMS = _with_paid(
    "tool", ["tool_groq", "tool_groq_qwen", "tool_gemini", "tool_openrouter"])
CLASSIFY_LLMS = _with_paid(
    "classify", ["classify_groq", "classify_groq_qwen", "classify_gemini", "classify_openrouter"])
PLAIN_LLMS = _with_paid(
    "plain", ["plain_groq", "plain_groq_qwen", "plain_gemini", "plain_openrouter"])
VISION_LLMS = ["vision_groq", "vision_openrouter"]
LOCATE_MODELS = ["moondream"]
STT_MODELS = ["stt_gemini", "stt_cartesia"]
GRID_MODELS = ["grid_gemini"]
TTS_MODELS = ["tts_groq", "tts_cartesia"]
EMBEDDING_MODELS = ["jina"]


# --- Local models (run on your PC, no API key) ---
LOCAL_MODELS = {
    "wake_whisper": {"model": "small", "device": "cpu", "compute_type": "int8"},
    "reranker": {"model": "cross-encoder/ms-marco-MiniLM-L-6-v2"},
}

# --- JEV ---
API_KEYS["jev"] = JEV_ALL_KEYS
MODEL_CONFIGS["jev_openrouter"] = {
    "provider": "jev", "base_url": "https://openrouter.ai/api"}
JEV_MODELS = ["jev_openrouter"]


def make_llm(name, api_key):
    """Build a LangChain chat model for one named config with one API key."""
    settings = MODEL_CONFIGS[name]
    provider = settings["provider"]
    kwargs = {k: v for k, v in settings.items() if k != "provider"}

    # vision uses its own keys, same Groq client
    if provider in ("groq", "groq_vision"):
        return ChatGroq(api_key=api_key, **kwargs)
    if provider == "google":
        return ChatGoogleGenerativeAI(google_api_key=api_key, **kwargs)
    if provider == "openrouter":
        return ChatOpenRouter(api_key=api_key, **kwargs)
    if provider == "jina":
        return JinaEmbeddings(api_key=api_key, **kwargs)
    # paid engines: lazy imports, free-only users don't need these packages
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(api_key=api_key, **kwargs)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(api_key=api_key, **kwargs)
    raise ValueError(
        f"'{name}' ({provider}) is not a LangChain chat model; use gateway.run()")
