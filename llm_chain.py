"""Universal LLM chain v130 — multi-key rotation + token-bucket + Postgres cache.

Goal: guarantee >=5 working providers at any moment by combining:
  1) Multi-key rotation INSIDE each provider (CEREBRAS_API_KEY_2, _3 etc.)
  2) Proactive token-bucket rate limiter per key (avoid hitting 429 at all)
  3) Postgres LLM cache (7-day TTL) — repeat prompts skip the API entirely
  4) Cloudflare Worker proxy rotation (CF_WORKER_PROXY[_2,_3])
  5) Ollama self-hosted fallback on Railway private network (unlimited)
  6) Per-key hourly cap (500 req/hour) to stay below provider thresholds
  7) Anthropic last-resort with $2/day cap (existing)

Providers (priority order):
  1.  Cerebras       — 1M tok/day, Qwen-3 235B (CF proxy, multi-key)
  2.  Groq           — 100K tok/day, Llama 3.3 70B (CF proxy, multi-key)
  3.  SambaNova      — ~1000 req/day, Llama 3.3 70B (multi-key)
  4.  Mistral        — free 1 RPS, mistral-small-latest (multi-key)
  5.  OpenRouter     — free models ~200 req/day (multi-key)
  6.  Gemini         — 1500 RPD, Gemini 2.0 Flash (multi-key)
  7.  Together AI    — free tier Llama 3.3 70B Turbo (CF proxy, multi-key)
  8.  GitHub Models  — ~150 req/day, GPT-4o-mini (multi-key)
  9.  Cohere         — 1000 req/month, command-r (multi-key)
  10. Ollama self    — UNLIMITED, llama3.2:3b, Railway internal
  11. Anthropic      — LAST-RESORT, $2/day hard cap

Usage:
    from llm_chain import llm_call
    response = llm_call("Hello", max_tokens=100, timeout=15)

Backward-compat: if *_KEY_2 / *_KEY_3 env vars are not set, behaves
exactly like v129 with a single key per provider.
"""
import os
import json
import time
import random
import hashlib
import threading
import requests
from typing import Optional, List


