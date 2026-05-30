"""Parser quality audit — sample 8 recent listings, ищет баги через LLM.

Цель: непрерывный self-improvement loop:
  1. Sample 8 listings completed за последний час
  2. LLM каждое проверяет vs original_text
  3. Pattern detection — что повторяется?
  4. Output → _parser_bugs.json (structured) + _parser_bugs.log (human-readable)
  5. Claude читает log и применяет фикс

Pacing: 8 calls / 30 min cycle = ~16 calls/час = ~380/день. В free бюджете.

ENV:
  QA_SAMPLE_SIZE (default 8)
"""
import os, sys, io, json, re, time, hashlib
from datetime import datetime, timezone

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
except Exception:
    pass

sys.path.insert(0, '.')

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set"))
)
SAMPLE = int(os.environ.get("QA_SAMPLE_SIZE", "8"))

BUGS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_parser_bugs.log")
BUGS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_parser_bugs.json")
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_parser_bugs_seen.json")

import psycopg2
from psycopg2.extras import RealDictCursor
from llm_chain import llm_call


def emit(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M')}] {msg}", flush=True)


def fetch_sample():
    """8 random recent listings that are NOT flagged and HAVE all fields."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, building, area, price, bedrooms, size_sqft,
                       deal_type, LEFT(original_text, 1500) AS txt
                  FROM listings
                 WHERE created_at > NOW() - INTERVAL '6 hours'
                   AND COALESCE(status,'') NOT IN ('audit_flagged','rejected','spam','duplicate')
                   AND building IS NOT NULL AND price IS NOT NULL
                   AND original_text IS NOT NULL AND length(original_text) > 100
                 ORDER BY random()
                 LIMIT %s
            """, (SAMPLE,))
            return cur.fetchall()
    finally:
        conn.close()


def audit_listing(row):
    """LLM verdict."""
    parsed = (
        f"building={row.get('building')!r}, area={row.get('area')!r}, "
        f"price={row.get('price')!r}, bedrooms={row.get('bedrooms')!r}, "
        f"size_sqft={row.get('size_sqft')!r}, deal_type={row.get('deal_type')!r}"
    )
    text = (row.get("txt") or "")[:1200]
    prompt = (
        "Dubai real estate parser auditor. Compare PARSED data vs original TEXT.\n"
        f"PARSED:\n{parsed}\n\n"
        f"TEXT:\n```\n{text}\n```\n\n"
        "Output JSON only:\n"
        '{"verdict": "ok"|"bug", "category": "wrong_price"|"wrong_building"|"wrong_area"|'
        '"wrong_bedrooms"|"wrong_size"|"wrong_deal_type"|"multiple"|"spam"|"ok",'
        ' "pattern": "<short description if bug, e.g. monthly_installment_taken_as_price>",'
        ' "confidence": 0-100}'
    )
    r = llm_call(prompt, max_tokens=150, timeout=15)
    if not r:
        return None
    m = re.search(r"\{.*\}", r, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def append_log(content):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(BUGS_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n## {ts}\n\n{content}\n")


def save_bugs_snapshot(new_bugs):
    """Структурированный JSON с НОВЫМИ багами для Claude."""
    out = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "new_bugs": new_bugs,
    }
    with open(BUGS_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


def main():
    emit(f"🔍 parser-quality-audit started · sample={SAMPLE}")
    sample = fetch_sample()
    if not sample:
        emit("no recent samples — skip")
        return

    seen = load_seen()
    new_bugs = []
    ok = bug = unable = 0

    for row in sample:
        time.sleep(1.2)  # rate-limit safe
        v = audit_listing(row)
        if not v:
            unable += 1
            continue
        if v.get("verdict") == "ok":
            ok += 1
            continue
        bug += 1
        pattern = (v.get("pattern") or "unknown").lower().strip()
        # Pattern hash — track if "first seen"
        pattern_key = re.sub(r'\W+', '_', pattern)[:60]
        first_time = pattern_key not in seen
        if first_time:
            seen[pattern_key] = {
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "example_id": row["id"],
                "category": v.get("category"),
                "pattern": pattern,
            }
        bug_entry = {
            "id": row["id"],
            "category": v.get("category"),
            "pattern": pattern,
            "confidence": v.get("confidence"),
            "first_seen": first_time,
            "parsed": {
                "building": row.get("building"),
                "area": row.get("area"),
                "price": row.get("price"),
                "bedrooms": row.get("bedrooms"),
                "size_sqft": row.get("size_sqft"),
            },
            "text_snippet": (row.get("txt") or "")[:400],
        }
        new_bugs.append(bug_entry)

    save_seen(seen)

    total = ok + bug
    rate = 100.0 * ok / max(total, 1)
    emit(f"📊 sample={len(sample)} · ok={ok} ({rate:.0f}%) · bug={bug} · unable={unable}")

    # Log + JSON snapshot only if bugs found
    if new_bugs:
        # Compact human log
        lines = [f"**{len(new_bugs)} bugs** (sample of {len(sample)}):"]
        first_time_bugs = [b for b in new_bugs if b["first_seen"]]
        if first_time_bugs:
            lines.append(f"\n🆕 **NEW patterns:** {len(first_time_bugs)}")
            for b in first_time_bugs[:5]:
                lines.append(
                    f"- id={b['id']} · {b['category']} ({b['confidence']}%) — `{b['pattern']}`"
                )
        repeated = [b for b in new_bugs if not b["first_seen"]]
        if repeated:
            lines.append(f"\n🔁 **Known patterns repeating:** {len(repeated)}")
            for b in repeated[:3]:
                lines.append(f"- id={b['id']} · `{b['pattern']}`")
        append_log("\n".join(lines))
        # Always save JSON (Claude reads it)
        save_bugs_snapshot(new_bugs)
        emit(f"✅ wrote {BUGS_LOG} + {BUGS_JSON}")
    else:
        emit(f"✅ clean run, no bugs")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        emit(f"ERR: {e}")
        import traceback
        emit(traceback.format_exc()[:500])
