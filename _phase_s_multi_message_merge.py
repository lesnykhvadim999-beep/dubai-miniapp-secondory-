"""Phase S — Multi-message listing merge.

Brokers often split one listing across 2-3 consecutive Telegram messages
(text-only, then a price reply, then a photo album).  Parser v2 sees them
as independent rows with partial data.  This phase collapses such groups.

Algorithm:
    1. Pull all active listings that have telegram_chat_id + seller_username
       + message_date.  Skip rows that are already "complete"
       (area + price + bedrooms + building all filled) — they don't need
       help and shouldn't be touched.
    2. Group by (chat_id, seller_username) and sort by message_date.
    3. Walk the timeline.  Two rows belong to the same group if both:
         * same chat_id + author
         * sent within SIBLING_WINDOW_MINUTES of the previous row
       Build chained groups (A-B within 2min, B-C within 2min => A-B-C).
    4. For each group with >= 2 rows:
         * Pick the "primary" via parser_v2_helpers.pick_primary
           (longest v2_source_message_text, then has_price, then smallest id).
         * Concatenate v2_source_message_text from all siblings (in time
           order, separated by a blank line).
         * Fill any NULL field on the primary from the first non-NULL sibling
           (area, price, building, bedrooms, size_sqft, has_images, status,
           cover_image_url, cover_image_tg_id, photo_phash).
         * If the concatenated text grew meaningfully, re-run
           parser_v2.parse_message_v2 to recover any new fields the original
           single-block extraction missed (best block by char length wins,
           fields only used when primary still NULL).
         * UPDATE the primary row.
         * UPDATE each non-primary sibling: is_active=FALSE,
           status='merged_into_<primary_id>'.
    5. Report stats.

Use DRY_RUN=1 to print the plan without writing.

Env:
    DATABASE_URL — Postgres DSN. Falls back to the project default.
    DRY_RUN       — "1" to skip COMMIT; default "1" if not set.
    SIBLING_WINDOW_MINUTES — default 2.
    RERUN_PARSER  — "1" to re-run parser_v2 on merged text (default 1).
"""
from __future__ import annotations

import os
import sys
import json
from datetime import timedelta
from collections import defaultdict
from typing import List, Dict, Any, Optional

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parser_v2_helpers import is_complete, pick_primary  # type: ignore


DEFAULT_DSN = (
    "postgresql://postgres:REDACTED_DSN_PASSWORD@"
    "tramway.proxy.rlwy.net:23228/railway"
)
DSN = os.environ.get("DATABASE_URL", DEFAULT_DSN)

DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
WINDOW_MIN = int(os.environ.get("SIBLING_WINDOW_MINUTES", "2"))
RERUN_PARSER = os.environ.get("RERUN_PARSER", "1") == "1"


# Fields we will try to fill on the primary from siblings (in this order)
FILLABLE_FIELDS = [
    "area",
    "price",
    "building",
    "bedrooms",
    "bathrooms",
    "size_sqft",
    "bua_sqft",
    "plot_sqft",
    "floor",
    "view",
    "furnishing",
    "status",
    "property_type",
    "deal_type",
    "emirate",
    "cover_image_url",
    "cover_image_tg_id",
    "photo_phash",
    "phone",
    "whatsapp",
    "agent_name",
    "description",
]

PARSER_FIELD_MAP = {
    # parse_message_v2 dict key -> column name
    "area": "area",
    "building": "building",
    "price": "price",
    "bedrooms": "bedrooms",
    "bathrooms": "bathrooms",
    "sqft": "size_sqft",
    "size_sqft": "size_sqft",
    "deal_type": "deal_type",
    "property_type": "property_type",
    "emirate": "emirate",
    "view": "view",
    "furnishing": "furnishing",
    "floor": "floor",
}