# ── Provider configs ─────────────────────────────────────────────────────
# env_keys: list of env-var NAMES to try as keys for this provider.
# rpm: requests-per-minute target (used to compute token-bucket refill).
PROVIDERS = [
    {
        "name":   "cerebras",
        "env_keys": ["CEREBRAS_API_KEY", "CEREBRAS_API_KEY_2", "CEREBRAS_API_KEY_3"],
        "url":    "https://api.cerebras.ai/v1/chat/completions",
        "model":  "qwen-3-235b-a22b-instruct-2507",
        "format": "openai",
        "proxy_via_cf": True,
        "rpm":    14,
    },
    {
        "name":   "groq",
        "env_keys": ["GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3"],
        "url":    "https://api.groq.com/openai/v1/chat/completions",
        "model":  "llama-3.3-70b-versatile",
        "format": "openai",
        "proxy_via_cf": True,
        "rpm":    30,
    },
    {
        "name":   "sambanova",
        "env_keys": ["SAMBANOVA_API_KEY", "SAMBANOVA_API_KEY_2", "SAMBANOVA_API_KEY_3"],
        "url":    "https://api.sambanova.ai/v1/chat/completions",
        "model":  "Meta-Llama-3.3-70B-Instruct",
        "format": "openai",
        "rpm":    20,
    },
    {
        "name":   "mistral",
        "env_keys": ["MISTRAL_API_KEY", "MISTRAL_API_KEY_2", "MISTRAL_API_KEY_3"],
        "url":    "https://api.mistral.ai/v1/chat/completions",
        "model":  "mistral-small-latest",
        "format": "openai",
        "rpm":    55,  # mistral free = 1 RPS
    },
    {
        "name":   "openrouter",
        "env_keys": ["OPENROUTER_API_KEY", "OPENROUTER_API_KEY_2", "OPENROUTER_API_KEY_3"],
        "url":    "https://openrouter.ai/api/v1/chat/completions",
        "model":  "meta-llama/llama-3.3-70b-instruct:free",
        "format": "openai",
        "rpm":    20,
    },
    {
        "name":   "gemini",
        "env_keys": ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"],
        "url":    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
        "model":  "gemini-2.0-flash",
        "format": "gemini",
        "rpm":    14,  # 15 RPM on free tier
    },
    {
        "name":   "together",
        "env_keys": ["TOGETHER_API_KEY", "TOGETHER_API_KEY_2", "TOGETHER_API_KEY_3"],
        "url":    "https://api.together.xyz/v1/chat/completions",
        "model":  "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
        "format": "openai",
        "proxy_via_cf": True,
        "rpm":    10,
    },
    {
        "name":   "github_models",
        "env_keys": ["GITHUB_TOKEN", "GITHUB_TOKEN_2", "GITHUB_TOKEN_3",
                     "GITHUB_MODELS_TOKEN"],
        "url":    "https://models.github.ai/inference/chat/completions",
        "model":  "openai/gpt-4o-mini",
        "format": "openai",
        "rpm":    10,
    },
    {
        "name":   "cohere",
        "env_keys": ["COHERE_API_KEY", "COHERE_API_KEY_2", "COHERE_API_KEY_3"],
        "url":    "https://api.cohere.com/v2/chat",
        "model":  "command-r-08-2024",
        "format": "cohere",
        "rpm":    20,
    },
    {
        # 10. Ollama self-hosted on Railway private network (unlimited).
        #     URL via env OLLAMA_URL (default Railway internal hostname).
        "name":   "ollama_self",
        "env_keys": [],            # no auth
        "url":    os.getenv("OLLAMA_URL", "http://ollama.railway.internal:11434/api/chat"),
        "model":  os.getenv("OLLAMA_MODEL", "llama3.2:3b"),
        "format": "ollama",
        "rpm":    600,             # effectively unlimited
        "unlimited": True,
    },
    {
        "name":   "anthropic",
        "env_keys": ["ANTHROPIC_API_KEY"],
        "url":    "https://api.anthropic.com/v1/messages",
        "model":  "claude-haiku-4-5",
        "format": "anthropic",
        "paid":   True,
        "rpm":    50,
    },
]


# ── Per-key cool-down (when 429 hit on a specific key) ───────────────────
# state key: (provider_name, key_idx) -> cooldown_until_epoch
_KEY_COOLDOWN: dict = {}
_KEY_COOLDOWN_LOCK = threading.Lock()
_COOLDOWN_SEC = 60   # short — we have other keys
_LONG_COOLDOWN = 86400


def _key_cooled(provider_name: str, key_idx: int) -> bool:
    with _KEY_COOLDOWN_LOCK:
        until = _KEY_COOLDOWN.get((provider_name, key_idx), 0)
    return time.time() < until


def _mark_key_cooldown(provider_name: str, key_idx: int,
                      seconds: int = _COOLDOWN_SEC):
    with _KEY_COOLDOWN_LOCK:
        _KEY_COOLDOWN[(provider_name, key_idx)] = time.time() + seconds


# ── Per-key hourly cap (500 req/hour) ────────────────────────────────────
_KEY_HOURLY: dict = {}   # (name, idx) -> list[epoch] of recent calls
_KEY_HOURLY_LOCK = threading.Lock()
_HOURLY_CAP = int(os.getenv("LLM_KEY_HOURLY_CAP", "500"))


def _record_key_call(provider_name: str, key_idx: int):
    now = time.time()
    with _KEY_HOURLY_LOCK:
        lst = _KEY_HOURLY.setdefault((provider_name, key_idx), [])
        lst.append(now)
        cutoff = now - 3600
        # Keep only last hour
        while lst and lst[0] < cutoff:
            lst.pop(0)
        if len(lst) >= _HOURLY_CAP:
            _mark_key_cooldown(provider_name, key_idx, 3600)


