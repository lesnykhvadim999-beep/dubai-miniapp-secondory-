# -*- coding: utf-8 -*-
"""
_cross_source_verification.py — Cross-source duplicate verification.

Находит дубликаты listings из разных каналов и выбирает лучшую версию.

Strategy:
  1) Group by photo_phash (top priority, IS NOT NULL).
  2) Group by (area, building, bedrooms, round(price/100k)) — fuzzy combo.

Для каждой группы > 1 records:
  - Winner = max(confidence_score, completeness, recency) по score-функции.
  - Лузеры: is_active=FALSE, review_reason='dup_of:{winner_id}'.
  - Merge: если у winner поле NULL, а у дубля есть — копируем в winner.
  - Защита: не трогаем listings, на которые есть leads (SELECT 1 FROM leads).

Idempotent. Safe to run hourly via cron or после batch v2 parser inserts.

Run:
  py _cross_source_verification.py            # live mode
  py _cross_source_verification.py --dry-run  # show top-10 groups, no writes
"""
from __future__ import annotations
import os
import sys
import argparse
from typing import Any

sys.stdout.reconfigure(encoding='utf-8')

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://postgres:REDACTED_DSN_PASSWORD"
    "@tramway.proxy.rlwy.net:23228/railway"
)

import psycopg2
from psycopg2.extras import RealDictCursor

try:
    from admin_notify import admin_notify
except Exception:
    def admin_notify(text: str, **kw):
        print(f"[admin_notify-stub] {text}")
        return False

DB_URL = os.environ["DATABASE_URL"]

# Поля для merge: если у winner NULL и у дубля есть — копируем.
MERGEABLE_FIELDS = [
    "area", "building", "bedrooms", "bathrooms",
    "size_sqft", "bua_sqft", "plot_sqft", "floor", "unit_number",
    "view", "furnishing", "status",
    "price", "original_price", "selling_price", "price_per_sqft",
    "agent_name", "phone", "whatsapp",
    "cover_image_url", "cover_image_tg_id",
    "description", "is_off_plan", "handover_date",
    "building_tier", "deal_quality", "is_hot_deal",
]

# Поля для подсчёта completeness
COMPLETENESS_FIELDS = [
    "area", "building", "bedrooms", "size_sqft", "price",
    "agent_name", "phone", "cover_image_url", "view", "furnishing",
    "floor", "unit_number", "description",
]


def listing_score(row: dict) -> float:
    """Чем больше — тем лучше. Учитывает confidence + completeness + recency."""
    conf = float(row.get("confidence_score") or 0.0)
    completeness = sum(1 for f in COMPLETENESS_FIELDS if row.get(f) not in (None, "", 0))
    # recency boost — свежее лучше (created_at в днях от epoch / 1000)
    created = row.get("created_at")
    rec_boost = 0.0
    if created is not None:
        try:
            rec_boost = created.timestamp() / 86400.0 / 365.0  # ~годы
        except Exception:
            rec_boost = 0.0
    return conf * 10.0 + completeness * 2.0 + rec_boost


def has_leads(cur, listing_id: int) -> bool:
    cur.execute("SELECT 1 FROM leads WHERE listing_id = %s LIMIT 1", (listing_id,))
    return cur.fetchone() is not None


def fetch_group_rows(cur, ids: list[int]) -> list[dict]:
    cur.execute(
        "SELECT * FROM listings WHERE id = ANY(%s)",
        (ids,)
    )
    return [dict(r) for r in cur.fetchall()]


def process_group(cur, rows: list[dict], dry_run: bool, group_key: str) -> dict:
    """Pick winner, deactivate losers, merge fields. Returns stats."""
    stats = {"deactivated": 0, "merged_fields": 0, "skipped_leads": 0, "winner": None}
    if len(rows) < 2:
        return stats

    # winner — max score
    rows_sorted = sorted(rows, key=listing_score, reverse=True)
    winner = rows_sorted[0]
    losers = rows_sorted[1:]
    stats["winner"] = winner["id"]

    # Merge: для каждого NULL-поля winner ищем не-NULL у дубля (по приоритету score)
    merge_updates: dict[str, Any] = {}
    for f in MERGEABLE_FIELDS:
        if winner.get(f) not in (None, "", 0):
            continue
        for cand in losers:
            v = cand.get(f)
            if v not in (None, "", 0):
                merge_updates[f] = v
                break

    if merge_updates and not dry_run:
        set_clause = ", ".join(f"{k} = %s" for k in merge_updates)
        params = list(merge_updates.values()) + [winner["id"]]
        cur.execute(
            f"UPDATE listings SET {set_clause}, updated_at = NOW() WHERE id = %s",
            params
        )
        stats["merged_fields"] = len(merge_updates)
        admin_notify(
            f"🔀 Merge dupes [{group_key}]\n"
            f"winner id={winner['id']} ← {len(merge_updates)} field(s): "
            f"{', '.join(merge_updates.keys())[:200]}",
            priority="info"
        )
    elif merge_updates:
        stats["merged_fields"] = len(merge_updates)

    # Deactivate losers
    for loser in losers:
        if has_leads(cur, loser["id"]):
            stats["skipped_leads"] += 1
            continue
        if not dry_run:
            cur.execute(
                "UPDATE listings SET is_active = FALSE, "
                "review_reason = %s, updated_at = NOW() "
                "WHERE id = %s AND is_active = TRUE",
                (f"dup_of:{winner['id']}", loser["id"])
            )
            if cur.rowcount > 0:
                stats["deactivated"] += 1
        else:
            stats["deactivated"] += 1

    return stats


