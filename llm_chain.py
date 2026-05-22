"""Universal LLM chain — fallback across 7 providers.

Каждый провайдер имеет свои лимиты. Когда один упирается в лимит (429) —
автоматически переходим к следующему. Это даёт ~3-5M токенов/день
суммарно бесплатно.

Order (по приоритету скорости/качества):
  1. Cerebras   — 1M tokens/day, Llama 3.3 70B (быстрее всех)
  2. Groq       — 100K tokens/day, Llama 3.3 70B
  3. SambaNova  — 1M tokens/day, Llama 3.1 405B (мощнее)
  4. Mistral    — щедрый free tier, Mistral Large
  5. Gemini     — 1500 RPD, Gemini 1.5 Flash
  6. OpenRouter — free model rotation
  7. Anthropic  — Claude Haiku (paid но есть credits)

Usage:
    from llm_chain import llm_call
    response = llm_call("Hello", max_tokens=100, timeout=15)
"""
import os
import time
import requests
from typing import Optional


# ── Provider configs ─────────────────────────────────────────────────────
PROVIDERS = [
    {
        # Cerebras — самый быстрый (фактически instant), mega-большие модели.
        # Лимит free: ~1M tokens/day. Доступные модели: qwen-3-235b, gpt-oss-120b.
        "name":  "cerebras",
        "env":   "CEREBRAS_API_KEY",
        "url":   "https://api.cerebras.ai/v1/chat/completions",
        "model": "qwen-3-235b-a22b-instruct-2507",
        "format": "openai",
    },
    {
        # Groq — быстрый, Llama 3.3 70B. Лимит 100K/day.
        "name":  "groq",
        "env":   "GROQ_API_KEY",
        "url":   "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
        "format": "openai",
    },
    {
        # SambaNova — щедрый free, Llama 3.3 70B.
        "name":  "sambanova",
        "env":   "SAMBANOVA_API_KEY",
        "url":   "https://api.sambanova.ai/v1/chat/completions",
        "model": "Meta-Llama-3.3-70B-Instruct",
        "format": "openai",
    },
    {
        # Mistral La Plateforme — free tier 1 RPS.
        "name":  "mistral",
        "env":   "MISTRAL_API_KEY",
        "url":   "https://api.mistral.ai/v1/chat/completions",
        "model": "mistral-large-latest",
        "format": "openai",
    },
    {
        # OpenRouter free model rotation — DeepSeek V4 flash отличный split.
        "name":  "openrouter",
        "env":   "OPENROUTER_API_KEY",
        "url":   "https://openrouter.ai/api/v1/chat/completions",
        "model": "deepseek/deepseek-v4-flash:free",
        "format": "openai",
    },
    {
        # Gemini 2.0 Flash — replaces deprecated gemini-1.5-flash (v50 fix)
        "name":  "gemini",
        "env":   "GEMINI_API_KEY",
        "url":   "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
        "model": "gemini-2.0-flash",
        "format": "gemini",
    },
    {
        # Claude Haiku — paid но из free credits, отличное качество.
        "name":  "anthropic",
        "env":   "ANTHROPIC_API_KEY",
        "url":   "https://api.anthropic.com/v1/messages",
        "model": "claude-haiku-4-5-20251001",
        "format": "anthropic",
    },
]


# ── Per-provider cool-down (when 429 hit, skip for 5 min) ────────────────
_PROVIDER_COOLDOWN: dict = {}
_COOLDOWN_SEC = 300

# v106.1: Anthropic out of credits — отключаем на 24h при импорте модуля,
# чтобы не долбить мёртвый ключ. Снимем после top-up.
import time as _time
_PROVIDER_COOLDOWN["anthropic"] = _time.time() + 86400


def _is_cooled_down(name: str) -> bool:
    """Returns True if provider hit rate limit recently and should be skipped."""
    until = _PROVIDER_COOLDOWN.get(name, 0)
    return time.time() < until


def _mark_cooldown(name: str, seconds: int = _COOLDOWN_SEC):
    _PROVIDER_COOLDOWN[name] = time.time() + seconds


def _call_openai_compat(provider: dict, prompt: str, max_tokens: int,
                        timeout: int) -> Optional[str]:
    """Standard OpenAI-compatible request."""
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
            return r.json()["choices"][0]["message"]["content"].strip()
        if r.status_code == 429:
            _mark_cooldown(provider["name"])
            print(f"[llm_chain] {provider['name']} 429 -> cooldown")
        elif r.status_code in (401, 403):
            # Invalid/expired key — disable provider for 24h
            _mark_cooldown(provider["name"], 86400)
            print(f"[llm_chain] {provider['name']} {r.status_code} (bad key) -> 24h skip")
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
        elif r.status_code >= 400:
            print(f"[llm_chain] gemini {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[llm_chain] gemini err: {e}")
    return None


def _call_anthropic(provider: dict, prompt: str, max_tokens: int,
                    timeout: int) -> Optional[str]:
    """Anthropic Claude format."""
    key = os.environ.get(provider["env"], "")
    if not key:
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
            return r.json()["content"][0]["text"].strip()
        if r.status_code == 429:
            _mark_cooldown(provider["name"])
            print(f"[llm_chain] anthropic 429 -> cooldown")
        elif r.status_code in (401, 403):
            _mark_cooldown(provider["name"], 86400)
            print(f"[llm_chain] anthropic {r.status_code} (bad key) -> 24h skip")
        elif r.status_code == 400 and "credit balance" in r.text.lower():
            # Out of credits — disable for 24h, user needs to top up
            _mark_cooldown(provider["name"], 86400)
            print(f"[llm_chain] anthropic OUT OF CREDIT -> 24h skip (top up needed)")
        elif r.status_code >= 400:
            print(f"[llm_chain] anthropic {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"[llm_chain] anthropic err: {e}")
    return None


def llm_call(prompt: str, max_tokens: int = 600, timeout: int = 15) -> Optional[str]:
    """Universal LLM call с fallback chain.
    Возвращает ответ от первого работающего провайдера или None если все
    в cooldown / без ключей."""
    for provider in PROVIDERS:
        if not os.environ.get(provider["env"]):
            continue
        if _is_cooled_down(provider["name"]):
            continue

        if provider["format"] == "openai":
            result = _call_openai_compat(provider, prompt, max_tokens, timeout)
        elif provider["format"] == "gemini":
            result = _call_gemini(provider, prompt, max_tokens, timeout)
        elif provider["format"] == "anthropic":
            result = _call_anthropic(provider, prompt, max_tokens, timeout)
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
        }
    return out


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(status(), indent=2))
    print()
    print("--- Test call ---")
    resp = llm_call("Say 'hello world' in one word.", max_tokens=10)
    print(f"Response: {resp!r}")
