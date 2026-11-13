"""Single entry point for every model call in TARZ.

Callers pass a priority list from config (e.g. TOOL_CALLING_LLMS). For each model the gateway
picks the API key that is LEAST LOADED right now (not "always key #1 until it dies"), skips keys
and models that are cooling down, waits briefly when everything is busy, then falls back to the
next model. Unexpected errors (bugs) are raised, not swallowed.

  * Load spreading: keys are ordered by tokens used in the last 60s (then least-recently-used).
  * Proactive limits: optional per-MODEL tpm/rpm (see MODEL_LIMITS in config). A key that would go
    over its budget is skipped BEFORE the call, so you do not eat a 429 + cooldown at all.
  * Right cooldown scope: a 429 blocks (key, model), not the whole key. A 401/402/403 blocks the key.
  * Real cooldowns: the 429's Retry-After header / "try again in 7.2s" message is used, not a flat 60s.
  * Short waits: if every key is busy but one frees up within max_wait seconds, wait for it instead
    of dropping to a weaker fallback model.
  * Token accounting uses the model's real usage_metadata when available.
"""

import logging
import re
import threading
import time
from collections import deque
from typing import Callable, Optional, Sequence

log = logging.getLogger("tarz.llm")

RATE_LIMIT_COOLDOWN = 60.0     # used only when the 429 gives no retry hint
SERVER_COOLDOWN = 30.0
DEAD_COOLDOWN = 3600.0
USAGE_WINDOW = 60.0            # providers count tokens per minute
DEFAULT_EST_TOKENS = 1500      # guess for one call when the caller gives none
# Longest we will sleep for a key to free up before falling back.
# Kept short: this is a voice assistant, silence feels broken.
MAX_WAIT = 4.0


class LLMError(Exception):
    pass


class EmptyResponse(LLMError):
    pass


class AllModelsFailed(LLMError):
    def __init__(self, failures: list[str]):
        self.failures = failures
        super().__init__("All models failed: " + " | ".join(failures))


# --------------------------------------------------------------------------
# error classification
# --------------------------------------------------------------------------
_DUR = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")
_HINT = re.compile(r"(?:try again in|retry in|retrydelay\W+)\s*([0-9hms.\s]+)")
_UNIT = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def _status_code(exc: Exception) -> Optional[int]:
    for source in (exc, getattr(exc, "response", None)):
        for attr in ("status_code", "code"):
            value = getattr(source, attr, None)
            if isinstance(value, int):
                return value
    return None


def _parse_duration(text: str) -> Optional[float]:
    parts = _DUR.findall(text.replace(" ", ""))
    if not parts:
        return None
    return sum(float(n) * _UNIT[u] for n, u in parts)


