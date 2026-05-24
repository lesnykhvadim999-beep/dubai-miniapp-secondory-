# Performance Rules — resale-bot

## Hot path SLA

| Action | Target | Hard limit |
|---|---|---|
| AI Recommend (5 areas × 3 listings) | <300ms | 500ms |
| Free-text search (parse_nl + listings) | <400ms | 1s |
| Single listing card render | <100ms | 200ms |
| `/start` welcome | <150ms | 300ms |

## Rules

1. **Read-model only in hot path.**
   - `area_stats`, `building_stats`, `mv_area_12m_summary`, `mv_building_12m_summary`,
     `market_overview` — это analytics-DB (через `dxb_stats_client`).
   - Listings table — собственная resale-DB, чистые SELECT по индексам.
   - **Запрещено** JOIN на `dld_sales_unified` / `dld_rentals_unified` / raw DLD в любом
     handler'е бота. Эти таблицы только для analytics-bot.

2. **LLM async only.**
   - LLM-вызов **запрещён** в синхронном flow handler'а если он влияет на отображение.
   - Допустимо: heuristic-результат сразу + Claude follow-up отдельным сообщением в threading.Thread.
   - `parse_nl` использует Claude ТОЛЬКО если regex не извлёк никакие структурированные
     поля (area/deal_type/max_price/bedrooms/view). Timeout 4s, max_tokens=400.

3. **Parallel SQL.**
   - Многие area-запросы (`run_ai_recommend`) идут через `ThreadPoolExecutor(max_workers=5)`.
   - DB connection pool psycopg2 — 20 connections.

4. **Cache.**
   - `_DLD_ANALYTICS_CACHE` LRU 200 entries, TTL 1h.
   - LLM responses кэшируются в `llm_cache` Postgres table (через `llm_chain.py` v130).

## Hot-path SQL whitelist

Эти таблицы можно читать в hot path:
- `listings` (own data)
- `area_stats` / `building_stats` (read-model)
- `mv_area_12m_summary` / `mv_building_12m_summary`
- `market_data` (legacy seed, fallback only)
- `_auth_kv` (config)
- `favorites`, `users` (own data, indexed)

## Benchmark (v131, May 2026)

| Operation | Before | After |
|---|---|---|
| `run_ai_recommend` SQL stage | ~400ms (5x serial) | ~80-120ms (parallel) |
| `parse_nl` worst case | ~3-8s (Claude timeout 15s) | ~50-150ms (skip Claude if regex hit) |
| `get_market_summary` | ~80ms | ~50ms (read-model) |
| `get_best_areas_from_db` | ~250ms (legacy market_data) | ~80ms (mv_area_12m_summary) |