def _fetch_candidates(conn) -> List[Dict[str, Any]]:
    sql = """
        SELECT id, telegram_chat_id, telegram_message_id, seller_username,
               message_date, area, price, building, bedrooms, bathrooms,
               size_sqft, bua_sqft, plot_sqft, floor, view, furnishing,
               status, property_type, deal_type, emirate,
               cover_image_url, cover_image_tg_id, photo_phash, phone, whatsapp,
               agent_name, description, has_images, unit_number,
               v2_source_message_text, v2_total_blocks_in_message, is_active
        FROM listings
        WHERE is_active = TRUE
          AND telegram_chat_id IS NOT NULL
          AND seller_username  IS NOT NULL
          AND message_date     IS NOT NULL
        ORDER BY telegram_chat_id, seller_username, message_date ASC, id ASC
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


def _build_groups(rows: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Build candidate sibling groups.

    Two strategies, deduplicated by primary id:

      (1) Same telegram_message_id groups — parser_v2 may have over-split
          a single message. We treat each message_id as its own group.

      (2) Time-window chain — same chat+author, consecutive within
          WINDOW_MIN. Used to catch true cross-message splits (text in
          one msg, photo album follow-up in the next msg).
    """
    groups: List[List[Dict[str, Any]]] = []

    # (1) Same telegram_message_id buckets
    by_msg: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = (str(r["telegram_chat_id"]), str(r["telegram_message_id"]))
        by_msg[key].append(r)
    for items in by_msg.values():
        if len(items) >= 2:
            items.sort(key=lambda r: r["id"])
            groups.append(items)

    # (2) Time-window chains across messages (different message_id but
    # same chat+author, within WINDOW_MIN).
    by_pair: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = (str(r["telegram_chat_id"]), r["seller_username"])
        by_pair[key].append(r)
    window = timedelta(minutes=WINDOW_MIN)
    for items in by_pair.values():
        if len(items) < 2:
            continue
        items.sort(key=lambda r: (r["message_date"], r["id"]))
        cur_group = [items[0]]
        for prev, cur in zip(items, items[1:]):
            if (cur["message_date"] - prev["message_date"] <= window
                    and str(cur.get("telegram_message_id"))
                    != str(prev.get("telegram_message_id"))):
                cur_group.append(cur)
            else:
                if len(cur_group) >= 2:
                    groups.append(cur_group)
                cur_group = [cur]
        if len(cur_group) >= 2:
            groups.append(cur_group)
    return groups


