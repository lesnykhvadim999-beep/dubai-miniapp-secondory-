# resale-bot — Dubai resale listings parser + Telegram bot

Production Telegram bot that scrapes Dubai real-estate channels via
Telethon, parses listings with `parser_v2`, deduplicates against
`listings`, and serves a search wizard to end users.

Deployed to Railway (`exemplary-compassion` / `resale-bot`).
Telegram: [@dubai_resale_fpr_bot](https://t.me/dubai_resale_fpr_bot)

## Self-improving parser (active 2026-06-03)

`parse_message_v2()` calls `parser_self_improve.failed_collector.maybe_log_failure()`
on every parse. Listings with confidence < 0.7 or NULL critical fields
land in `parser_failed_log`, where:

- **Mon 03:00** cron `discover_patterns.py` clusters failures and proposes
  regex patches via LLM.
- **Wed 04:00** cron `confidence_calibration.py` re-parses borderline
  listings (0.5 ≤ conf ≤ 0.7) with current parser and lifts confidence
  if re-parse beats threshold without field drift.

Expected: 10-50 new failures logged per day. First Mon pattern
suggestion arrives in admin bot next Monday.

## Deferred toggles (review 2026-06-10)

The following hardening flags are intentionally OFF during a one-week
observation period:

| Flag | Default | Effect when ON | Re-evaluate |
|------|---------|----------------|------------|
| `STRICT_CONTRACTS` | OFF | Raise instead of warn on DB schema drift | 2026-06-10 |
| `STRICT_I18N` | OFF | Raise on missing translation keys (currently fallback to EN) | 2026-06-10 |
| `PLAINTEXT_PHONE_NULLIFY` | OFF (lead-bot) | Replace raw phone with masked form in DB | 2026-06-10 |

To enable, set the env var to `1` in Railway → Variables → redeploy.

## Local dev

```powershell
$env:RESALE_DATABASE_URL = "postgresql://..."
$env:BOT_TOKEN = "..."
python resale_bot.py
```

## Tests

```powershell
python -m pytest tests/
```

(Requires DLD_DB_URL stub — see `tests/conftest.py`.)

## Audit 2026-06-05 (cleanup pass)

Полный аудит выявил и устранил:
- NameError `user_languages` → `user_lang` в format_detail/get_market_summary
- Markdown-injection в admin/lead уведомлениях (добавлен `_md_esc()` helper)
- Отсутствие 4096-char guard в `_send`/`_edit` (теперь `_trim_tg()`)
- IndexError/ValueError protection в detail-callback
- TRUNCATE `listings_staging` (1.1 GB → освобождено)
- DELETE 2496 дубликатов в `listings` + UNIQUE INDEX `uq_listings_telegram_msg_chat`
- Pin `dre-sdk` на commit hash (supply-chain)
- `digest_loop` / `hourly_report_loop` — не дублируют отправку при рестарте
- `hourly_report_loop` — exponential backoff (защита от спама админу при PG-down)
- Чистка CLAUDE.md (Railway-токен убран в env)

Полный список багов — см. CHANGELOG.md и memory bug_knowledge_base.
