"""Universal LLM chain — fallback across 10 providers (v129).

Каждый провайдер имеет свои лимиты. Когда один упирается в лимит (429) —
автоматически переходим к следующему. Это даёт ~5-10M токенов/день
суммарно бесплатно. Anthropic как last-resort с hard spend cap.

Order (по приоритету скорости/качества):
  1.  Cerebras       — 1M tokens/day, Llama 3.3 70B (RU-IP CF-1010, proxy_via_cf)
  2.  Groq           — 100K tokens/day, Llama 3.3 70B (RU-IP CF-1010, proxy_via_cf)
  3.  SambaNova      — ~1000 req/day, Meta-Llama 3.3 70B (без CF)
  4.  Mistral        — free 1 RPS, mistral-small-latest (без CF, основной для RU)
  5.  OpenRouter     — free-models ~200 req/day на модель
  6.  Gemini         — 1500 RPD, Gemini 2.0 Flash (1.5-flash убит Google в 2026)
  7.  Together AI    — free tier (RU-IP CF-1010, proxy_via_cf)
  8.  GitHub Models  — free для GitHub users, GPT-4o-mini
  9.  Cohere         — free 1000 req/month, command-r-08-2024
  10. Anthropic      — LAST-RESORT (paid, $10 баланс, spend cap $2/day)

Usage:
    from llm_chain import llm_call
    response = llm_call("Hello", max_tokens=100, timeout=15)

CF Worker proxy (для обхода CF-1010 на RU-IP):
    Установи env CF_WORKER_PROXY=https://llm-proxy.YOUR.workers.dev
    Тогда провайдеры с proxy_via_cf=True пойдут через Worker.
"""
import os
import json
import time
import requests
from typing import Optional


# ── Provider configs ─────────────────────────────────────────────────────
PROVIDERS = [
    {
        # 1. Cerebras — free, ультра-быстрый. RU-IP блокируется CF-1010,
        #    но Railway IP работает. proxy_via_cf обходит блок.
        "name":   "cerebras",
        "env":    "CEREBRAS_API_KEY",
        "url":    "https://api.cerebras.ai/v1/chat/completions",
        "model":  "qwen-3-235b-a22b-instruct-2507",
        "format": "openai",
        "proxy_via_cf": True,
    },
    {
        # 2. Groq — free, очень быстрый. Те же условия что cerebras.
        "name":   "groq",
        "env":    "GROQ_API_KEY",
        "url":    "https://api.groq.com/openai/v1/chat/completions",
        "model":  "llama-3.3-70b-versatile",
        "format": "openai",
        "proxy_via_cf": True,
    },
    {
        # 3. SambaNova — free, ~1000 req/day. Не за CF.
        "name":   "sambanova",
        "env":    "SAMBANOVA_API_KEY",
        "url":    "https://api.sambanova.ai/v1/chat/completions",
        "model":  "Meta-Llama-3.3-70B-Instruct",
        "format": "openai",
    },
    {
        # 4. Mistral — free 1 req/sec, не за CF, основной для RU-IP.
        "name":   "mistral",
        "env":    "MISTRAL_API_KEY",
        "url":    "https://api.mistral.ai/v1/chat/completions",
        "model":  "mistral-small-latest",
        "format": "openai",
    },
    {
        # 5. OpenRouter free-models. ~200 req/day на модель.
        "name":   "openrouter",
        "env":    "OPENROUTER_API_KEY",
        "url":    "https://openrouter.ai/api/v1/chat/completions",
        "model":  "meta-llama/llama-3.3-70b-instruct:free",
        "format": "openai",
    },
    {
        # 6. Gemini 2.0-flash. Старая 1.5-flash убита Google в 2026.
        "name":   "gemini",
        "env":    "GEMINI_API_KEY",
        "url":    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
        "model":  "gemini-2.0-flash",
        "format": "gemini",
    },
    {
        # 7. Together — free tier. CF-блок для RU-IP, нужен Worker.
        "name":   "together",
        "env":    "TOGETHER_API_KEY",
        "url":    "https://api.together.xyz/v1/chat/completions",
        "model":  "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
        "format": "openai",
        "proxy_via_cf": True,
    },
    {
        # 8. GitHub Models — бесплатно для GitHub users (~150 req/day на модель).
        #    Auth: GitHub PAT с scope models:read.
        "name":   "github_models",
        "env":    "GITHUB_TOKEN",
        "url":    "https://models.github.ai/inference/chat/completions",
        "model":  "openai/gpt-4o-mini",
        "format": "openai",
    },
    {
        # 9. Cohere — free 1000 req/month.
        "name":   "cohere",
        "env":    "COHERE_API_KEY",
        "url":    "https://api.cohere.com/v2/chat",
        "model":  "command-r-08-2024",
        "format": "cohere",
    },
    {
        # 10. Anthropic — LAST-RESORT (платный, $10 баланс закинут 24.05.2026).
        #     Только если все free-провайдеры в cooldown.
        #     Hard cap: суммарный $-spend за день > $2 → выключить на 24h.
        "name":   "anthropic",
        "env":    "ANTHROPIC_API_KEY",
        "url":    "https://api.anthropic.com/v1/messages",
        "model":  "claude-haiku-4-5",
        "format": "anthropic",
        "paid":   True,
    },
]