def find_phash_groups(cur) -> list[dict]:
    cur.execute("""
        SELECT photo_phash AS k, ARRAY_AGG(id ORDER BY id) AS ids, COUNT(*) AS cnt
        FROM listings
        WHERE is_active = TRUE
          AND photo_phash IS NOT NULL
          AND photo_phash != ''
        GROUP BY photo_phash
        HAVING COUNT(*) > 1
        ORDER BY COUNT(*) DESC
    """)
    return [dict(r) for r in cur.fetchall()]


def find_combo_groups(cur, exclude_ids: set[int]) -> list[dict]:
    """Group by (area, building, bedrooms, round(price/100k))."""
    cur.execute("""
        SELECT
            LOWER(TRIM(area)) AS area_k,
            LOWER(TRIM(building)) AS building_k,
            bedrooms,
            ROUND(price / 100000.0) AS price_bucket,
            ARRAY_AGG(id ORDER BY id) AS ids,
            COUNT(*) AS cnt
        FROM listings
        WHERE is_active = TRUE
          AND area IS NOT NULL AND area != ''
          AND building IS NOT NULL AND building != ''
          AND bedrooms IS NOT NULL
          AND price IS NOT NULL AND price > 0
        GROUP BY 1, 2, 3, 4
        HAVING COUNT(*) > 1
        ORDER BY COUNT(*) DESC
    """)
    out = []
    for r in cur.fetchall():
        ids = [i for i in r["ids"] if i not in exclude_ids]
        if len(ids) < 2:
            continue
        out.append({
            "k": f"{r['area_k']}|{r['building_k']}|{r['bedrooms']}br|~{int(r['price_bucket'])}00k",
            "ids": ids,
            "cnt": len(ids),
        })
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="No DB writes, show top-10 groups")
    parser.add_argument("--top", type=int, default=10,
                        help="How many groups to display in dry-run")
    args = parser.parse_args()

    dry_run = args.dry_run
    mode = "DRY-RUN" if dry_run else "LIVE"
    print(f"[xverify] mode={mode}", flush=True)

    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    # 1. phash groups (priority 1)
    phash_groups = find_phash_groups(cur)
    print(f"[xverify] phash groups: {len(phash_groups)}", flush=True)

    # Соберём ids которые уже в phash-группах, чтобы не дублировать в combo
    phash_ids: set[int] = set()
    for g in phash_groups:
        for i in g["ids"]:
            phash_ids.add(i)

    # 2. combo groups (priority 2)
    combo_groups = find_combo_groups(cur, exclude_ids=phash_ids)
    print(f"[xverify] combo groups: {len(combo_groups)}", flush=True)

    all_groups = [
        {"k": f"phash:{g['k'][:12]}", "ids": g["ids"], "cnt": g["cnt"]}
        for g in phash_groups
    ] + [
        {"k": f"combo:{g['k']}", "ids": g["ids"], "cnt": g["cnt"]}
        for g in combo_groups
    ]

    if dry_run:
        print(f"\n[xverify] === TOP-{args.top} groups ===")
        for i, g in enumerate(all_groups[:args.top], 1):
            print(f"  #{i} key={g['k']} count={g['cnt']} ids={g['ids'][:10]}")
        # Process top-10 with stats only
        total = {"deactivated": 0, "merged_fields": 0, "skipped_leads": 0}
        for g in all_groups[:args.top]:
            rows = fetch_group_rows(cur, g["ids"])
            s = process_group(cur, rows, dry_run=True, group_key=g["k"])
            total["deactivated"] += s["deactivated"]
            total["merged_fields"] += s["merged_fields"]
            total["skipped_leads"] += s["skipped_leads"]
            print(f"    → winner={s['winner']} would-deactivate={s['deactivated']} "
                  f"would-merge={s['merged_fields']} skipped(leads)={s['skipped_leads']}")
        print(f"\n[xverify] DRY-RUN totals (top {args.top}): {total}")
        conn.close()
        return

    # LIVE mode — process ALL groups
    total = {"deactivated": 0, "merged_fields": 0, "skipped_leads": 0, "groups": 0}
    for g in all_groups:
        rows = fetch_group_rows(cur, g["ids"])
        try:
            s = process_group(cur, rows, dry_run=False, group_key=g["k"])
            total["deactivated"] += s["deactivated"]
            total["merged_fields"] += s["merged_fields"]
            total["skipped_leads"] += s["skipped_leads"]
            total["groups"] += 1
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[xverify] err group {g['k']}: {e}")

    msg = (
        f"🔀 cross_source_verification done\n"
        f"groups={total['groups']} deactivated={total['deactivated']} "
        f"merged_fields={total['merged_fields']} skipped(leads)={total['skipped_leads']}"
    )
    print(f"[xverify] {msg}")
    if total["deactivated"] > 0 or total["merged_fields"] > 0:
        admin_notify(msg, priority="info")
    conn.close()


if __name__ == "__main__":
    main()
