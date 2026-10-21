"""Single entry point for every model call in TARZ.

Callers pass a priority list from config (e.g. TOOL_CALLING_LLMS). For each
model the gateway tries the provider's API keys in order, skips keys and
models that are cooling down, then falls back to the next model.
Unexpected errors (bugs) are raised, not swallowed.
"""

import logging
import threading
import time
from typing import Callable, Optional, Sequence

log = logging.getLogger("tarz.llm")

RATE_LIMIT_COOLDOWN = 60.0
SERVER_COOLDOWN = 30.0
DEAD_COOLDOWN = 3600.0


class LLMError(Exception):
    pass


class EmptyResponse(LLMError):
    pass


class AllModelsFailed(LLMError):
    def __init__(self, failures: list[str]):
        self.failures = failures
        super().__init__("All models failed: " + " | ".join(failures))


def _status_code(exc: Exception) -> Optional[int]:
    for source in (exc, getattr(exc, "response", None)):
        for attr in ("status_code", "code"):
            value = getattr(source, attr, None)
            if isinstance(value, int):
                return value
    return None


def classify_error(exc: Exception) -> Optional[tuple[str, float]]:
    """Return (scope, cooldown_seconds), or None if the error must be raised.

    scope "key"   -> try the next API key of the same provider
    scope "model" -> skip to the next model in the list
    """
    if isinstance(exc, EmptyResponse):
        return "model", 0.0

    status = _status_code(exc)
    text = f"{type(exc).__name__} {exc}".lower()

    if status in (401, 402, 403) or any(
        k in text for k in ("payment", "invalid api key", "api key not valid", "permission denied")
    ):
        return "key", DEAD_COOLDOWN
    if status == 429 or any(
        k in text for k in ("rate limit", "rate_limit", "ratelimit", "quota", "resource_exhausted")
    ):
        return "key", RATE_LIMIT_COOLDOWN
    if status == 404 or any(k in text for k in ("decommission", "model_not_found")):
        return "model", DEAD_COOLDOWN
    if (status is not None and status >= 500) or any(
        k in text for k in ("timeout", "timed out", "connection", "overloaded", "unavailable")
    ):
        return "model", SERVER_COOLDOWN
    if status == 400 and any(
        k in text for k in ("tool_use_failed", "failed to call a function", "failed_generation")
    ):
        return "model", 0.0
    return None


class LLMGateway:
    def __init__(
        self,
        model_configs: dict[str, dict],
        api_keys: dict[str, Sequence[str]],
        client_factory: Callable[[str, str], object],
        cancel_check: Optional[Callable[[], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._configs = model_configs
        self._api_keys = api_keys
        self._cancel_check = cancel_check
        self._client_factory = client_factory
        self._clock = clock
        self._lock = threading.Lock()
        self._blocked: dict[tuple, float] = {}
        self._clients: dict[tuple, object] = {}

    def _is_blocked(self, scope_key: tuple) -> bool:
        with self._lock:
            return self._blocked.get(scope_key, 0.0) > self._clock()

    def _block(self, scope_key: tuple, seconds: float) -> None:
        if seconds > 0:
            with self._lock:
                self._blocked[scope_key] = self._clock() + seconds

    def run(self, models: Sequence[str], call: Callable[[str, dict, str], object]):
        """Run call(model_name, settings, api_key) with key rotation and model fallback.

        Works for any provider (Moondream, Cartesia, raw Groq vision, ...).
        """
        failures: list[str] = []
        attempted = False

        for respect_blocks in (True, False):
            for name in models:
                settings = self._configs.get(name)
                if settings is None:
                    raise LLMError(f"Unknown model config '{name}'")
                provider = settings["provider"]
                keys = self._api_keys.get(provider, ())

                if not keys:
                    if respect_blocks:
                        failures.append(
                            f"{name}: no API keys for '{provider}'")
                    continue
                if respect_blocks and self._is_blocked(("model", name)):
                    continue

                for index, key in enumerate(keys, start=1):
                    if respect_blocks and self._is_blocked(("key", provider, key)):
                        continue
                    if self._cancel_check:
                        self._cancel_check()

                    attempted = True
                    started = self._clock()
                    try:
                        result = call(name, settings, key)
                    except Exception as exc:
                        verdict = classify_error(exc)
                        if verdict is None:
                            raise
                        scope, cooldown = verdict
                        failures.append(
                            f"{name}#key{index}: {type(exc).__name__}: {str(exc)[:200]}")
                        log.warning("model=%s key=#%d failed scope=%s cooldown=%.0fs err=%s",
                                    name, index, scope, cooldown, type(exc).__name__)
                        if scope == "key":
                            self._block(("key", provider, key), cooldown)
                            continue
                        self._block(("model", name), cooldown)
                        break

                    log.info("model=%s key=#%d latency=%.2fs",
                             name, index, self._clock() - started)
                    return result

            if attempted:
                break

        raise AllModelsFailed(failures)

    def _client(self, name, settings, key, tools, schema):
        tool_key = tuple(getattr(t, "name", repr(t))
                         for t in tools) if tools else None
        cache_key = (name, key, tool_key, schema)
        with self._lock:
            client = self._clients.get(cache_key)
        if client is None:
            client = self._client_factory(name, key)
            if tools:
                client = client.bind_tools(list(tools))
            if schema:
                client = client.with_structured_output(schema)
            with self._lock:
                self._clients[cache_key] = client
        return client

    def invoke(self, models: Sequence[str], messages: list, *,
               tools: Optional[Sequence] = None, schema: Optional[type] = None):
        if tools and schema:
            raise ValueError("Use either tools or schema, not both.")

        def call(name, settings, key):
            result = self._client(name, settings, key,
                                  tools, schema).invoke(messages)
            if schema and result is None:
                raise EmptyResponse(f"{name} returned no structured output")
            return result

        return self.run(models, call)


_gateway: Optional[LLMGateway] = None
_gateway_lock = threading.Lock()


def get_gateway() -> LLMGateway:
    global _gateway
    with _gateway_lock:
        if _gateway is None:
            from Main.config import API_KEYS, MODEL_CONFIGS, make_llm
            from Tools.cancel_state import check_cancel
            _gateway = LLMGateway(MODEL_CONFIGS, API_KEYS, make_llm,
                                  cancel_check=check_cancel)
        return _gateway