# ── Per-provider cool-down (when 429 hit, skip for 5 min) ────────────────
_PROVIDER_COOLDOWN: dict = {}
_COOLDOWN_SEC = 300


def _is_cooled_down(name: str) -> bool:
    """Returns True if provider hit rate limit recently and should be skipped."""
    until = _PROVIDER_COOLDOWN.get(name, 0)
    return time.time() < until


def _mark_cooldown(name: str, seconds: int = _COOLDOWN_SEC):
    _PROVIDER_COOLDOWN[name] = time.time() + seconds


# ── CF Worker proxy helper ───────────────────────────────────────────────
def _maybe_proxy_url(provider: dict) -> str:
    """If provider has proxy_via_cf and CF_WORKER_PROXY env set, route
    request through the Worker. Otherwise return original URL."""
    base = provider["url"]
    if not provider.get("proxy_via_cf"):
        return base
    worker = os.environ.get("CF_WORKER_PROXY", "").rstrip("/")
    if not worker:
        return base
    # Worker expects ?u=<upstream_url>
    from urllib.parse import quote
    return f"{worker}/?u={quote(base, safe='')}"


# ── Anthropic spend tracker (hard cap $2/day) ────────────────────────────
_ANTHROPIC_SPEND_FILE = os.environ.get(
    "ANTHROPIC_SPEND_FILE",
    os.path.join(os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp",
                 "anthropic_spend.json"),
)
# Claude Haiku 4.5 pricing: $1/M input, $5/M output
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
        os.makedirs(os.path.dirname(_ANTHROPIC_SPEND_FILE), exist_ok=True)
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


