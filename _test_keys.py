"""Quick test of all 4 new LLM provider keys."""
import os, time, sys, io
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
except Exception:
    pass

sys.path.insert(0, '.')
import requests

PROVIDERS = [
    ("cerebras",   "https://api.cerebras.ai/v1/chat/completions",       "qwen-3-235b-a22b-instruct-2507", "openai"),
    ("groq",       "https://api.groq.com/openai/v1/chat/completions",   "llama-3.3-70b-versatile",       "openai"),
    ("sambanova",  "https://api.sambanova.ai/v1/chat/completions",      "Meta-Llama-3.3-70B-Instruct",   "openai"),
    ("mistral",    "https://api.mistral.ai/v1/chat/completions",        "mistral-large-latest",          "openai"),
    ("openrouter", "https://openrouter.ai/api/v1/chat/completions",     "deepseek/deepseek-v4-flash:free", "openai"),
    ("gemini",     "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent", "gemini-2.0-flash", "gemini"),
    ("anthropic",  "https://api.anthropic.com/v1/messages",             "claude-haiku-4-5-20251001",     "anthropic"),
]
ENV_MAP = {
    "cerebras":  "CEREBRAS_API_KEY",
    "groq":      "GROQ_API_KEY",
    "sambanova": "SAMBANOVA_API_KEY",
    "mistral":   "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini":    "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

for name, url, model, fmt in PROVIDERS:
    env = ENV_MAP[name]
    key = os.environ.get(env, "")
    if not key:
        print(f"{name:12s} skip (no key)")
        continue
    t = time.time()
    try:
        if fmt == "openai":
            r = requests.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json={"model": model, "max_tokens": 10, "messages": [{"role": "user", "content": "Reply OK"}]}, timeout=15)
        elif fmt == "gemini":
            r = requests.post(f"{url}?key={key}", headers={"Content-Type": "application/json"}, json={"contents": [{"parts": [{"text": "Reply OK"}]}], "generationConfig": {"maxOutputTokens": 10}}, timeout=15)
        elif fmt == "anthropic":
            r = requests.post(url, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={"model": model, "max_tokens": 10, "messages": [{"role": "user", "content": "Reply OK"}]}, timeout=15)
        else:
            print(f"{name:12s} unknown format")
            continue
        sc = r.status_code
        dur = f"{time.time()-t:.1f}s"
        if sc == 200:
            print(f"{name:12s} OK ({dur})")
        else:
            text_short = r.text.replace("\n", " ")[:130]
            print(f"{name:12s} HTTP {sc} ({dur}) — {text_short}")
    except Exception as e:
        print(f"{name:12s} err: {e}")