def _key_over_hourly_cap(provider_name: str, key_idx: int) -> bool:
    now = time.time()
    with _KEY_HOURLY_LOCK:
        lst = _KEY_HOURLY.get((provider_name, key_idx), [])
        cutoff = now - 3600
        recent = sum(1 for t in lst if t >= cutoff)
    return recent >= _HOURLY_CAP


# ── Token bucket (per-key) ───────────────────────────────────────────────
class TokenBucket:
    __slots__ = ("capacity", "tokens", "refill", "last_refill", "lock")

    def __init__(self, capacity: int, refill_per_sec: float):
        self.capacity = capacity
        self.tokens = float(capacity)
        self.refill = refill_per_sec
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill)
            self.last_refill = now

    def acquire(self, n: int = 1, blocking: bool = True,
                max_wait: float = 30.0) -> bool:
        deadline = time.monotonic() + max_wait if blocking else 0
        while True:
            with self.lock:
                self._refill()
                if self.tokens >= n:
                    self.tokens -= n
                    return True
                if not blocking:
                    return False
                # Sleep just long enough for n tokens
                needed = (n - self.tokens) / self.refill if self.refill > 0 else 1.0
            if time.monotonic() + needed > deadline:
                return False
            time.sleep(min(needed, 1.0))


_BUCKETS: dict = {}   # (provider_name, key_idx) -> TokenBucket
_BUCKETS_LOCK = threading.Lock()


def _get_bucket(provider: dict, key_idx: int) -> TokenBucket:
    name = provider["name"]
    rpm = max(1, int(provider.get("rpm", 20)))
    refill = rpm / 60.0
    with _BUCKETS_LOCK:
        b = _BUCKETS.get((name, key_idx))
        if b is None:
            b = TokenBucket(capacity=rpm, refill_per_sec=refill)
            _BUCKETS[(name, key_idx)] = b
        return b


# ── Key selection ────────────────────────────────────────────────────────
_RR_INDEX: dict = {}   # provider_name -> next start idx (round-robin)
_RR_LOCK = threading.Lock()


def _available_keys(provider: dict) -> List[tuple]:
    """Returns list of (key_idx, key_value) tuples for keys that are
    configured (env set) and not currently cooled-down."""
    out = []
    for idx, env_name in enumerate(provider.get("env_keys", [])):
        val = os.environ.get(env_name)
        if not val:
            continue
        if _key_cooled(provider["name"], idx):
            continue
        if _key_over_hourly_cap(provider["name"], idx):
            continue
        out.append((idx, val))
    return out


def _pick_key(provider: dict):
    """Round-robin between available keys. Returns (key_idx, key_value) or
    (None, None) if no keys are usable."""
    keys = _available_keys(provider)
    if not keys:
        return (None, None)
    name = provider["name"]
    with _RR_LOCK:
        start = _RR_INDEX.get(name, 0)
        _RR_INDEX[name] = (start + 1) % max(1, len(keys))
    return keys[start % len(keys)]


# ── CF Worker proxy rotation ─────────────────────────────────────────────
def _cf_workers() -> List[str]:
    return [u.rstrip("/") for u in [
        os.getenv("CF_WORKER_PROXY"),
        os.getenv("CF_WORKER_PROXY_2"),
        os.getenv("CF_WORKER_PROXY_3"),
    ] if u]


def _maybe_proxy_url(provider: dict) -> str:
    base = provider["url"]
    if not provider.get("proxy_via_cf"):
        return base
    workers = _cf_workers()
    if not workers:
        return base
    worker = random.choice(workers)
    from urllib.parse import quote
    return f"{worker}/?u={quote(base, safe='')}"


