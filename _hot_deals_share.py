"""Hot Deals auto-share — поиск свежих горячих сделок и отправка в @vadim_admin_bot.

Логика:
  1. SELECT FROM listings WHERE is_active AND is_hot_deal AND confidence_score >= 0.9
     AND created_at > NOW() - INTERVAL '24 hours' AND NOT alerted.
  2. Для каждой строки — карточка + inline кнопки [Опубликовать в @flipluxproperty / Скип].
  3. UPDATE listings SET alerted=true WHERE id=X.

Запуск:
  py -3 _hot_deals_share.py            — реальный режим
  py -3 _hot_deals_share.py --dry      — только LIMIT 1, без UPDATE alerted (для проверки)

Колбэки обрабатывает _hot_deals_callback_handler.py.
"""
from __future__ import annotations
import os
import sys
import argparse
import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from admin_notify import admin_notify  # type: ignore

DB_URL = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set")),
)

# admin bot env: admin_notify читает ADMIN_BOT_TOKEN из окружения.
# Токен НЕ хардкодим — задаётся через Railway/env переменную ADMIN_BOT_TOKEN.

SELECT_SQL = """
    SELECT id, building, area, bedrooms, price, size_sqft, view,
           confidence_score, price_vs_market_percent
    FROM listings
    WHERE is_active = TRUE
      AND is_hot_deal = TRUE
      AND confidence_score >= 0.9
      AND created_at > NOW() - INTERVAL '24 hours'
      AND (alerted IS NULL OR alerted = FALSE)
    ORDER BY confidence_score DESC, created_at DESC
    {limit_clause}
"""


def format_card(row: dict) -> str:
    price_m = (row["price"] or 0) / 1_000_000.0
    pct = row.get("price_vs_market_percent")
    pct_str = f"{pct:+.1f}%" if pct is not None else "n/a"
    br = row.get("bedrooms")
    br_str = f"{br}BR" if br not in (None, 0) else "Studio"
    sqft = row.get("size_sqft")
    sqft_str = f"{sqft} sqft" if sqft else "?"
    view = row.get("view") or "—"
    conf = row.get("confidence_score") or 0.0
    return (
        "🔥 <b>Hot deal!</b>\n"
        f"{row.get('area') or '?'} • {row.get('building') or '?'}\n"
        f"{br_str} • {sqft_str} • {view}\n"
        f"💰 <b>{price_m:.2f}M AED</b> ({pct_str} vs market)\n\n"
        f"Listing #{row['id']} • confidence {conf:.2f}"
    )


def fetch_hot_deals(dry: bool):
    limit_clause = "LIMIT 1" if dry else "LIMIT 20"
    sql = SELECT_SQL.format(limit_clause=limit_clause)
    with psycopg2.connect(DB_URL) as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return rows


def mark_alerted(listing_id: int) -> None:
    with psycopg2.connect(DB_URL) as conn, conn.cursor() as cur:
        cur.execute("UPDATE listings SET alerted = TRUE WHERE id = %s", (listing_id,))
        conn.commit()


def buttons_for(listing_id: int) -> list[list[dict]]:
    return [[
        {"text": "🚀 Опубликовать в @flipluxproperty", "callback_data": f"share:{listing_id}"},
        {"text": "🚫 Скип", "callback_data": f"skip:{listing_id}"},
    ]]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true",
                   help="LIMIT 1, не помечать alerted (тест карточки)")
    args = p.parse_args()

    rows = fetch_hot_deals(dry=args.dry)
    if not rows:
        print("[hot_deals_share] нет новых hot deals", flush=True)
        return 0

    sent = 0
    for row in rows:
        card = format_card(row)
        ok = admin_notify(card, buttons=buttons_for(row["id"]))
        status = "OK" if ok else "FAIL"
        print(f"[hot_deals_share] listing #{row['id']} → admin: {status}", flush=True)
        if ok and not args.dry:
            mark_alerted(row["id"])
            sent += 1

    print(f"[hot_deals_share] done. sent={sent} dry={args.dry}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
