"""
rescan_with_ai.py — Пересканирование всей базы через Claude Haiku AI.
Запуск: python3 rescan_with_ai.py

Что делает:
- Берёт все активные объявления с original_text
- Прогоняет каждое через ai_parse_listing()
- Обновляет deal_type, building, area, bedrooms, price, status, furnishing, view, floor
- Удаляет спам-объявления (is_spam=True)
- Логирует стоимость каждые 500 вызовов
- Коммитит каждые 50 записей
"""
import sys
import time
import psycopg2
from psycopg2.extras import RealDictCursor

# Must be run from project root so parser_engine is importable
sys.path.insert(0, "/home/runner/workspace")
from parser_engine import ai_parse_listing, _ai_stats

import os
DB_URL = os.environ.get(
    "DATABASE_URL",
    "REDACTED_DSN_USE_DATABASE_URL_ENV",
)


def rescan_all_with_ai():
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("""
        SELECT id, original_text, price, deal_type, building, area, bedrooms
        FROM listings
        WHERE original_text IS NOT NULL
          AND original_text != ''
          AND is_active = TRUE
        ORDER BY id
    """)
    rows = cur.fetchall()

    total       = len(rows)
    fixed       = 0
    spam_deleted = 0
    errors      = 0

    print(f"[RESCAN] Starting AI scan of {total} listings...")

    for i, row in enumerate(rows):
        if i % 100 == 0:
            print(f"[RESCAN] {i}/{total} processed | fixed={fixed} spam={spam_deleted} err={errors}")

        result = ai_parse_listing(row["original_text"])

        if result is None:
            errors += 1
            continue

        # Delete spam
        if result.get("is_spam"):
            try:
                cur.execute("DELETE FROM leads WHERE listing_id = %s", (row["id"],))
                cur.execute("DELETE FROM listings WHERE id = %s", (row["id"],))
                spam_deleted += 1
            except Exception as e:
                print(f"[RESCAN] Delete error id={row['id']}: {e}")
                conn.rollback()
            continue

        # Collect fields to update
        updates: dict = {}

        if result.get("deal_type") in ("sale", "rent") and result["deal_type"] != row["deal_type"]:
            updates["deal_type"] = result["deal_type"]

        if result.get("building") and not row["building"]:
            updates["building"] = result["building"]

        if result.get("district") and not row["area"]:
            updates["area"] = result["district"]

        if result.get("bedrooms") is not None and row["bedrooms"] is None:
            updates["bedrooms"] = result["bedrooms"]

        if result.get("price") and not row["price"]:
            updates["price"] = result["price"]

        if result.get("status"):
            updates["status"] = result["status"]

        if result.get("furnishing"):
            updates["furnishing"] = result["furnishing"]

        if result.get("view"):
            updates["view"] = result["view"]

        if result.get("floor") is not None:
            updates["floor"] = result["floor"]

        # ── Жёсткая валидация перед записью в БД ──────────────────────────
        final_price = updates.get("price") or row.get("price") or 0
        final_deal  = updates.get("deal_type") or row.get("deal_type")
        if final_price > 0:
            if final_deal == "sale" and final_price < 500_000:
                updates["deal_type"] = "rent"
            elif final_deal == "rent" and final_price > 30_000_000:
                updates["deal_type"] = "sale"

        # Валидация size_sqft — не сохраняем битые данные
        sqft = result.get("area_sqft")
        if sqft is not None:
            if 50 <= sqft <= 50000:
                ppsf = (final_price / sqft) if (final_price and sqft > 0) else 0
                if ppsf == 0 or ppsf < 100_000:
                    updates["size_sqft"] = sqft
            # иначе sqft < 50 или > 50000 или цена/sqft абсурдная — игнорируем
        # ───────────────────────────────────────────────────────────────────

        if updates:
            set_clause = ", ".join(f"{k}=%s" for k in updates)
            try:
                cur.execute(
                    f"UPDATE listings SET {set_clause} WHERE id=%s",
                    list(updates.values()) + [row["id"]],
                )
                fixed += 1
            except Exception as e:
                print(f"[RESCAN] Update error id={row['id']}: {e}")
                conn.rollback()
                continue

        # Commit every 50 rows
        if (i + 1) % 50 == 0:
            conn.commit()

        # Small pause to avoid rate limits
        time.sleep(0.05)

    conn.commit()

    # Final stats
    cost = (
        _ai_stats["input_tokens"] * 0.0000008
        + _ai_stats["output_tokens"] * 0.0000004
    )
    print(f"""
[RESCAN DONE]
Total processed : {total}
Fixed           : {fixed}
Spam deleted    : {spam_deleted}
Errors          : {errors}
AI calls        : {_ai_stats['calls']}
Total tokens    : {_ai_stats['input_tokens'] + _ai_stats['output_tokens']:,}
Estimated cost  : ${cost:.4f}
""")

    # Final DB summary
    cur.execute("""
        SELECT deal_type,
               COUNT(*) as total,
               MIN(price)::bigint as min_price,
               MAX(price)::bigint as max_price,
               AVG(price)::bigint as avg_price,
               COUNT(building) as has_building,
               COUNT(area) as has_area
        FROM listings
        WHERE price > 0
        GROUP BY deal_type
        ORDER BY total DESC
    """)
    print(f"{'deal_type':<8} {'total':>6} {'avg_price':>12} {'min':>10} {'max':>12} {'building':>8} {'area':>6}")
    print("-" * 68)
    for r in cur.fetchall():
        print(
            f"{r['deal_type']:<8} {r['total']:>6} "
            f"{r['avg_price']:>12,} {r['min_price']:>10,} {r['max_price']:>12,} "
            f"{r['has_building']:>8} {r['has_area']:>6}"
        )

    conn.close()


if __name__ == "__main__":
    rescan_all_with_ai()
