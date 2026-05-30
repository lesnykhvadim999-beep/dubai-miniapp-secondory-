"""A/B test: parse_message_v2_v1 (single-pass) vs parse_message_v2_two_pass
(new two-pass classify→type-specific extractor).

Pulls 50 random recent listings with non-empty original_text from the
production `listings` table, runs BOTH parsers on each, and prints:
- per-listing comparison row
- agree/disagree totals on property_type, deal_type, area, building
- examples where new version is clearly better (i.e. legacy returned
  None/wrong-class while new succeeded).

Does NOT mutate the DB. Read-only."""
from __future__ import annotations
import os
import sys
import io
import json
import time
import random
from typing import Optional, Any

# UTF-8 stdout for Windows
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                    line_buffering=True)
except Exception:
    pass

os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set")),
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
from parser_v2 import (
    parse_message_v2_v1,
    parse_message_v2_two_pass,
)


SAMPLE_SIZE = int(os.getenv("TEST_SAMPLE", "50"))
SEED = int(os.getenv("TEST_SEED", "42"))


def _first_listing(results: list) -> dict:
    """Return first listing dict (or empty)."""
    if results and isinstance(results, list):
        return results[0] if isinstance(results[0], dict) else {}
    return {}


def _norm(v: Any) -> Optional[str]:
    if v is None:
        return None
    return str(v).strip().lower() or None


def _short(s: Optional[str], n: int = 25) -> str:
    if not s:
        return "-"
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> int:
    random.seed(SEED)

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, telegram_chat_id, original_text
          FROM listings
         WHERE original_text IS NOT NULL
           AND LENGTH(original_text) > 60
           AND created_at > NOW() - INTERVAL '14 days'
         ORDER BY RANDOM()
         LIMIT %s
        """,
        (SAMPLE_SIZE,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    print(f"[test] sampling {len(rows)} recent listings (seed={SEED})\n",
            flush=True)

    agree_ptype = 0
    agree_deal = 0
    agree_area = 0
    agree_bldg = 0
    both_none = 0
    only_v1 = 0
    only_v2 = 0
    examples_better = []
    examples_diff_type = []

    t_v1 = 0.0
    t_v2 = 0.0

    for idx, (lid, chat, text) in enumerate(rows, start=1):
        try:
            t0 = time.time()
            r1 = parse_message_v2_v1(text, source_channel=chat)
            t_v1 += time.time() - t0
        except Exception as e:
            print(f"[v1 fail id={lid}] {e}", flush=True)
            r1 = []

        try:
            t0 = time.time()
            r2 = parse_message_v2_two_pass(text, source_channel=chat)
            t_v2 += time.time() - t0
        except Exception as e:
            print(f"[v2 fail id={lid}] {e}", flush=True)
            r2 = []

        f1 = _first_listing(r1)
        f2 = _first_listing(r2)

        # Aggregates
        if not f1 and not f2:
            both_none += 1
        elif f1 and not f2:
            only_v1 += 1
        elif f2 and not f1:
            only_v2 += 1
            examples_better.append({
                "id": lid,
                "reason": "v1 dropped, v2 extracted",
                "v2": {
                    "deal_type": f2.get("deal_type"),
                    "property_type": f2.get("property_type"),
                    "area": f2.get("area"),
                    "building": f2.get("building"),
                    "price": f2.get("price"),
                },
                "text": (text or "")[:200],
            })

        # Compare keys (only when both produced something)
        pt1 = _norm(f1.get("property_type"))
        pt2 = _norm(f2.get("property_type"))
        if pt1 == pt2:
            agree_ptype += 1
        else:
            examples_diff_type.append({
                "id": lid,
                "v1_pt": pt1,
                "v2_pt": pt2,
                "v1_area": f1.get("area"),
                "v2_area": f2.get("area"),
                "text": (text or "")[:160],
            })

        if _norm(f1.get("deal_type")) == _norm(f2.get("deal_type")):
            agree_deal += 1
        if _norm(f1.get("area")) == _norm(f2.get("area")):
            agree_area += 1
        if _norm(f1.get("building")) == _norm(f2.get("building")):
            agree_bldg += 1

        print(
            f"#{idx:02d} id={lid} | "
            f"v1: dt={_short(f1.get('deal_type'),5):5s} pt={_short(f1.get('property_type'),11):11s} "
            f"area={_short(f1.get('area'),16):16s} bldg={_short(f1.get('building'),18):18s} | "
            f"v2: dt={_short(f2.get('deal_type'),5):5s} pt={_short(f2.get('property_type'),11):11s} "
            f"area={_short(f2.get('area'),16):16s} bldg={_short(f2.get('building'),18):18s}",
            flush=True,
        )

    total = len(rows)
    print("\n" + "=" * 70)
    print(f"SAMPLE: {total} listings")
    print(f"both extracted nothing : {both_none}")
    print(f"only v1 extracted      : {only_v1}")
    print(f"only v2 extracted      : {only_v2}  ← new wins here")
    print("-" * 70)
    print(f"agree property_type    : {agree_ptype}/{total}  ({agree_ptype/total:.0%})")
    print(f"agree deal_type        : {agree_deal}/{total}  ({agree_deal/total:.0%})")
    print(f"agree area             : {agree_area}/{total}  ({agree_area/total:.0%})")
    print(f"agree building         : {agree_bldg}/{total}  ({agree_bldg/total:.0%})")
    print("-" * 70)
    print(f"total time v1: {t_v1:.1f}s  | v2: {t_v2:.1f}s")
    print(f"avg per call : v1 {t_v1/max(total,1):.2f}s, v2 {t_v2/max(total,1):.2f}s")

    if examples_better:
        print("\n--- v2 BETTER (v1 dropped, v2 extracted) ---")
        for ex in examples_better[:8]:
            print(json.dumps(ex, ensure_ascii=False, indent=2))

    if examples_diff_type:
        print("\n--- property_type DISAGREEMENT examples (first 8) ---")
        for ex in examples_diff_type[:8]:
            print(json.dumps(ex, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
