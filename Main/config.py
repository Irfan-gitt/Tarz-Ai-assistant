"""Central configuration for TARZ: API keys, models, priority lists, LLM builder.

Key rotation and fallback live in llm_gateway.py (it imports from here).
"""

import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openrouter import ChatOpenRouter
from typesafe_sdk import Choice, TypeSafeClient

load_dotenv()


def _keys(*names):
    return tuple(v for v in (os.getenv(n) for n in names) if v)


# --- API keys: every provider exposes ALL its keys, the gateway picks one ---
GROQ_ALL_KEYS = _keys("GROQ_API_KEY_1", "GROQ_API_KEY_2", "GROQ_API_KEY_3")
GROQ_VISION_ALL_KEYS = _keys(
    "GROQ_VISION_KEY_1", "GROQ_VISION_KEY_2")
GEMINI_ALL_KEYS = _keys("GEMINI_KEY_1", "GEMINI_KEY_2",
                        "GEMINI_KEY_3", "GEMINI_KEY_4", "GEMINI_KEY_5")
OPENROUTER_ALL_KEYS = _keys("OPENROUTER_API_KEY", "OPENROUTER_API_KEY2")
MOONDREAM_ALL_KEYS = _keys("MOONDREAM_API_KEY")
JEV_ALL_KEYS = _keys("OPENROUTER_API_KEY", "OPENROUTER_API_KEY2")
CARTESIA_ALL_KEYS = _keys("CARTESIA_API_KEY")
TAVILY_ALL_KEYS = _keys("TAVILY_API_KEY")
OPENWEATHER_ALL_KEYS = _keys("OPENWEATHER_KEY")

API_KEYS = {
    "groq": GROQ_ALL_KEYS,
    "groq_vision": GROQ_VISION_ALL_KEYS,
    "google": GEMINI_ALL_KEYS,
    "openrouter": OPENROUTER_ALL_KEYS,
    "moondream": MOONDREAM_ALL_KEYS,
    "cartesia": CARTESIA_ALL_KEYS,
    "tavily": TAVILY_ALL_KEYS,
    "openweather": OPENWEATHER_ALL_KEYS,
}


# Retries off: the gateway, not the SDK, decides when to switch key or model.
_FAST = {"timeout": 30, "max_retries": 0}
_NO_RETRY = {"max_retries": 0}

# --- Every model config, one name each ---
MODEL_CONFIGS = {
    "tool_groq": {"provider": "groq", "model": "openai/gpt-oss-120b", "temperature": 0, **_FAST},
    "tool_groq_qwen": {"provider": "groq", "model": "qwen/qwen3.8-27b", "temperature": 0, **_FAST},
    "tool_gemini": {"provider": "google", "model": "gemini-3.7-flash", "temperature": 0.2, **_FAST},
    "tool_openrouter": {"provider": "openrouter", "model": "openrouter/free", "temperature": 0, **_NO_RETRY},

    "classify_groq": {"provider": "groq", "model": "openai/gpt-oss-120b", "temperature": 0, **_FAST},
    "classify_groq_qwen": {"provider": "groq", "model": "qwen/qwen3.8-27b", "temperature": 0, **_FAST},
    "classify_gemini": {"provider": "google", "model": "gemini-3.7-flash", "temperature": 0, **_FAST},
    "classify_openrouter": {"provider": "openrouter", "model": "openrouter/free", "temperature": 0, **_NO_RETRY},

    "plain_groq": {"provider": "groq", "model": "openai/gpt-oss-120b", "temperature": 0.6, **_FAST},
    "plain_groq_qwen": {"provider": "groq", "model": "qwen/qwen3.8-27b", "temperature": 0.6, **_FAST},
    "plain_gemini": {"provider": "google", "model": "gemini-3.7-flash", "temperature": 0.6, **_FAST},
    "plain_openrouter": {"provider": "openrouter", "model": "openrouter/free", "temperature": 0.6, **_NO_RETRY},
    "plain_action": {"provider": "groq", "model": "llama-3.3-70b-versatile", "temperature": 0.7, **_FAST},
    "plain_news": {"provider": "groq", "model": "llama-3.3-70b-versatile", "temperature": 0.4, **_FAST},

    "vision_groq": {"provider": "groq_vision", "model": "qwen/qwen3.8-27b"},
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
}


# --- Priority lists: first = preferred, the rest are fallbacks ---
TOOL_CALLING_LLMS = ["tool_groq", "tool_groq_qwen",
                     "tool_gemini", "tool_openrouter"]
CLASSIFY_LLMS = ["classify_groq", "classify_groq_qwen",
                 "classify_gemini", "classify_openrouter"]
PLAIN_LLMS = ["plain_groq", "plain_groq_qwen",
              "plain_gemini", "plain_openrouter"]
VISION_LLMS = ["vision_groq", "vision_openrouter"]
LOCATE_MODELS = ["moondream"]
STT_MODELS = ["stt_gemini", "stt_cartesia"]
GRID_MODELS = ["grid_gemini"]
TTS_MODELS = ["tts_groq", "tts_cartesia"]


# --- Local models (run on your PC, no API key) ---
LOCAL_MODELS = {
    "wake_whisper": {"model": "small", "device": "cpu", "compute_type": "int8"},
    "reranker": {"model": "cross-encoder/ms-marco-MiniLM-L-6-v2"},
}

# JEV

jev_client = TypeSafeClient(
    api_key=JEV_ALL_KEYS[0] if JEV_ALL_KEYS else None,
    base_url="https://openrouter.ai/api",
)


def make_llm(name, api_key):
    """Build a LangChain chat model for one named config with one API key."""
    settings = MODEL_CONFIGS[name]
    provider = settings["provider"]
    kwargs = {k: v for k, v in settings.items() if k != "provider"}

    if provider == "groq":
        return ChatGroq(api_key=api_key, **kwargs)
    if provider == "google":
        return ChatGoogleGenerativeAI(google_api_key=api_key, **kwargs)
    if provider == "openrouter":
        return ChatOpenRouter(api_key=api_key, **kwargs)
    raise ValueError(
        f"'{name}' ({provider}) is not a LangChain chat model; use gateway.run()")