def retry_after_seconds(exc: Exception, text: str = "") -> Optional[float]:
    """How long the provider says to wait: Retry-After header, else 'try again in 7.2s' in the message."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers:
        try:
            value = headers.get("retry-after")
        except Exception:
            value = None
        if value:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    match = _HINT.search(text or f"{exc}".lower())
    return _parse_duration(match.group(1)) if match else None


def classify_error(exc: Exception) -> Optional[tuple[str, float]]:
    """Return (scope, cooldown_seconds), or None if the error must be raised.

    scope "auth"  -> this API key is dead/unpaid: block it for every model
    scope "rate"  -> this key is over its limit for THIS model: block (key, model) only
    scope "model" -> skip to the next model in the list
    """
    if isinstance(exc, EmptyResponse):
        return "model", 0.0

    status = _status_code(exc)
    text = f"{type(exc).__name__} {exc}".lower()

    if status in (401, 402, 403) or any(
        k in text for k in ("payment", "invalid api key", "api key not valid", "permission denied")
    ):
        return "auth", DEAD_COOLDOWN
    if status == 429 or any(
        k in text for k in ("rate limit", "rate_limit", "ratelimit", "quota", "resource_exhausted")
    ):
        hint = retry_after_seconds(exc, text)
        cooldown = RATE_LIMIT_COOLDOWN if hint is None else min(
            max(hint + 0.5, 1.0), DEAD_COOLDOWN)
        return "rate", cooldown
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


def _estimate_tokens(messages) -> int:
    chars = 0
    for m in messages or []:
        chars += len(str(getattr(m, "content", m)))
    return chars // 4 + 300          # + room for the answer


# --------------------------------------------------------------------------
# the gateway
# --------------------------------------------------------------------------
class LLMGateway:
    def __init__(
        self,
        model_configs: dict[str, dict],
        api_keys: dict[str, Sequence[str]],
        client_factory: Callable[[str, str], object],
        cancel_check: Optional[Callable[[], None]] = None,
        clock: Callable[[], float] = time.monotonic,
        limits: Optional[dict[str, dict]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """limits: {"<provider model id>": {"tpm": 8000, "rpm": 30, "shared": False}}. Optional.
        tpm/rpm count PER API KEY, unless "shared": True - then all keys of that provider share ONE bucket
        (Groq applies limits per organization, so keys from the same account do NOT add up)."""
        self._configs = model_configs
        self._api_keys = api_keys
        self._cancel_check = cancel_check
        self._client_factory = client_factory
        self._clock = clock
        self._sleep = sleep
        self._limits = limits or {}
        self._lock = threading.Lock()
        self._blocked: dict[tuple, float] = {}
        # (provider, key, model_id) -> deque([t, tokens])
        self._usage: dict[tuple, deque] = {}
        self._last_used: dict[tuple, float] = {}    # (provider, key) -> time
        self._clients: dict[tuple, object] = {}

    # ---- bookkeeping (call with self._lock held unless noted) ----
    @staticmethod
    def _model_id(name: str, settings: dict) -> str:
        # Limits live on the PROVIDER's model, so tool_groq / classify_groq / plain_groq share one bucket.
        return settings.get("model", name)

    def _bucket(self, provider: str, key: str, model_id: str) -> tuple:
        shared = self._limits.get(model_id, {}).get("shared")
        return (provider, "*" if shared else key, model_id)

    def _window(self, ukey: tuple, now: float) -> deque:
        q = self._usage.setdefault(ukey, deque())
        while q and q[0][0] <= now - USAGE_WINDOW:
            q.popleft()
        return q

    def _key_wait(self, provider: str, key: str, model_id: str, est: int, now: float) -> float:
        """Seconds until this key can take one more request for this model (0 = usable now)."""
        wait = max(self._blocked.get(("auth", provider, key), 0.0),
                   self._blocked.get(("rate", provider, key, model_id), 0.0),
                   self._blocked.get(("rate", provider, "*", model_id), 0.0)) - now
        lim = self._limits.get(model_id)
        if lim:
            q = self._window(self._bucket(provider, key, model_id), now)
            rpm, tpm = lim.get("rpm"), lim.get("tpm")
            if rpm and len(q) >= rpm:
                wait = max(wait, q[0][0] + USAGE_WINDOW - now)
            if tpm and est <= tpm:                  # a request bigger than the budget can never fit: let the API decide
                need = sum(tok for _, tok in q) + est - tpm
                if need > 0:
                    acc, free_at = 0, now + USAGE_WINDOW
                    for t, tok in q:
                        acc += tok
                        if acc >= need:
                            free_at = t + USAGE_WINDOW
                            break
                    wait = max(wait, free_at - now)
        return max(0.0, wait)

    def _candidates(self, provider, model_id, keys, est, respect, now):
        """-> ([(index, key) least-loaded first], seconds until the soonest busy key frees up)"""
        rows = []
        for index, key in enumerate(keys, start=1):
            wait = self._key_wait(provider, key, model_id, est, now)
            used = sum(tok for _, tok in self._window(
                self._bucket(provider, key, model_id), now))
            rows.append((wait, used, self._last_used.get(
                (provider, key), 0.0), index, key))
        if respect:
            ready = [r for r in rows if r[0] <= 0]
            busy = [r[0] for r in rows if r[0] > 0]
            soonest = min(busy) if busy else None
        else:
            ready, soonest = rows, None
        # least tokens used, then least recently used
        ready.sort(key=lambda r: (r[1], r[2]))
        return [(r[3], r[4]) for r in ready], soonest

    def _reserve(self, provider, key, model_id, est, now) -> list:
        entry = [now, est]
        with self._lock:
            self._window(self._bucket(provider, key, model_id),
                         now).append(entry)
            self._last_used[(provider, key)] = now
        return entry

    def _release(self, provider, key, model_id, entry) -> None:
        with self._lock:
            try:
                self._usage[self._bucket(
                    provider, key, model_id)].remove(entry)
            except (KeyError, ValueError):
                pass

    def _block(self, scope_key: tuple, seconds: float) -> None:
        if seconds > 0:
            with self._lock:
                self._blocked[scope_key] = self._clock() + seconds

    def _pause(self, seconds: float) -> None:
        end = self._clock() + seconds
        while True:
            if self._cancel_check:
                self._cancel_check()
            left = end - self._clock()
            if left <= 0:
                return
            self._sleep(min(left, 1.0))

    # ---- one pass over the priority list ----
    def _pass(self, models, call, est, respect, failures):
        """-> (done, result, attempted, soonest_wait)"""
        attempted, soonest = False, None
        for name in models:
            settings = self._configs.get(name)
            if settings is None:
                raise LLMError(f"Unknown model config '{name}'")
            provider = settings["provider"]
            keys = self._api_keys.get(provider, ())
            model_id = self._model_id(name, settings)

            if not keys:
                if respect:
                    failures.append(f"{name}: no API keys for '{provider}'")
                continue
            now = self._clock()
            with self._lock:
                model_wait = self._blocked.get(("model", name), 0.0) - now
            if respect and model_wait > 0:
                soonest = model_wait if soonest is None else min(
                    soonest, model_wait)
                continue

            with self._lock:
                order, key_soonest = self._candidates(
                    provider, model_id, keys, est, respect, now)
            if key_soonest is not None:
                soonest = key_soonest if soonest is None else min(
                    soonest, key_soonest)

            for index, key in order:
                if self._cancel_check:
                    self._cancel_check()
                attempted = True
                started = self._clock()
                entry = self._reserve(provider, key, model_id, est, started)
                try:
                    result = call(name, settings, key)
                except Exception as exc:
                    self._release(provider, key, model_id, entry)
                    verdict = classify_error(exc)
                    if verdict is None:
                        raise
                    scope, cooldown = verdict
                    failures.append(
                        f"{name}#key{index}: {type(exc).__name__}: {str(exc)[:200]}")
                    log.warning("model=%s key=#%d failed scope=%s cooldown=%.1fs err=%s",
                                name, index, scope, cooldown, type(exc).__name__)
                    if scope == "auth":
                        self._block(("auth", provider, key), cooldown)
                        continue
                    if scope == "rate":
                        shared = self._limits.get(model_id, {}).get("shared")
                        self._block(
                            ("rate", provider, "*" if shared else key, model_id), cooldown)
                        if shared:           # same bucket: no point trying the other keys of this model right now
                            break
                        continue
                    self._block(("model", name), cooldown)
                    break

                usage = getattr(result, "usage_metadata", None)
                if isinstance(usage, dict) and usage.get("total_tokens"):
                    entry[1] = int(usage["total_tokens"])
                log.info("model=%s key=#%d tokens=%s latency=%.2fs",
                         name, index, entry[1], self._clock() - started)
                return True, result, True, None
        return False, None, attempted, soonest

    def run(self, models: Sequence[str], call: Callable[[str, dict, str], object], *,
            est_tokens: int = DEFAULT_EST_TOKENS, max_wait: float = MAX_WAIT):
        """Run call(model_name, settings, api_key) with load-spread key rotation and model fallback.

        Works for any provider (Moondream, Cartesia, raw Groq vision, ...).
        est_tokens: rough size of this call, used to pick a key with enough headroom.
        """
        failures: list[str] = []
        waited = 0.0
        while True:
            done, result, attempted, soonest = self._pass(
                models, call, est_tokens, True, failures)
            if done:
                return result
            if attempted:                    # real calls were made and every one failed
                raise AllModelsFailed(failures)
            if soonest is not None and waited + soonest <= max_wait:
                log.info(
                    "all keys busy, waiting %.1fs for one to free up", soonest)
                self._pause(soonest + 0.05)
                waited += max(soonest, 0.05)
                continue
            break
        # everything was cooling down and no key frees up soon: try anyway, ignoring blocks
        done, result, _a, _s = self._pass(
            models, call, est_tokens, False, failures)
        if done:
            return result
        raise AllModelsFailed(failures)

    # ---- LangChain helpers ----
    def client(self, name: str, key: str, tools: Optional[Sequence] = None, schema: Optional[type] = None):
        """The cached LangChain model for (config name, key), optionally with tools / structured output."""
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
               tools: Optional[Sequence] = None, schema: Optional[type] = None,
               est_tokens: Optional[int] = None):
        if tools and schema:
            raise ValueError("Use either tools or schema, not both.")

        def call(name, settings, key):
            result = self.client(name, key, tools, schema).invoke(messages)
            if schema and result is None:
                raise EmptyResponse(f"{name} returned no structured output")
            return result

        return self.run(models, call, est_tokens=est_tokens or _estimate_tokens(messages))

    # ---- debugging ----
    def _label(self, scope_key: tuple) -> str:
        """Readable name for a cooldown entry. Shows key NUMBER (#1, #2), never any part of the key itself."""
        parts = []
        for i, p in enumerate(scope_key):
            if i == 2 and scope_key[0] in ("auth", "rate") and p != "*":
                keys = list(self._api_keys.get(scope_key[1], ()))
                p = f"#{keys.index(p) + 1}" if p in keys else "?"
            parts.append(str(p))
        return " ".join(parts)

    def stats(self) -> dict:
        """Tokens used in the last 60s per (provider, key #, model) and active cooldowns."""
        now = self._clock()
        with self._lock:
            usage = {}
            for (provider, key, model_id), q in self._usage.items():
                total = sum(tok for t, tok in q if t > now - USAGE_WINDOW)
                if total:
                    keys = list(self._api_keys.get(provider, ()))
                    idx = keys.index(key) + 1 if key in keys else "*"
                    usage[f"{provider}#{idx}:{model_id}"] = total
            blocked = {self._label(k): round(v - now, 1)
                       for k, v in self._blocked.items() if v > now}
        return {"tokens_last_60s": usage, "cooling_down": blocked}


_gateway: Optional[LLMGateway] = None
_gateway_lock = threading.Lock()


def get_gateway() -> LLMGateway:
    global _gateway
    with _gateway_lock:
        if _gateway is None:
            from config_settings import config as cfg
            from Tools.cancel_state import check_cancel
            _gateway = LLMGateway(cfg.MODEL_CONFIGS, cfg.API_KEYS, cfg.make_llm,
                                  cancel_check=check_cancel,
                                  limits=getattr(cfg, "MODEL_LIMITS", None))
        return _gateway
