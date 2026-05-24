# LLM Resilience v130 — гарантия ≥5 живых провайдеров

Что добавилось:

1. **Multi-key rotation** — каждый провайдер берёт 1–3 ключа из env (`*_KEY`, `*_KEY_2`, `*_KEY_3`). При 429 cooldown получает **только** конкретный ключ, не весь провайдер.
2. **Token-bucket rate-limiter** — сами держимся ниже RPM лимита, чтобы вообще не получать 429.
3. **Postgres LLM cache** (таблица `llm_cache`, TTL 7 дней) — повторные prompts не идут в API.
4. **Cloudflare Worker rotation** — `CF_WORKER_PROXY[_2,_3]`, случайный выбор для каждого запроса.
5. **Ollama self-hosted на Railway** — `llama3.2:3b`, безлимит, fallback на случай "все API лежат".
6. **Hourly health-check** в `staging-processor` → Telegram alert если <5 OK.
7. **Per-key hourly cap** 500 req/час (превентивная заморозка ключа).
8. **Anti-ban headers** — randomized User-Agent из пула SDK-строк.

## Новые env vars для Railway

Все опциональны — без них работает как раньше с одним ключом.

### Дополнительные ключи провайдеров (новые аккаунты)

| Провайдер | Доп. ключи | Где зарегистрировать (gmail+alias) |
|---|---|---|
| Cerebras | `CEREBRAS_API_KEY_2`, `_3` | https://cloud.cerebras.ai/ — sign up с `vadim+cb2@gmail.com`, `vadim+cb3@gmail.com` |
| Groq | `GROQ_API_KEY_2`, `_3` | https://console.groq.com/ — те же aliases |
| SambaNova | `SAMBANOVA_API_KEY_2`, `_3` | https://cloud.sambanova.ai/ |
| Mistral | `MISTRAL_API_KEY_2`, `_3` | https://console.mistral.ai/ |
| OpenRouter | `OPENROUTER_API_KEY_2`, `_3` | https://openrouter.ai/ |
| Gemini | `GEMINI_API_KEY_2`, `_3` | https://aistudio.google.com/apikey (нужен отдельный Google-акк) |
| Together | `TOGETHER_API_KEY_2`, `_3` | https://api.together.xyz/ |
| GitHub Models | `GITHUB_TOKEN_2`, `_3` | settings.github.com/personal-access-tokens (отд. GH-аккаунты с aliases) |
| Cohere | `COHERE_API_KEY_2`, `_3` | https://dashboard.cohere.com/ |

**Правило**: используй email с `+aliases` — все идут на основной inbox. Большинство сервисов их **принимают как уникальные** (Cerebras, Groq, Mistral, Together, OpenRouter, Cohere). Gemini и GitHub требуют разных Google/GH аккаунтов — там создавай реальные второстепенные.

### Cloudflare Workers (дополнительные)

```
CF_WORKER_PROXY    = https://llm-proxy.vadim-realty.workers.dev     (есть)
CF_WORKER_PROXY_2  = https://llm-proxy-2.<account-2>.workers.dev    (поднять)
CF_WORKER_PROXY_3  = https://llm-proxy-3.<account-3>.workers.dev    (поднять)
```

Инструкция поднять CF Worker #2:

```powershell
cd C:\Projects\cf-worker-llm-proxy
# Создать второй CF-аккаунт на vadim+cf2@gmail.com:
# https://dash.cloudflare.com/sign-up
# Подтвердить email из основного inbox, войти в Wrangler:
wrangler logout
wrangler login                       # выберет 2-й аккаунт в браузере
wrangler deploy --config wrangler-worker2.toml
# Скопировать URL из вывода → в Railway env CF_WORKER_PROXY_2
```

Для #3 — то же самое с `vadim+cf3@gmail.com` и `wrangler-worker3.toml`.

### Ollama (self-hosted, безлимит)

```
OLLAMA_URL    = http://ollama.railway.internal:11434/api/chat
OLLAMA_MODEL  = llama3.2:3b
```

Как поднять — см. `C:\Projects\ollama-service\README.md`. Одна команда:

```powershell
cd C:\Projects\ollama-service ; railway up
```

### Telegram alert при <5 OK провайдерах

```
ADMIN_BOT_TOKEN = <token любого админ-бота, hub-bot подойдёт>
ADMIN_CHAT_ID   = 353806371
```

Cron работает в `staging-processor`, раз в час: `health_check_all()` → если <5 OK → отправляет JSON отчёт в Telegram.

### Per-key hourly cap (опц.)

```
LLM_KEY_HOURLY_CAP = 500   # default
```

## Тест

```powershell
# Status (какие ключи настроены, какие в cooldown):
cd C:\Projects\resale-bot ; py llm_chain.py status

# Health (живой ping на каждый провайдер):
py llm_chain.py health
# Должно показать "*** N working providers ***"
```

## Очистка кэша

```powershell
# Каждый день можно вызывать (или поставить в Railway cron):
py C:\Projects\resale-bot\_llm_cache_cleanup.py
```

## Применено во ВСЕХ 5 проектах

- `resale-bot/llm_chain.py`
- `audit-queue-processor/llm_chain.py`
- `staging-processor/llm_chain.py` (+ health-check cron)
- `lead-bot/reelly-web-enricher/llm_chain.py`
- `channel-bot-new/Channel-Bot-new/telegram-bot/llm_chain.py`

Все скомпилированы (`py -m py_compile` OK).

## Что это даёт

- **Без дополнительных ключей**: backward-compat, работает как v129. Бесплатно.
- **С 2-3 ключами на провайдер**: ~3x запас на каждый провайдер до cooldown.
- **С Ollama**: всегда есть последний работающий провайдер (даже когда все API в дауне).
- **С Postgres cache**: повторные одинаковые prompts (типичный кейс — auditor пересматривает 1 listing) не жгут квоту.
- **Гарантия ≥5 OK**: если упадёт ниже — Telegram alert, и сразу видно что подкрутить.
