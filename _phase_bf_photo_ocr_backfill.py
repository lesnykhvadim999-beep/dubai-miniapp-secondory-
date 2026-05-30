"""PHASE BF — backfill missing fields from listing photos via Gemini Vision.

Strategy:
  1. Pick listings with missing price OR area OR building, AND has images
  2. Run OCR on first image (most likely the main brochure)
  3. If confidence > 0.5 → apply only the missing fields
  4. Log results

Designed for: off-plan listings where text is sparse but brochure has data.
"""
from __future__ import annotations
import os
import sys
import psycopg2
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from photo_ocr import extract_from_image  # type: ignore

DB = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:REDACTED_DSN_PASSWORD"
    "@tramway.proxy.rlwy.net:23228/railway",
)

LIMIT = int(os.environ.get("LIMIT", "100"))
MIN_CONF = float(os.environ.get("MIN_CONF", "0.4"))


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main():
    c = psycopg2.connect(DB, connect_timeout=10)
    cur = c.cursor()

    cur.execute(f"""
        SELECT l.id, l.area, l.building, l.bedrooms, l.price, l.size_sqft,
               l.property_type, l.deal_type,
               (SELECT url FROM listing_images
                WHERE listing_id = l.id ORDER BY sort_order, id LIMIT 1) AS img_url
        FROM listings l
        WHERE l.v2_extracted_at IS NOT NULL AND l.is_active = TRUE
          AND (l.price IS NULL OR l.area IS NULL OR l.building IS NULL OR l.bedrooms IS NULL)
          AND EXISTS (SELECT 1 FROM listing_images WHERE listing_id = l.id)
        ORDER BY l.id DESC
        LIMIT {LIMIT}
    """)
    rows = cur.fetchall()
    log(f"Targets: {len(rows)} listings with image + missing fields")

    stats = {
        "ocr_attempted": 0,
        "ocr_returned": 0,
        "ocr_high_conf": 0,
        "price_filled": 0,
        "area_filled": 0,
        "building_filled": 0,
        "bedrooms_filled": 0,
        "sqft_filled": 0,
    }

    for i, r in enumerate(rows):
        (lid, area, bld, br, price, sqft, pt, deal, img_url) = r
        if not img_url:
            continue
        stats["ocr_attempted"] += 1
        result = extract_from_image(img_url)
        if not result:
            continue
        stats["ocr_returned"] += 1
        conf = float(result.get("confidence") or 0)
        if conf < MIN_CONF:
            continue
        stats["ocr_high_conf"] += 1

        upd = {}
        # Only fill missing fields
        if not price and result.get("price"):
            try:
                upd["price"] = int(result["price"])
                stats["price_filled"] += 1
            except Exception: pass
        if not area and result.get("area"):
            upd["area"] = str(result["area"])[:120]
            stats["area_filled"] += 1
        if not bld and result.get("building"):
            upd["building"] = str(result["building"])[:120]
            stats["building_filled"] += 1
        if br is None and result.get("bedrooms") is not None:
            try:
                upd["bedrooms"] = int(result["bedrooms"])
                stats["bedrooms_filled"] += 1
            except Exception: pass
        if not sqft and result.get("size_sqft"):
            try:
                upd["size_sqft"] = float(result["size_sqft"])
                stats["sqft_filled"] += 1
            except Exception: pass

        if upd:
            set_parts = ", ".join(f"{k}=%s" for k in upd.keys())
            params = list(upd.values()) + [lid]
            cur.execute(f"UPDATE listings SET {set_parts} WHERE id=%s", params)
            log(f"  id={lid} conf={conf:.2f} filled: {list(upd.keys())}")

        if (i + 1) % 10 == 0:
            c.commit()
            log(f"  [{i+1}/{len(rows)}] {stats}")
        # Rate limiting — Gemini free tier: 15 RPM
        time.sleep(4.5)

    c.commit()
    log("\n=== FINAL STATS ===")
    for k, v in stats.items():
        log(f"  {k}: {v}")
    c.close()


if __name__ == "__main__":
    main()