# ── Provider call functions ──────────────────────────────────────────────
def _call_openai_compat(provider: dict, prompt: str, max_tokens: int,
                        timeout: int) -> Optional[str]:
    """Standard OpenAI-compatible request."""
    key = os.environ.get(provider["env"], "")
    if not key:
        return None
    try:
        r = requests.post(
            _maybe_proxy_url(provider),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"model": provider["model"],
                  "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=timeout,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        if r.status_code == 429:
            _mark_cooldown(provider["name"])
            print(f"[llm_chain] {provider['name']} 429 -> cooldown")
        elif r.status_code in (401, 403):
            _mark_cooldown(provider["name"], 86400)
            print(f"[llm_chain] {provider['name']} {r.status_code} (bad key) -> 24h skip")
        elif r.status_code == 402 or (r.status_code >= 400 and "insufficient" in r.text.lower()):
            _mark_cooldown(provider["name"], 86400)
            print(f"[llm_chain] {provider['name']} 402 (insufficient balance) -> 24h skip")
        elif r.status_code >= 400:
            print(f"[llm_chain] {provider['name']} {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[llm_chain] {provider['name']} err: {e}")
    return None


def _call_gemini(provider: dict, prompt: str, max_tokens: int,
                 timeout: int) -> Optional[str]:
    """Gemini has different request/response shape."""
    key = os.environ.get(provider["env"], "")
    if not key:
        return None
    try:
        r = requests.post(
            f'{provider["url"]}?key={key}',
            headers={"Content-Type": "application/json"},
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
            _mark_cooldown(provider["name"])
            print(f"[llm_chain] gemini 429 -> cooldown")
        elif r.status_code in (401, 403):
            _mark_cooldown(provider["name"], 86400)
            print(f"[llm_chain] gemini {r.status_code} (bad key) -> 24h skip")
        elif r.status_code == 404:
            _mark_cooldown(provider["name"], 86400)
            print(f"[llm_chain] gemini 404 (model gone) -> 24h skip")
        elif r.status_code >= 400:
            print(f"[llm_chain] gemini {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[llm_chain] gemini err: {e}")
    return None


def _call_anthropic(provider: dict, prompt: str, max_tokens: int,
                    timeout: int) -> Optional[str]:
    """Anthropic Claude format. Hard $2/day cap."""
    key = os.environ.get(provider["env"], "")
    if not key:
        return None
    if _anthropic_over_cap():
        _mark_cooldown(provider["name"], 86400)
        print(f"[llm_chain] anthropic over $2/day cap -> 24h skip")
        return None
    try:
        r = requests.post(
            provider["url"],
            headers={"x-api-key": key,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
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
            _mark_cooldown(provider["name"])
            print(f"[llm_chain] anthropic 429 -> cooldown")
        elif r.status_code in (401, 403):
            _mark_cooldown(provider["name"], 86400)
            print(f"[llm_chain] anthropic {r.status_code} (bad key) -> 24h skip")
        elif r.status_code == 400 and "credit balance" in r.text.lower():
            _mark_cooldown(provider["name"], 86400)
            print(f"[llm_chain] anthropic OUT OF CREDIT -> 24h skip (top up needed)")
        elif r.status_code >= 400:
            print(f"[llm_chain] anthropic {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[llm_chain] anthropic err: {e}")
    return None


def _call_cohere(provider: dict, prompt: str, max_tokens: int,
                 timeout: int) -> Optional[str]:
    """Cohere v2 chat format."""
    key = os.environ.get(provider["env"], "")
    if not key:
        return None
    try:
        r = requests.post(
            provider["url"],
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
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
            # Fallback shape (text field)
            if data.get("text"):
                return data["text"].strip()
            return None
        if r.status_code == 429:
            _mark_cooldown(provider["name"])
            print(f"[llm_chain] cohere 429 -> cooldown")
        elif r.status_code in (401, 403):
            _mark_cooldown(provider["name"], 86400)
            print(f"[llm_chain] cohere {r.status_code} (bad key) -> 24h skip")
        elif r.status_code >= 400:
            print(f"[llm_chain] cohere {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[llm_chain] cohere err: {e}")
    return None


# ── Public API ───────────────────────────────────────────────────────────
def llm_call(prompt: str, max_tokens: int = 600, timeout: int = 15) -> Optional[str]:
    """Universal LLM call с fallback chain.
    Возвращает ответ от первого работающего провайдера или None если все
    в cooldown / без ключей."""
    for provider in PROVIDERS:
        if not os.environ.get(provider["env"]):
            continue
        if _is_cooled_down(provider["name"]):
            continue

        fmt = provider.get("format", "openai")
        if fmt == "openai":
            result = _call_openai_compat(provider, prompt, max_tokens, timeout)
        elif fmt == "gemini":
            result = _call_gemini(provider, prompt, max_tokens, timeout)
        elif fmt == "anthropic":
            result = _call_anthropic(provider, prompt, max_tokens, timeout)
        elif fmt == "cohere":
            result = _call_cohere(provider, prompt, max_tokens, timeout)
        else:
            continue

        if result:
            return result

    return None


def status() -> dict:
    """Returns active/cooldown status of each provider."""
    out = {}
    for p in PROVIDERS:
        has_key = bool(os.environ.get(p["env"]))
        cooled = _is_cooled_down(p["name"])
        out[p["name"]] = {
            "configured": has_key,
            "available": has_key and not cooled,
            "cooldown_until": _PROVIDER_COOLDOWN.get(p["name"], 0),
            "paid": bool(p.get("paid")),
            "proxy_via_cf": bool(p.get("proxy_via_cf")),
        }
    # Anthropic spend info
    spend = _load_spend()
    out["_anthropic_spend"] = {
        "day": spend.get("day"),
        "usd": round(_anthropic_spend_usd(spend), 4),
        "cap_usd": _ANTHROPIC_DAILY_CAP,
        "over_cap": _anthropic_over_cap(),
    }
    out["_cf_worker_proxy"] = os.environ.get("CF_WORKER_PROXY", "") or None
    return out


def health_check_all(timeout: int = 10) -> dict:
    """Минимальный prompt на каждый провайдер. Возвращает
    {"<name>": {"status": "ok|auth_fail|rate_limit|network|no_key|model_gone|other", "code": HTTP_or_-1}}.
    Используется в админ-команде бота."""
    out: dict = {}
    test_prompt = "Reply with one word: ok"
    for p in PROVIDERS:
        name = p["name"]
        key = os.environ.get(p["env"], "")
        if not key:
            out[name] = {"status": "no_key", "code": 0}
            continue
        fmt = p.get("format", "openai")
        try:
            if fmt == "openai":
                r = requests.post(
                    _maybe_proxy_url(p),
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json={"model": p["model"], "max_tokens": 10,
                          "messages": [{"role": "user", "content": test_prompt}]},
                    timeout=timeout,
                )
            elif fmt == "gemini":
                r = requests.post(
                    f'{p["url"]}?key={key}',
                    headers={"Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": test_prompt}]}],
                          "generationConfig": {"maxOutputTokens": 10}},
                    timeout=timeout,
                )
            elif fmt == "anthropic":
                r = requests.post(
                    p["url"],
                    headers={"x-api-key": key,
                             "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": p["model"], "max_tokens": 10,
                          "messages": [{"role": "user", "content": test_prompt}]},
                    timeout=timeout,
                )
            elif fmt == "cohere":
                r = requests.post(
                    p["url"],
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json={"model": p["model"], "max_tokens": 10,
                          "messages": [{"role": "user", "content": test_prompt}]},
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
    import json as _json
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "health":
        print(_json.dumps(health_check_all(), indent=2))
    else:
        print(_json.dumps(status(), indent=2))
        print()
        print("--- Test call ---")
        resp = llm_call("Say 'hello world' in one word.", max_tokens=10)
        print(f"Response: {resp!r}")