# ── Anthropic spend tracker (hard cap $2/day) ────────────────────────────
_ANTHROPIC_SPEND_FILE = os.environ.get(
    "ANTHROPIC_SPEND_FILE",
    os.path.join(os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp",
                 "anthropic_spend.json"),
)
_ANTHROPIC_PRICE_IN  = 1.0 / 1_000_000
_ANTHROPIC_PRICE_OUT = 5.0 / 1_000_000
_ANTHROPIC_DAILY_CAP = 2.0  # USD


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _load_spend() -> dict:
    try:
        with open(_ANTHROPIC_SPEND_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("day") != _today():
            return {"day": _today(), "input_tokens": 0, "output_tokens": 0}
        return data
    except Exception:
        return {"day": _today(), "input_tokens": 0, "output_tokens": 0}


def _save_spend(data: dict):
    try:
        d = os.path.dirname(_ANTHROPIC_SPEND_FILE)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(_ANTHROPIC_SPEND_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[llm_chain] anthropic spend save err: {e}")


def _anthropic_spend_usd(data: dict) -> float:
    return (data.get("input_tokens", 0) * _ANTHROPIC_PRICE_IN +
            data.get("output_tokens", 0) * _ANTHROPIC_PRICE_OUT)


def _anthropic_over_cap() -> bool:
    return _anthropic_spend_usd(_load_spend()) >= _ANTHROPIC_DAILY_CAP


def _anthropic_add_usage(input_tokens: int, output_tokens: int):
    data = _load_spend()
    data["input_tokens"] = data.get("input_tokens", 0) + int(input_tokens or 0)
    data["output_tokens"] = data.get("output_tokens", 0) + int(output_tokens or 0)
    _save_spend(data)


# ── Postgres LLM cache (7-day TTL) ───────────────────────────────────────
_PG_CACHE_INIT = False
_PG_CACHE_LOCK = threading.Lock()
_PG_CACHE_DISABLED = False


def _pg_conn():
    """Open psycopg2 connection or return None (graceful)."""
    global _PG_CACHE_DISABLED
    if _PG_CACHE_DISABLED:
        return None
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    try:
        import psycopg2
        return psycopg2.connect(dsn, connect_timeout=5)
    except Exception as e:
        # Mark disabled to avoid retry storms
        _PG_CACHE_DISABLED = True
        print(f"[llm_chain] pg cache disabled (connect err): {e}")
        return None


def _pg_cache_init():
    global _PG_CACHE_INIT
    if _PG_CACHE_INIT:
        return
    with _PG_CACHE_LOCK:
        if _PG_CACHE_INIT:
            return
        conn = _pg_conn()
        if conn is None:
            _PG_CACHE_INIT = True
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS llm_cache (
                        prompt_hash    TEXT PRIMARY KEY,
                        provider       TEXT NOT NULL,
                        model          TEXT NOT NULL,
                        prompt_snippet TEXT,
                        response       TEXT NOT NULL,
                        tokens_in      INT,
                        tokens_out     INT,
                        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        expires_at     TIMESTAMPTZ NOT NULL
                    );
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_llm_cache_expires
                        ON llm_cache(expires_at);
                """)
            conn.commit()
        except Exception as e:
            print(f"[llm_chain] pg cache init err: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass
        _PG_CACHE_INIT = True


def _hash_prompt(model: str, prompt: str) -> str:
    h = hashlib.sha256()
    h.update(model.encode("utf-8", "ignore"))
    h.update(b"\n--\n")
    h.update(prompt.encode("utf-8", "ignore"))
    return h.hexdigest()


def _cache_lookup(prompt: str) -> Optional[str]:
    """Try every (provider, model) hash variant — but since we hash by
    (model+prompt), we lookup by content-only hash. To keep this simple:
    we hash with model='*' so any provider's cached answer matches."""
    if os.getenv("LLM_CACHE_DISABLE") == "1":
        return None
    _pg_cache_init()
    conn = _pg_conn()
    if conn is None:
        return None
    try:
        h = _hash_prompt("*", prompt)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT response FROM llm_cache
                 WHERE prompt_hash = %s
                   AND expires_at > NOW()
                 LIMIT 1
            """, (h,))
            row = cur.fetchone()
        if row:
            return row[0]
    except Exception as e:
        print(f"[llm_chain] cache lookup err: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return None


def _cache_store(prompt: str, response: str, provider: str, model: str,
                 tokens_in: int = 0, tokens_out: int = 0,
                 ttl_days: int = 7):
    if os.getenv("LLM_CACHE_DISABLE") == "1":
        return
    if not response:
        return
    _pg_cache_init()
    conn = _pg_conn()
    if conn is None:
        return
    try:
        h = _hash_prompt("*", prompt)
        snippet = (prompt or "")[:200]
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO llm_cache (prompt_hash, provider, model,
                                       prompt_snippet, response,
                                       tokens_in, tokens_out, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s,
                        NOW() + (%s || ' days')::interval)
                ON CONFLICT (prompt_hash) DO UPDATE
                  SET response   = EXCLUDED.response,
                      provider   = EXCLUDED.provider,
                      model      = EXCLUDED.model,
                      tokens_in  = EXCLUDED.tokens_in,
                      tokens_out = EXCLUDED.tokens_out,
                      expires_at = EXCLUDED.expires_at
            """, (h, provider, model, snippet, response,
                  tokens_in, tokens_out, str(ttl_days)))
        conn.commit()
    except Exception as e:
        print(f"[llm_chain] cache store err: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Common request headers (anti-ban) ────────────────────────────────────
_UA_POOL = [
    "openai-python/1.54.0",
    "Python/3.11 anthropic-sdk-python/0.34.0",
    "Mistralai/1.2.5 Python/3.11",
    "groq/0.13.0 python-requests/2.32",
]


def _common_headers() -> dict:
    return {"User-Agent": random.choice(_UA_POOL)}


# ── Provider call functions ──────────────────────────────────────────────
def _call_openai_compat(provider: dict, key_idx: int, key: str, prompt: str,
                        max_tokens: int, timeout: int) -> Optional[str]:
    try:
        headers = _common_headers()
        headers.update({"Authorization": f"Bearer {key}",
                        "Content-Type": "application/json"})
        r = requests.post(
            _maybe_proxy_url(provider),
            headers=headers,
            json={"model": provider["model"],
                  "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=timeout,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        if r.status_code == 429:
            _mark_key_cooldown(provider["name"], key_idx, _COOLDOWN_SEC)
            print(f"[llm_chain] {provider['name']}#{key_idx} 429 -> 60s cooldown")
        elif r.status_code in (401, 403):
            _mark_key_cooldown(provider["name"], key_idx, _LONG_COOLDOWN)
            print(f"[llm_chain] {provider['name']}#{key_idx} {r.status_code} (bad key) -> 24h skip")
        elif r.status_code == 402 or (r.status_code >= 400 and "insufficient" in r.text.lower()):
            _mark_key_cooldown(provider["name"], key_idx, _LONG_COOLDOWN)
            print(f"[llm_chain] {provider['name']}#{key_idx} 402 (no balance) -> 24h skip")
        elif r.status_code >= 400:
            print(f"[llm_chain] {provider['name']}#{key_idx} {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[llm_chain] {provider['name']}#{key_idx} err: {e}")
    return None


def _call_gemini(provider: dict, key_idx: int, key: str, prompt: str,
                 max_tokens: int, timeout: int) -> Optional[str]:
    try:
        headers = _common_headers()
        headers["Content-Type"] = "application/json"
        r = requests.post(
            f'{provider["url"]}?key={key}',
            headers=headers,
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens,
                                     "temperature": 0.2},
            },
            timeout=timeout,
        )
        if r.status_code == 200:
            data = r.json()
            cands = data.get("candidates") or []
            if cands:
                parts = (cands[0].get("content") or {}).get("parts") or []
                if parts and parts[0].get("text"):
                    return parts[0]["text"].strip()
        if r.status_code == 429:
            _mark_key_cooldown(provider["name"], key_idx, _COOLDOWN_SEC)
            print(f"[llm_chain] gemini#{key_idx} 429 -> 60s cooldown")
        elif r.status_code in (401, 403):
            _mark_key_cooldown(provider["name"], key_idx, _LONG_COOLDOWN)
            print(f"[llm_chain] gemini#{key_idx} {r.status_code} (bad key) -> 24h skip")
        elif r.status_code == 404:
            _mark_key_cooldown(provider["name"], key_idx, _LONG_COOLDOWN)
            print(f"[llm_chain] gemini#{key_idx} 404 (model gone) -> 24h skip")
        elif r.status_code >= 400:
            print(f"[llm_chain] gemini#{key_idx} {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[llm_chain] gemini#{key_idx} err: {e}")
    return None


def _call_anthropic(provider: dict, key_idx: int, key: str, prompt: str,
                    max_tokens: int, timeout: int) -> Optional[str]:
    if _anthropic_over_cap():
        _mark_key_cooldown(provider["name"], key_idx, _LONG_COOLDOWN)
        print(f"[llm_chain] anthropic over $2/day cap -> 24h skip")
        return None
    try:
        headers = _common_headers()
        headers.update({"x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"})
        r = requests.post(
            provider["url"],
            headers=headers,
            json={"model": provider["model"],
                  "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = r.json()
            usage = data.get("usage") or {}
            _anthropic_add_usage(
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
            )
            content = data.get("content") or []
            if content and content[0].get("text"):
                return content[0]["text"].strip()
            return None
        if r.status_code == 429:
            _mark_key_cooldown(provider["name"], key_idx, _COOLDOWN_SEC)
            print(f"[llm_chain] anthropic 429 -> 60s cooldown")
        elif r.status_code in (401, 403):
            _mark_key_cooldown(provider["name"], key_idx, _LONG_COOLDOWN)
        elif r.status_code == 400 and "credit balance" in r.text.lower():
            _mark_key_cooldown(provider["name"], key_idx, _LONG_COOLDOWN)
            print(f"[llm_chain] anthropic OUT OF CREDIT -> 24h skip")
        elif r.status_code >= 400:
            print(f"[llm_chain] anthropic {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[llm_chain] anthropic err: {e}")
    return None


def _call_cohere(provider: dict, key_idx: int, key: str, prompt: str,
                 max_tokens: int, timeout: int) -> Optional[str]:
    try:
        headers = _common_headers()
        headers.update({"Authorization": f"Bearer {key}",
                        "Content-Type": "application/json"})
        r = requests.post(
            provider["url"],
            headers=headers,
            json={"model": provider["model"],
                  "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = r.json()
            msg = data.get("message") or {}
            content = msg.get("content") or []
            if content and content[0].get("text"):
                return content[0]["text"].strip()
            if data.get("text"):
                return data["text"].strip()
            return None
        if r.status_code == 429:
            _mark_key_cooldown(provider["name"], key_idx, _COOLDOWN_SEC)
        elif r.status_code in (401, 403):
            _mark_key_cooldown(provider["name"], key_idx, _LONG_COOLDOWN)
        elif r.status_code >= 400:
            print(f"[llm_chain] cohere#{key_idx} {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[llm_chain] cohere#{key_idx} err: {e}")
    return None


def _call_ollama(provider: dict, key_idx: int, key: str, prompt: str,
                 max_tokens: int, timeout: int) -> Optional[str]:
    """Ollama-format (no auth, JSON {model, messages, stream:false})."""
    try:
        headers = _common_headers()
        headers["Content-Type"] = "application/json"
        r = requests.post(
            provider["url"],
            headers=headers,
            json={"model": provider["model"],
                  "messages": [{"role": "user", "content": prompt}],
                  "stream": False,
                  "options": {"num_predict": max_tokens}},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = r.json()
            msg = data.get("message") or {}
            if msg.get("content"):
                return msg["content"].strip()
            if data.get("response"):
                return data["response"].strip()
            return None
        if r.status_code == 429:
            _mark_key_cooldown(provider["name"], key_idx, _COOLDOWN_SEC)
        elif r.status_code >= 400:
            print(f"[llm_chain] ollama_self {r.status_code}: {r.text[:120]}")
    except Exception as e:
        # Ollama unreachable is expected outside Railway — quiet but mark cooldown
        _mark_key_cooldown(provider["name"], key_idx, 300)
        print(f"[llm_chain] ollama_self err: {e}")
    return None


_DISPATCH = {
    "openai":    _call_openai_compat,
    "gemini":    _call_gemini,
    "anthropic": _call_anthropic,
    "cohere":    _call_cohere,
    "ollama":    _call_ollama,
}


# ── Public API ───────────────────────────────────────────────────────────
def llm_call(prompt: str, max_tokens: int = 600,
             timeout: int = 15, use_cache: bool = True) -> Optional[str]:
    """Universal LLM call with multi-key + token-bucket + Postgres cache.

    Returns response text from first working provider or None.
    """
    # Cache lookup
    if use_cache:
        cached = _cache_lookup(prompt)
        if cached:
            return cached

    for provider in PROVIDERS:
        fmt = provider.get("format", "openai")

        # ollama_self has no env keys — use synthetic single "key"
        if not provider.get("env_keys"):
            # only if URL is reachable (we let the call fail and cooldown)
            key_idx = 0
            if _key_cooled(provider["name"], key_idx):
                continue
            bucket = _get_bucket(provider, key_idx)
            if not bucket.acquire(1, blocking=True, max_wait=2.0):
                continue
            _record_key_call(provider["name"], key_idx)
            func = _DISPATCH.get(fmt)
            if not func:
                continue
            result = func(provider, key_idx, "", prompt, max_tokens, timeout)
            if result:
                if use_cache:
                    _cache_store(prompt, result, provider["name"], provider["model"])
                return result
            continue

        # Try each available key for this provider
        keys = _available_keys(provider)
        if not keys:
            continue
        # round-robin start offset
        with _RR_LOCK:
            start = _RR_INDEX.get(provider["name"], 0) % len(keys)
            _RR_INDEX[provider["name"]] = (start + 1) % len(keys)

        ordered = keys[start:] + keys[:start]
        for key_idx, key in ordered:
            bucket = _get_bucket(provider, key_idx)
            if not bucket.acquire(1, blocking=True, max_wait=2.0):
                # bucket empty even after wait — try next key
                continue
            _record_key_call(provider["name"], key_idx)
            func = _DISPATCH.get(fmt)
            if not func:
                break
            result = func(provider, key_idx, key, prompt, max_tokens, timeout)
            if result:
                if use_cache:
                    _cache_store(prompt, result, provider["name"], provider["model"])
                return result
            # this key failed — move to next key in same provider
        # all keys for this provider failed → next provider
    return None


def status() -> dict:
    """Returns active/cooldown status of each provider and per-key state."""
    out = {}
    for p in PROVIDERS:
        env_keys = p.get("env_keys", [])
        keys_info = []
        any_available = False
        for idx, env_name in enumerate(env_keys):
            has = bool(os.environ.get(env_name))
            cooled = _key_cooled(p["name"], idx) if has else False
            avail = has and not cooled and not _key_over_hourly_cap(p["name"], idx)
            if avail:
                any_available = True
            keys_info.append({
                "env": env_name,
                "configured": has,
                "available": avail,
                "cooldown_until": _KEY_COOLDOWN.get((p["name"], idx), 0),
            })
        # ollama_self: no env keys, always "configured"
        if not env_keys:
            cooled = _key_cooled(p["name"], 0)
            any_available = not cooled
            keys_info.append({
                "env": "(no-auth)", "configured": True,
                "available": any_available,
                "cooldown_until": _KEY_COOLDOWN.get((p["name"], 0), 0),
            })
        out[p["name"]] = {
            "available": any_available,
            "paid": bool(p.get("paid")),
            "unlimited": bool(p.get("unlimited")),
            "proxy_via_cf": bool(p.get("proxy_via_cf")),
            "keys": keys_info,
        }
    spend = _load_spend()
    out["_anthropic_spend"] = {
        "day": spend.get("day"),
        "usd": round(_anthropic_spend_usd(spend), 4),
        "cap_usd": _ANTHROPIC_DAILY_CAP,
        "over_cap": _anthropic_over_cap(),
    }
    out["_cf_workers"] = _cf_workers() or None
    out["_pg_cache"] = "disabled" if _PG_CACHE_DISABLED else (
        "active" if os.environ.get("DATABASE_URL") else "no_dsn")
    return out


def health_check_all(timeout: int = 10) -> dict:
    """Minimal prompt to each provider (one key per provider). Returns
    {name: {status, code}} for monitoring."""
    out: dict = {}
    test_prompt = "Reply with one word: ok"
    for p in PROVIDERS:
        name = p["name"]
        env_keys = p.get("env_keys", [])

        # pick first available key
        key = ""
        key_idx = 0
        if env_keys:
            for idx, env_name in enumerate(env_keys):
                v = os.environ.get(env_name)
                if v:
                    key = v
                    key_idx = idx
                    break
            if not key:
                out[name] = {"status": "no_key", "code": 0}
                continue

        fmt = p.get("format", "openai")
        try:
            headers = _common_headers()
            if fmt == "openai":
                headers.update({"Authorization": f"Bearer {key}",
                                "Content-Type": "application/json"})
                r = requests.post(
                    _maybe_proxy_url(p),
                    headers=headers,
                    json={"model": p["model"], "max_tokens": 10,
                          "messages": [{"role": "user", "content": test_prompt}]},
                    timeout=timeout,
                )
            elif fmt == "gemini":
                headers["Content-Type"] = "application/json"
                r = requests.post(
                    f'{p["url"]}?key={key}',
                    headers=headers,
                    json={"contents": [{"parts": [{"text": test_prompt}]}],
                          "generationConfig": {"maxOutputTokens": 10}},
                    timeout=timeout,
                )
            elif fmt == "anthropic":
                headers.update({"x-api-key": key,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json"})
                r = requests.post(
                    p["url"], headers=headers,
                    json={"model": p["model"], "max_tokens": 10,
                          "messages": [{"role": "user", "content": test_prompt}]},
                    timeout=timeout,
                )
            elif fmt == "cohere":
                headers.update({"Authorization": f"Bearer {key}",
                                "Content-Type": "application/json"})
                r = requests.post(
                    p["url"], headers=headers,
                    json={"model": p["model"], "max_tokens": 10,
                          "messages": [{"role": "user", "content": test_prompt}]},
                    timeout=timeout,
                )
            elif fmt == "ollama":
                headers["Content-Type"] = "application/json"
                r = requests.post(
                    p["url"], headers=headers,
                    json={"model": p["model"],
                          "messages": [{"role": "user", "content": test_prompt}],
                          "stream": False,
                          "options": {"num_predict": 10}},
                    timeout=timeout,
                )
            else:
                out[name] = {"status": "other", "code": -1}
                continue
            code = r.status_code
            if code == 200:
                out[name] = {"status": "ok", "code": 200}
            elif code == 429:
                out[name] = {"status": "rate_limit", "code": 429}
            elif code in (401, 403):
                out[name] = {"status": "auth_fail", "code": code}
            elif code == 404:
                out[name] = {"status": "model_gone", "code": 404}
            elif code == 402:
                out[name] = {"status": "payment_required", "code": 402}
            else:
                snippet = (r.text or "")[:100]
                out[name] = {"status": "other", "code": code, "body": snippet}
        except requests.exceptions.Timeout:
            out[name] = {"status": "network", "code": -1, "error": "timeout"}
        except Exception as e:
            out[name] = {"status": "network", "code": -1, "error": str(e)[:100]}
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "health":
        result = health_check_all()
        print(json.dumps(result, indent=2))
        working = sum(1 for v in result.values() if v.get("status") == "ok")
        print(f"\n*** {working} working providers ***")
    elif len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(status(), indent=2, default=str))
    else:
        print(json.dumps(status(), indent=2, default=str))
        print()
        print("--- Test call ---")
        resp = llm_call("Say 'hello world' in one word.", max_tokens=10)
        print(f"Response: {resp!r}")
