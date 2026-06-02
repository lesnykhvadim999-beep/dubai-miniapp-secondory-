# resale-bot — Dubai resale listings parser + Telegram bot

Production Telegram bot that scrapes Dubai real-estate channels via
Telethon, parses listings with `parser_v2`, deduplicates against
`listings_v2`, and serves a search wizard to end users.

Deployed to Railway (`exemplary-compassion` / `resale-bot`).

## Self-improving parser (active 2026-06-03)

`parse_message_v2()` now calls `parser_self_improve.failed_collector.maybe_log_failure()`
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

## Known dead code (TODO — do not remove without review)

- `dld_analytics.py` in `channel-bot-new` — helper module flagged by
  dead-code scanner; verify no Railway cron references before delete.

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
