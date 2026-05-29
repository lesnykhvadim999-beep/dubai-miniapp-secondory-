"""
_check_saved_searches_alerts.py — #48 hourly cron for Saved Searches.

For each active saved_search:
  1. SELECT listings WHERE id > last_seen_max_id AND matches(filters) AND is_active.
  2. If matches > 0 → send_message to user (RESALE_BOT_TOKEN).
  3. UPDATE last_seen_max_id + last_alert_at.

Run from Railway cron (or simple loop):
    py _check_saved_searches_alerts.py
"""
import os
import sys
import json
import time
import requests

from db_schema import (
    get_conn,
    get_all_active_saved_searches,
    update_saved_search_seen,
)

BOT_TOKEN = (os.environ.get("RESALE_BOT_TOKEN")
             or os.environ.get("TELEGRAM_BOT_TOKEN", ""))
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

MAX_ITEMS_PER_ALERT = 5
SLEEP_BETWEEN_USERS = 0.4  # avoid Telegram flood


def _send(chat_id: int, text: str):
    if not BOT_TOKEN:
        print(f"[ss-cron] WARN no BOT_TOKEN; would send to {chat_id}: {text[:80]}")
        return
    try:
        r = requests.post(
            f"{API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if not r.ok:
            print(f"[ss-cron] send fail uid={chat_id}: {r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"[ss-cron] send err uid={chat_id}: {e}")


def _build_where(filters: dict):
    """Compile filters dict → SQL WHERE clause + params list.
    Supports keys: deal_type, area, building, bedrooms (exact or br_min/br_max),
    price_min/price_max OR min_price/max_price, property_type, emirate."""
    where = ["is_active = TRUE",
             "(is_audit IS NULL OR is_audit = FALSE)",
             "(is_frozen IS NULL OR is_frozen = FALSE)"]
    params = []

    if filters.get("deal_type"):
        where.append("deal_type = %s")
        params.append(filters["deal_type"])
    if filters.get("emirate"):
        where.append("LOWER(emirate) = LOWER(%s)")
        params.append(filters["emirate"])
    if filters.get("area"):
        where.append("LOWER(area) = LOWER(%s)")
        params.append(filters["area"])
    if filters.get("building"):
        where.append("LOWER(building) = LOWER(%s)")
        params.append(filters["building"])
    if filters.get("property_type"):
        where.append("LOWER(property_type) = LOWER(%s)")
        params.append(filters["property_type"])

    br = filters.get("bedrooms")
    br_min = filters.get("br_min")
    br_max = filters.get("br_max")
    if br is not None:
        where.append("bedrooms = %s")
        params.append(int(br))
    else:
        if br_min is not None:
            where.append("bedrooms >= %s")
            params.append(int(br_min))
        if br_max is not None:
            where.append("bedrooms <= %s")
            params.append(int(br_max))

    pmin = filters.get("min_price") or filters.get("price_min")
    pmax = filters.get("max_price") or filters.get("price_max")
    if pmin is not None:
        where.append("price >= %s")
        params.append(int(pmin))
    if pmax is not None:
        where.append("price <= %s")
        params.append(int(pmax))

    return " AND ".join(where), params


def _fetch_new_matches(conn, filters: dict, last_seen_max_id: int, limit: int = 50):
    where_sql, params = _build_where(filters)
    sql = f"""
        SELECT id, area, building, bedrooms, price, deal_type, property_type
          FROM listings
         WHERE id > %s AND {where_sql}
         ORDER BY id ASC
         LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, [int(last_seen_max_id), *params, limit])
        return list(cur.fetchall()), cur


def _format_alert(name: str, matches: list, total: int) -> str:
    lines = [f"🔔 *Новые объекты по «{name}»*", ""]
    for i, m in enumerate(matches[:MAX_ITEMS_PER_ALERT], 1):
        area = m.get("area") or "—"
        bld  = m.get("building") or ""
        br   = m.get("bedrooms")
        price = m.get("price") or 0
        br_str = f"{br}BR" if (br is not None and br > 0) else ("Studio" if br == 0 else "—")
        price_str = (f"{price/1_000_000:.2f}M AED" if price >= 1_000_000
                     else f"{price:,} AED" if price else "—")
        head = f"*{i}.* {area}"
        if bld:
            head += f" · {bld}"
        head += f" · {br_str}"
        lines.append(head)
        lines.append(f"   💰 {price_str}    /detail_{m['id']}")
    if total > MAX_ITEMS_PER_ALERT:
        lines.append("")
        lines.append(f"_…and {total - MAX_ITEMS_PER_ALERT} more. Open /my_alerts to manage._")
    return "\n".join(lines)


def main():
    print(f"[ss-cron] start  has_token={bool(BOT_TOKEN)}", flush=True)
    rows = get_all_active_saved_searches()
    print(f"[ss-cron] active subscriptions: {len(rows)}", flush=True)
    sent, errs = 0, 0
    conn = get_conn()
    try:
        for r in rows:
            try:
                filters = r.get("filters") or {}
                if isinstance(filters, str):
                    filters = json.loads(filters)
                last_seen = int(r.get("last_seen_max_id") or 0)
                matches, _ = _fetch_new_matches(conn, filters, last_seen)
                if not matches:
                    continue
                new_max = max(m["id"] for m in matches)
                # Re-query COUNT for accurate "total" if limited
                where_sql, params = _build_where(filters)
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT COUNT(*) AS c FROM listings WHERE id > %s AND {where_sql}",
                        [last_seen, *params])
                    row_cnt = cur.fetchone()
                    total = int(row_cnt["c"] if isinstance(row_cnt, dict) else row_cnt[0])
                text = _format_alert(r.get("name") or "Saved search", matches, total)
                _send(r["user_id"], text)
                update_saved_search_seen(r["id"], new_max)
                sent += 1
                time.sleep(SLEEP_BETWEEN_USERS)
            except Exception as e:
                errs += 1
                print(f"[ss-cron] err ss_id={r.get('id')}: {e}", flush=True)
    finally:
        conn.close()
    print(f"[ss-cron] done  sent={sent} errs={errs}", flush=True)


if __name__ == "__main__":
    main()
