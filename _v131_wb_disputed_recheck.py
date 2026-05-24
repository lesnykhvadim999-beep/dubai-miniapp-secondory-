"""v131 wb-disputed recheck — 6 listings that received conflicting verdicts
across wb-audit and apt-audit stages.

For each listing: 3 LLM calls (cache disabled) → consensus majority (2 of 3).
If no consensus → mark needs_manual_review=true, audit_reason+='v131-wb: LLM dissent'.
If consensus matches current property_type → no change.
If consensus differs → UPDATE property_type + audit_reason.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from datetime import datetime

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm_chain import llm_call, status as llm_status  # noqa: E402

DSN = "REDACTED_DSN_USE_DATABASE_URL_ENV"
LOG_FILE = r"C:/Temp/v131_wb_disputed_recheck.log"
SLEEP_BETWEEN = 8.0

VERDICT_PROMPT = """You are a Dubai real estate parser. Read the listing text and decide its TRUE property type.

Possible types:
- apartment: single residential apartment/flat in a multi-unit building
- villa: standalone house/villa
- whole_building: ENTIRE building/tower for sale (multiple floors, plot+GFA mentioned, NO single bedroom count, usually >50M AED)
- plot: bare land plot, no building
- unknown: cannot determine

Return EXACTLY ONE JSON object (no markdown, no commentary):
{{"verdict": "apartment"|"villa"|"whole_building"|"plot"|"unknown", "confidence": 0.0-1.0, "reasoning": "<=60 chars"}}

Current parsed values: type="{ptype}", building="{building}", area="{area}", bedrooms={br}, price={price}.

Text:
{text}
"""

DISPUTED_IDS = [18223, 18523, 18979, 18984, 19400, 19703]


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"[log err] {e}", flush=True)


def parse_llm_json(raw: str) -> dict | None:
    if not raw:
        return None
    txt = raw.strip()
    txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.DOTALL)
    txt = re.sub(r"^```(?:json)?\s*", "", txt)
    txt = re.sub(r"\s*```\s*$", "", txt)
    txt = txt.strip()
    try:
        return json.loads(txt)
    except Exception:
        m = re.search(r"\{[^{}]*\}", txt, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return None


def call_once(prompt: str) -> dict | None:
    # use_cache=False: must get fresh independent calls
    raw = llm_call(prompt, max_tokens=250, timeout=25, use_cache=False)
    if not raw:
        return None
    return parse_llm_json(raw)


def get_consensus(verdicts: list[str]) -> tuple[str | None, int]:
    """Return (winner, count). Winner is None if no majority."""
    if not verdicts:
        return None, 0
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v] = counts.get(v, 0) + 1
    sorted_v = sorted(counts.items(), key=lambda x: -x[1])
    top, cnt = sorted_v[0]
    if cnt >= 2:
        return top, cnt
    return None, cnt


def update_listing(conn, lid: int, new_type: str | None, current_type: str,
                   reason_suffix: str, manual_review: bool) -> None:
    cur = conn.cursor()
    sets: list[str] = []
    params: list = []
    if new_type and new_type != current_type and new_type in (
        "apartment", "villa", "whole_building", "plot"
    ):
        sets.append("property_type=%s")
        params.append(new_type)
    if manual_review:
        sets.append("needs_manual_review=true")
    sets.append("audit_reason=COALESCE(audit_reason||' | ','') || %s")
    params.append(reason_suffix)
    params.append(lid)
    sql = f"UPDATE listings SET {', '.join(sets)} WHERE id=%s"
    cur.execute(sql, tuple(params))
    conn.commit()
    cur.close()


def main() -> int:
    log("====== v131 wb-disputed 3-LLM recheck ======")
    log(f"providers: {json.dumps(llm_status(), default=str)[:200]}")

    conn = psycopg2.connect(DSN, connect_timeout=15)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, property_type, building, area, bedrooms, price,
               COALESCE(original_text, description, '') AS txt
          FROM listings
         WHERE id = ANY(%s)
         ORDER BY id
    """, (DISPUTED_IDS,))
    rows = cur.fetchall()
    cur.close()
    log(f"loaded {len(rows)} rows of {len(DISPUTED_IDS)} expected")

    summary = {
        "total": len(rows),
        "consensus_matches_current": 0,
        "consensus_differs_updated": 0,
        "no_consensus_manual_review": 0,
        "llm_no_answer": 0,
        "by_id": {},
    }

    for lid, ptype, building, area, br, price, txt in rows:
        log(f"--- id={lid} current_type={ptype} ---")
        verdicts: list[str] = []
        details: list[dict] = []

        prompt = VERDICT_PROMPT.format(
            ptype=ptype,
            building=building or "",
            area=area or "",
            br=br if br is not None else "null",
            price=price if price is not None else "null",
            text=(txt or "")[:1600],
        )

        for attempt in range(3):
            res = call_once(prompt)
            if res:
                v = (res.get("verdict") or "").strip().lower()
                if v in ("apartment", "villa", "whole_building", "plot", "unknown"):
                    verdicts.append(v)
                    details.append(res)
                    log(f"  call#{attempt+1}: {v} (conf={res.get('confidence')}) — {res.get('reasoning')!r}")
                else:
                    log(f"  call#{attempt+1}: bad verdict {v!r}")
            else:
                log(f"  call#{attempt+1}: no answer")
            time.sleep(SLEEP_BETWEEN)

        # consensus excluding "unknown"
        non_unknown = [v for v in verdicts if v != "unknown"]
        winner, cnt = get_consensus(non_unknown if non_unknown else verdicts)
        summary["by_id"][lid] = {
            "current": ptype,
            "verdicts": verdicts,
            "winner": winner,
            "count": cnt,
        }

        if not verdicts:
            summary["llm_no_answer"] += 1
            log(f"  id={lid} NO LLM answers — skip")
            continue

        if winner is None:
            # No majority
            update_listing(
                conn, lid, None, ptype,
                f"v131-wb 3LLM dissent: {','.join(verdicts)}",
                manual_review=True,
            )
            summary["no_consensus_manual_review"] += 1
            log(f"  id={lid} DISSENT {verdicts} -> manual_review")
        elif winner == ptype:
            update_listing(
                conn, lid, None, ptype,
                f"v131-wb 3LLM consensus confirms {winner} ({cnt}/3)",
                manual_review=False,
            )
            summary["consensus_matches_current"] += 1
            log(f"  id={lid} OK consensus={winner} {cnt}/3 — matches current")
        else:
            update_listing(
                conn, lid, winner, ptype,
                f"v131-wb 3LLM consensus -> {winner} ({cnt}/3, was {ptype})",
                manual_review=False,
            )
            summary["consensus_differs_updated"] += 1
            log(f"  id={lid} CHANGED {ptype} -> {winner} ({cnt}/3)")

    conn.close()
    log("====== DONE ======")
    log("SUMMARY: " + json.dumps(summary, ensure_ascii=False))
    try:
        with open(r"C:/Temp/v131_wb_disputed_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"summary save err: {e}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("INTERRUPTED")
        sys.exit(130)
    except Exception as e:
        log(f"FATAL: {e}\n{traceback.format_exc()}")
        sys.exit(1)