def _are_compatible(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Two rows are compatible (could be fragments of same listing) only if
    their non-NULL key fields agree. If both have a price and the prices
    differ — they're distinct listings.  Same for area/building/bedrooms.
    """
    for col in ("price", "area", "building", "bedrooms"):
        av, bv = a.get(col), b.get(col)
        # For bedrooms only: treat None as missing, but 0 (studio) as a
        # real value. For other columns 0/"" also count as missing.
        if col == "bedrooms":
            if av is None or bv is None:
                continue
        else:
            if av in (None, "", 0) or bv in (None, "", 0):
                continue
        if isinstance(av, str) and isinstance(bv, str):
            if av.strip().lower() != bv.strip().lower():
                return False
        else:
            if av != bv:
                return False
    # Also require that ANY shared non-null field actually matches — i.e.
    # if both rows have a non-null building, it must match (already checked
    # above). Additionally require that the rows are not 'fully different'
    # listings: if BOTH have price AND building AND they all match, fine;
    # if neither shares any non-null key field, refuse to consider them
    # the same listing (avoid blind-merging two photo-only rows).
    shared_any = False
    for col in ("price", "area", "building", "bedrooms"):
        av, bv = a.get(col), b.get(col)
        if col == "bedrooms":
            if av is not None and bv is not None:
                shared_any = True
                break
        else:
            if av not in (None, "", 0) and bv not in (None, "", 0):
                shared_any = True
                break
    if not shared_any:
        # No overlapping field at all — only merge if they likely complement
        # each other (one has text, the other has nothing).
        # Require at least one of the two to have meaningful text.
        ta = (a.get("v2_source_message_text") or "").strip()
        tb = (b.get("v2_source_message_text") or "").strip()
        if not (ta or tb):
            return False
    return True


def _split_into_compatible_subgroups(
        group: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Brokers often post multiple DIFFERENT listings within 2 minutes
    (an inventory dump).  Even though they share chat+author+time, the rows
    have different prices / different buildings — they are NOT split
    fragments of one listing.

    This function partitions the time-window group into compatible
    subgroups using union-find on the compatibility relation.
    """
    n = len(group)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for i in range(n):
        for j in range(i + 1, n):
            if _are_compatible(group[i], group[j]):
                union(i, j)

    buckets: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for i, r in enumerate(group):
        buckets[find(i)].append(r)

    # Strict check: every pair in the bucket must be compatible. If not,
    # discard the whole bucket (safer than merging incompatible rows via
    # transitivity).
    result: List[List[Dict[str, Any]]] = []
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        ok = True
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                if not _are_compatible(bucket[i], bucket[j]):
                    ok = False
                    break
            if not ok:
                break
        if ok:
            result.append(bucket)
    return result


def _is_safe_merge_pattern(sg: List[Dict[str, Any]]) -> bool:
    """Restrict merge to high-confidence split-listing patterns:

      Pattern A — text-rich + text-poor pair:
          One row has a non-trivial v2_source_message_text (>=50 chars)
          and the others have empty/very-short text. This catches the
          "broker posts caption then sends a bare photo follow-up" case.

      Pattern B — same key fields, different sqft is NOT allowed:
          If multiple rows have non-null size_sqft values that DIFFER,
          they're distinct units in the same building. Reject.

      Pattern C — same key fields, conflicting unit_number/floor: reject.
    """
    # Reject if size_sqft values disagree
    sqfts = [r.get("size_sqft") for r in sg
             if r.get("size_sqft") not in (None, 0, 0.0)]
    if len(set(sqfts)) > 1:
        return False

    # Reject if floor values disagree
    floors = [r.get("floor") for r in sg if r.get("floor") not in (None, 0)]
    if len(set(floors)) > 1:
        return False

    # Reject if unit_number values disagree
    units = [r.get("unit_number") for r in sg
             if r.get("unit_number") not in (None, "")]
    if len(set(units)) > 1:
        return False

    # Require at least one "rich" + one "poor" text row, OR identical
    # telegram_message_id across all members + clearly complementary data.
    text_lens = [len((r.get("v2_source_message_text") or "").strip()) for r in sg]
    has_rich = any(tl >= 50 for tl in text_lens)
    has_poor = any(tl < 20 for tl in text_lens)
    if has_rich and has_poor:
        return True

    # Same telegram_message_id pairs are tricky: parser_v2 deliberately
    # splits a single message into multiple blocks. Allow merging two
    # blocks from the same message ONLY when:
    #   * exactly 2 rows
    #   * both rows are the only 2 blocks of that message
    #     (v2_total_blocks_in_message == 2)
    #   * they are strictly complementary: one carries area (no building),
    #     the other carries building+price (no area), AND bedrooms match
    #     or one is NULL.
    # This catches the "header + detail" over-split pattern where the
    # parser broke "Palm Jumeirah — 3BR / Palm Beach Towers 2 — details"
    # into two rows.
    msg_ids = {str(r.get("telegram_message_id")) for r in sg}
    if len(msg_ids) == 1 and len(sg) == 2:
        a, b = sg
        # Both should come from a 2-block message
        if (a.get("v2_total_blocks_in_message") == 2
                and b.get("v2_total_blocks_in_message") == 2):
            # Identify header vs detail.
            # Header row = has area, missing building AND price.
            # Detail row = has building AND price.
            # The detail row may also have area filled (re-extracted),
            # which is fine as long as it matches the header's area.
            def is_header(r):
                return (r.get("area") and not r.get("building")
                        and r.get("price") in (None, 0))

            def is_detail(r):
                return (r.get("building") and
                        r.get("price") not in (None, 0))

            if (is_header(a) and is_detail(b)) or \
               (is_header(b) and is_detail(a)):
                return True
    return False


def _filter_mergeable(group: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Returns a list of mergeable sub-groups.

    A subgroup is mergeable iff:
      * All members are pairwise compatible (no conflicting prices / areas /
        buildings / bedrooms / sqft / floor / unit).
      * At least one member is INCOMPLETE.
      * At least one OTHER member can plausibly contribute the missing
        information.
      * The group matches a safe split-listing pattern (see
        _is_safe_merge_pattern).

    Returns [] when nothing should be merged."""
    if len(group) < 2:
        return []

    subgroups = _split_into_compatible_subgroups(group)
    out: List[List[Dict[str, Any]]] = []
    for sg in subgroups:
        if all(is_complete(r) for r in sg):
            continue
        if not _is_safe_merge_pattern(sg):
            continue
        # Need at least one row with a complementary value
        useful = False
        for r in sg:
            if is_complete(r):
                useful = True
                break
            for col in ("area", "price", "building", "bedrooms",
                        "size_sqft", "v2_source_message_text"):
                v = r.get(col)
                if v not in (None, "", 0):
                    for other in sg:
                        if other["id"] == r["id"]:
                            continue
                        ov = other.get(col)
                        if ov in (None, "", 0):
                            useful = True
                            break
                if useful:
                    break
            if useful:
                break
        if useful:
            out.append(sg)
    return out


def _concatenate_text(group: List[Dict[str, Any]]) -> str:
    parts = []
    seen = set()
    for r in group:
        t = (r.get("v2_source_message_text") or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        parts.append(t)
    return "\n\n".join(parts)


def _merge_field_values(primary: Dict[str, Any], group: List[Dict[str, Any]]):
    """Returns (updated_fields_dict, field_fill_counts).

    Only fills NULL/empty fields on the primary. Does not overwrite
    non-NULL values.
    """
    updates: Dict[str, Any] = {}
    fill_counts: Dict[str, int] = defaultdict(int)

    for col in FILLABLE_FIELDS:
        cur_val = primary.get(col)
        if cur_val not in (None, "", 0) or (col == "bedrooms" and cur_val is not None):
            # bedrooms=0 is a valid "studio" value, but if it's the existing
            # value we keep it. Skip filling.
            continue
        # Find first sibling with a non-null value
        for r in group:
            if r["id"] == primary["id"]:
                continue
            v = r.get(col)
            if v in (None, "", 0):
                continue
            updates[col] = v
            fill_counts[col] += 1
            break
    # has_images = OR across siblings
    if not primary.get("has_images") and any(r.get("has_images") for r in group):
        updates["has_images"] = True
        fill_counts["has_images"] += 1
    return updates, fill_counts


def _rerun_parser(text: str) -> Optional[Dict[str, Any]]:
    """Re-run parser_v2 on merged text and pick the best block.
    Returns dict of extracted fields, or None on error."""
    try:
        from parser_v2 import parse_message_v2  # type: ignore
    except Exception as e:
        print(f"[warn] parser_v2 import failed: {e}")
        return None
    try:
        blocks = parse_message_v2(text)
    except Exception as e:
        print(f"[warn] parse_message_v2 raised: {e}")
        return None
    if not blocks:
        return None
    # Pick the block with the most extracted fields
    best = max(blocks, key=lambda b: sum(1 for k, v in b.items()
                                         if v not in (None, "", {}, []) and not k.startswith("v2_")))
    return best


def _apply_parser_recovery(primary: Dict[str, Any],
                           updates: Dict[str, Any],
                           parsed: Dict[str, Any],
                           fill_counts: Dict[str, int]):
    for src_key, col in PARSER_FIELD_MAP.items():
        cur_val = primary.get(col)
        # Don't overwrite if primary already has it or we already planned to fill from sibling
        if cur_val not in (None, "", 0) and not (col == "bedrooms" and cur_val == 0):
            continue
        if col in updates:
            continue
        v = parsed.get(src_key)
        if v in (None, "", 0) and not (col == "bedrooms" and v == 0):
            continue
        updates[col] = v
        fill_counts[col] += 1
        fill_counts["__from_parser__"] += 1


def _update_primary(conn, primary_id: int, updates: Dict[str, Any], merged_text: str):
    # Always update v2_source_message_text to the concatenated form
    updates = dict(updates)
    updates["v2_source_message_text"] = merged_text

    cols = list(updates.keys())
    set_clause = ", ".join(f"{c} = %s" for c in cols)
    sql = f"UPDATE listings SET {set_clause}, updated_at = NOW() WHERE id = %s"
    params = [updates[c] for c in cols] + [primary_id]
    cur = conn.cursor()
    cur.execute(sql, params)
    cur.close()


def _deactivate_sibling(conn, sib_id: int, primary_id: int):
    sql = """
        UPDATE listings
        SET is_active = FALSE,
            status    = %s,
            updated_at = NOW()
        WHERE id = %s
    """
    label = f"merged_into_{primary_id}"[:50]
    cur = conn.cursor()
    cur.execute(sql, (label, sib_id))
    cur.close()


def main():
    print("=" * 70)
    print(f"Phase S — multi-message merge   DRY_RUN={DRY_RUN}   "
          f"window={WINDOW_MIN}min   rerun_parser={RERUN_PARSER}")
    print("=" * 70)

    conn = psycopg2.connect(DSN)
    conn.autocommit = False

    rows = _fetch_candidates(conn)
    print(f"[scan] active candidate rows: {len(rows)}")

    groups = _build_groups(rows)
    print(f"[scan] raw sibling groups: {len(groups)}")

    mergeable = []
    for g in groups:
        for sg in _filter_mergeable(g):
            mergeable.append(sg)
    print(f"[scan] mergeable subgroups (compatible + has gap): {len(mergeable)}")

    total_records_collapsed = 0
    total_fill_counts: Dict[str, int] = defaultdict(int)
    primaries_updated = 0
    parser_reruns = 0
    parser_recovered_groups = 0

    sample_log = []

    for g in mergeable:
        primary = pick_primary(g)
        if primary is None:
            continue
        siblings = [r for r in g if r["id"] != primary["id"]]
        if not siblings:
            continue

        merged_text = _concatenate_text(g)
        updates, fill_counts = _merge_field_values(primary, g)

        rerun_used = False
        if RERUN_PARSER:
            old_text_len = len(primary.get("v2_source_message_text") or "")
            if len(merged_text) > old_text_len + 20:
                parsed = _rerun_parser(merged_text)
                parser_reruns += 1
                if parsed:
                    before_count = len(fill_counts)
                    _apply_parser_recovery(primary, updates, parsed, fill_counts)
                    if len(fill_counts) > before_count:
                        parser_recovered_groups += 1
                        rerun_used = True

        # Tally
        for k, v in fill_counts.items():
            if k.startswith("__"):
                continue
            total_fill_counts[k] += v

        primaries_updated += 1
        total_records_collapsed += len(siblings)

        if len(sample_log) < 10:
            sample_log.append({
                "primary_id": primary["id"],
                "sibling_ids": [s["id"] for s in siblings],
                "fields_filled": dict(fill_counts),
                "rerun_used": rerun_used,
                "text_len": len(merged_text),
            })

        if not DRY_RUN:
            try:
                _update_primary(conn, primary["id"], updates, merged_text)
                for s in siblings:
                    _deactivate_sibling(conn, s["id"], primary["id"])
            except Exception as e:
                conn.rollback()
                print(f"[err] group primary={primary['id']}: {e}")
                continue

    if DRY_RUN:
        conn.rollback()
        print("\n[DRY_RUN] no changes committed.")
    else:
        conn.commit()
        print("\n[COMMIT] changes applied.")

    print("\n" + "=" * 70)
    print("STATS")
    print("=" * 70)
    print(f"groups merged (primaries updated):  {primaries_updated}")
    print(f"records collapsed (deactivated):    {total_records_collapsed}")
    print(f"parser re-runs attempted:           {parser_reruns}")
    print(f"groups recovered by parser re-run:  {parser_recovered_groups}")
    print("\nNet new field fills:")
    for k in sorted(total_fill_counts.keys()):
        print(f"  +{total_fill_counts[k]:>5}  {k}")
    print("\nSample groups:")
    for s in sample_log:
        print(f"  primary={s['primary_id']}  siblings={s['sibling_ids']}  "
              f"fills={s['fields_filled']}  rerun={s['rerun_used']}  "
              f"text_len={s['text_len']}")

    conn.close()


if __name__ == "__main__":
    main()
