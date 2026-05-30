"""B040: Retro-validator — прогоняет всю активную БД resale-bot через
новые quality gates B037 (strict thresholds) + B038 (area benchmark) +
B039 (area×property_type profile).

Two-phase:
  Phase 1 (fast, no LLM): static checks → bucket suspicious rows.
  Phase 2 (slow, LLM): bench/profile verify, mark confirmed mis-parses as is_audit.

Resumable: state in _retro_validator_state.json.
Fail-safe: LLM error → keep is_audit=FALSE, retry next run.

Usage:
    py -3 _retro_validator.py            # run from start or resume
    py -3 _retro_validator.py --phase 1  # only static scan
    py -3 _retro_validator.py --phase 2  # only LLM verify on existing queue
"""

import json
import os
import sys
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Optional

# ── DB
DSN = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set")),
)
os.environ["DATABASE_URL"] = DSN  # parser_engine needs it for bench/profile caches

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

# ── import parser_engine validators
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
from parser_engine import (  # noqa: E402
    _validate_listing_strict,
    _check_area_benchmark,
    _check_area_profile,
    _bench_llm_verify,
    _profile_llm_verify,
)

STATE_PATH = SCRIPT_DIR / "_retro_validator_state.json"
QUEUE_PATH = SCRIPT_DIR / "_retro_validator_queue.json"
BATCH = 100
SAVE_EVERY = 50
LLM_PACING_SEC = 1.0


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_processed_id": 0,
        "phase1_done": False,
        "stats": {
            "processed": 0,
            "static_clean": 0,
            "llm_queue": 0,
            "confirmed_misparse": 0,
            "llm_errors": 0,
            "reasons_counter": {},
        },
    }


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(STATE_PATH)


def load_queue() -> list:
    if QUEUE_PATH.exists():
        try:
            with open(QUEUE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_queue(q: list) -> None:
    tmp = QUEUE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)
    tmp.replace(QUEUE_PATH)


