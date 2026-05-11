"""
rescan_no_ai.py — Пересканирование базы БЕЗ AI, только rule-based парсер.
Запуск: python3 rescan_no_ai.py

Что делает:
- Берёт все активные объявления с original_text
- Прогоняет через parse_message() (regex/словари, без API)
- Обновляет: price, deal_type, building, area, emirate,
  bedrooms, size_sqft, view, floor, status, furnishing
- Не трогает поля которые уже заполнены (только пустые + цену)
- Коммитит каждые 100 записей
"""
import sys, os, time
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone

sys.path.insert(0, "/home/runner/workspace")
from parser_engine import parse_message, validate_deal_type_by_price

DB_URL = os.environ.get(
    "DATABASE_URL",
    "REDACTED_DSN_USE_DATABASE_URL_ENV",
)

def rescan_no_ai():
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("""
        SELECT id, original_text, price, deal_type, building, area,
               emirate, bedrooms, size_sqft, view, floor, status, furnishing,
               telegram_message_id, telegram_chat_id, message_date
        FROM listings
        WHERE original_text IS NOT NULL
          AND original_text != ''
          AND is_active = TRUE
        ORDER BY id
    """)
    rows = cur.fetchall()
    total = len(rows)
    fixed = skipped = errors = 0

    print(f"[RESCAN] Starting rule-based scan of {total} listings...")

    for i, row in enumerate(rows):
        if i % 200 == 0:
            print(f"[RESCAN] {i}/{total} | fixed={fixed} skipped={skipped} err={errors}")

        try:
            parsed = parse_message(
                text=row["original_text"],
                message_id=row.get("telegram_message_id") or 0,
                message_date=row.get("message_date") or datetime.now(timezone.utc),
                chat_id=row.get("telegram_chat_id") or "0",
            )
        except Exception as e:
            errors += 1
            continue

        if not parsed:
            skipped += 1
            continue

        updates = {}

        # ── Price: always update if parser found something ─────────────────
        new_price = parsed.get("price")
        if new_price and new_price > 0:
            if not row["price"] or row["price"] == 0:
                updates["price"] = new_price
            # Also fix obviously wrong prices (e.g. 500000 stored but 1850000 in text)
            elif row["price"] != new_price:
                # Trust new parser if it's significantly different
                old_p = row["price"]
                if old_p and new_price and abs(new_price - old_p) / max(old_p, 1) > 0.05:
                    updates["price"] = new_price

        # ── deal_type: revalidate with new price ───────────────────────────
        final_price = updates.get("price") or row.get("price") or 0
        new_deal = parsed.get("deal_type") or row.get("deal_type", "sale")
        validated_deal = validate_deal_type_by_price(
            final_price, new_deal,
            bedrooms=parsed.get("bedrooms") or row.get("bedrooms"),
            area=parsed.get("area") or row.get("area"),
            text=row["original_text"],
        )
        if validated_deal != row.get("deal_type"):
            updates["deal_type"] = validated_deal

        # ── Fill empty fields only ─────────────────────────────────────────
        if not row["building"] and parsed.get("building"):
            updates["building"] = parsed["building"]

        if not row["area"] and parsed.get("area"):
            updates["area"] = parsed["area"]

        if not row["emirate"] and parsed.get("emirate"):
            updates["emirate"] = parsed["emirate"]

        if row["bedrooms"] is None and parsed.get("bedrooms") is not None:
            updates["bedrooms"] = parsed["bedrooms"]

        if not row["size_sqft"] and parsed.get("size_sqft"):
            sqft = parsed["size_sqft"]
            fp = final_price or 0
            # Validate: price per sqft sanity check
            if 50 <= sqft <= 50000:
                ppsf = fp / sqft if (fp > 0 and sqft > 0) else 0
                if ppsf == 0 or ppsf < 100_000:
                    updates["size_sqft"] = sqft

        if not row["view"] and parsed.get("view"):
            updates["view"] = parsed["view"]

        if row["floor"] is None and parsed.get("floor") is not None:
            updates["floor"] = parsed["floor"]

        if not row["status"] and parsed.get("status"):
            updates["status"] = parsed["status"]

        if not row["furnishing"] and parsed.get("furnishing"):
            updates["furnishing"] = parsed["furnishing"]

        # ── Recalculate price_per_sqft ─────────────────────────────────────
        final_sqft = updates.get("size_sqft") or row.get("size_sqft")
        fp2 = updates.get("price") or row.get("price") or 0
        if final_sqft and fp2 and final_sqft > 0:
            updates["price_per_sqft"] = round(fp2 / final_sqft, 0)

        if not updates:
            skipped += 1
            continue

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
            errors += 1
            continue

        if (i + 1) % 100 == 0:
            conn.commit()
            print(f"[RESCAN] Committed at {i+1}")

    conn.commit()

    print(f"""
[RESCAN DONE]
Total     : {total}
Fixed     : {fixed}
Skipped   : {skipped}
Errors    : {errors}
""")

    # Summary
    cur.execute("""
        SELECT deal_type,
               COUNT(*) as cnt,
               COUNT(price) as has_price,
               AVG(price)::bigint as avg_price,
               COUNT(building) as has_bld,
               COUNT(area) as has_area,
               COUNT(size_sqft) as has_sqft
        FROM listings
        WHERE is_active = TRUE
        GROUP BY deal_type ORDER BY cnt DESC
    """)
    print(f"{'deal_type':<8} {'cnt':>6} {'has_price':>10} {'avg_price':>12} {'bld':>6} {'area':>6} {'sqft':>6}")
    print("-" * 60)
    for r in cur.fetchall():
        print(f"{(r['deal_type'] or '?'):<8} {r['cnt']:>6} {r['has_price']:>10} "
              f"{(r['avg_price'] or 0):>12,} {r['has_bld']:>6} {r['has_area']:>6} {r['has_sqft']:>6}")

    conn.close()

if __name__ == "__main__":
    rescan_no_ai()