def row_to_data(row: dict) -> dict:
    return {
        "id": row["id"],
        "building": row.get("building"),
        "area": row.get("area"),
        "price": row.get("price"),
        "deal_type": row.get("deal_type") or "sale",
        "property_type": row.get("property_type") or "apartment",
        "bedrooms": row.get("bedrooms"),
        "size_sqft": row.get("size_sqft"),
        "original_text": row.get("original_text") or "",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1: static scan
# ──────────────────────────────────────────────────────────────────────────────
def phase1(state: dict, queue: list, total_active: int) -> None:
    print(f"[{_now()}] === Phase 1 start, last_processed_id={state['last_processed_id']}")
    conn = psycopg2.connect(DSN, connect_timeout=15)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    reasons_counter: Counter = Counter(state["stats"].get("reasons_counter") or {})
    since_save = 0

    while True:
        cur.execute(
            """SELECT id, building, area, price, deal_type, property_type,
                      bedrooms, size_sqft, original_text
                 FROM listings
                WHERE id > %s
                  AND is_active = TRUE
                  AND COALESCE(is_audit, FALSE) = FALSE
                ORDER BY id
                LIMIT %s""",
            (state["last_processed_id"], BATCH),
        )
        rows = cur.fetchall()
        if not rows:
            break

        for row in rows:
            data = row_to_data(dict(row))
            try:
                reasons1 = _validate_listing_strict(data) or []
            except Exception as e:
                reasons1 = []
                print(f"[{_now()}] WARN validate err id={data['id']}: {e}")
            # _validate_listing_strict already calls bench/profile internally,
            # so reasons1 contains them too. We still check the action-flags here:
            has_bench = any(r.startswith("price_off_median_") for r in reasons1)
            has_profile = any(r.startswith("property_type_rare_for_area_") for r in reasons1)

            for r in reasons1:
                reasons_counter[r] += 1

            state["stats"]["processed"] += 1
            state["last_processed_id"] = data["id"]

            if has_bench or has_profile:
                queue.append({
                    "id": data["id"],
                    "has_bench": has_bench,
                    "has_profile": has_profile,
                    "reasons": reasons1,
                })
                state["stats"]["llm_queue"] = len(queue)
            else:
                state["stats"]["static_clean"] += 1

            since_save += 1
            if since_save >= SAVE_EVERY:
                state["stats"]["reasons_counter"] = dict(reasons_counter)
                save_state(state)
                save_queue(queue)
                since_save = 0

            n = state["stats"]["processed"]
            if n % 100 == 0:
                print(f"[{_now()}] [Phase1 {n}/{total_active}] static_clean={state['stats']['static_clean']} queue={len(queue)}")

    cur.close()
    conn.close()
    state["phase1_done"] = True
    state["stats"]["reasons_counter"] = dict(reasons_counter)
    save_state(state)
    save_queue(queue)
    print(f"[{_now()}] === Phase 1 DONE. processed={state['stats']['processed']} "
          f"static_clean={state['stats']['static_clean']} queue={len(queue)}")
    print(f"[{_now()}] Top reasons: {reasons_counter.most_common(10)}")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2: LLM verify
# ──────────────────────────────────────────────────────────────────────────────
def _mark_audit(cur, lid: int, audit_reason: str) -> None:
    cur.execute(
        "UPDATE listings SET is_audit=TRUE, audit_reason=%s WHERE id=%s",
        (f"B040: {audit_reason}"[:200], lid),
    )


def phase2(state: dict, queue: list) -> None:
    if not queue:
        print(f"[{_now()}] Phase 2: queue empty, nothing to verify")
        return

    print(f"[{_now()}] === Phase 2 start, queue size={len(queue)}")
    conn = psycopg2.connect(DSN, connect_timeout=15)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    new_queue: list = []
    processed_in_p2 = 0

    for item in queue:
        lid = item["id"]
        has_bench = item.get("has_bench", False)
        has_profile = item.get("has_profile", False)

        cur.execute(
            """SELECT id, building, area, price, deal_type, property_type,
                      bedrooms, size_sqft, original_text, is_audit
                 FROM listings WHERE id=%s""", (lid,))
        row = cur.fetchone()
        if not row:
            continue
        if row.get("is_audit"):
            # уже помечен, skip
            continue
        data = row_to_data(dict(row))
        text = data.get("original_text") or ""

        action: Optional[str] = None
        verify_reason = ""

        def _run_llm(fn, label: str) -> Optional[str]:
            """Returns (action_name) or 'error'."""
            nonlocal data
            for attempt in (1, 2):
                try:
                    data, act = fn(data, text)
                    return act
                except Exception as e:
                    err_str = str(e).lower()
                    print(f"[{_now()}] LLM {label} err id={lid} attempt{attempt}: {e}")
                    if "429" in err_str or "rate" in err_str or attempt == 1:
                        time.sleep(60)
                        continue
                    return "error"
            return "error"

        try:
            if has_bench:
                act = _run_llm(_bench_llm_verify, "bench")
                time.sleep(LLM_PACING_SEC)
                if act == "error":
                    state["stats"]["llm_errors"] += 1
                    new_queue.append(item)
                    continue
                if act == "accepted_correction":
                    verify_reason = "bench:price_corrected"
                    action = "misparse"
                elif act == "confirmed_misparse":
                    verify_reason = "bench:confirmed_misparse"
                    action = "misparse"

            if has_profile and action is None:
                act = _run_llm(_profile_llm_verify, "profile")
                time.sleep(LLM_PACING_SEC)
                if act == "error":
                    state["stats"]["llm_errors"] += 1
                    new_queue.append(item)
                    continue
                if act == "corrected":
                    verify_reason = "profile:type_corrected"
                    action = "misparse"
                elif act == "confirmed_misparse":
                    verify_reason = "profile:confirmed_misparse"
                    action = "misparse"

            if action == "misparse":
                _mark_audit(cur, lid, verify_reason + " | " + ",".join(item.get("reasons") or [])[:120])
                state["stats"]["confirmed_misparse"] += 1
                print(f"[{_now()}] MISPARSE id={lid} {verify_reason}")

        except Exception as e:
            print(f"[{_now()}] phase2 err id={lid}: {e}")
            traceback.print_exc()
            new_queue.append(item)
            state["stats"]["llm_errors"] += 1

        processed_in_p2 += 1
        if processed_in_p2 % 25 == 0:
            save_queue(new_queue + queue[queue.index(item) + 1:])
            save_state(state)
            print(f"[{_now()}] [Phase2 {processed_in_p2}/{len(queue)}] "
                  f"misparse={state['stats']['confirmed_misparse']} errors={state['stats']['llm_errors']}")

    cur.close()
    conn.close()
    save_queue(new_queue)
    save_state(state)
    print(f"[{_now()}] === Phase 2 DONE. misparse={state['stats']['confirmed_misparse']} "
          f"errors={state['stats']['llm_errors']} remaining={len(new_queue)}")


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main() -> int:
    phase_arg = None
    for i, a in enumerate(sys.argv[1:]):
        if a == "--phase" and i + 1 < len(sys.argv) - 1:
            try:
                phase_arg = int(sys.argv[i + 2])
            except Exception:
                pass

    state = load_state()
    queue = load_queue()

    # Count total active rows for progress
    try:
        c = psycopg2.connect(DSN, connect_timeout=15)
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) FROM listings WHERE is_active=TRUE AND COALESCE(is_audit,FALSE)=FALSE")
        total = cur.fetchone()[0]
        cur.close(); c.close()
    except Exception as e:
        print(f"[{_now()}] WARN count active: {e}")
        total = 0
    print(f"[{_now()}] total active non-audit: {total}")

    if phase_arg in (None, 1) and not state.get("phase1_done"):
        phase1(state, queue, total)
    else:
        print(f"[{_now()}] Phase 1 already done, skip")

    if phase_arg in (None, 2):
        phase2(state, queue)

    print(f"[{_now()}] FINAL STATE: {json.dumps(state['stats'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
