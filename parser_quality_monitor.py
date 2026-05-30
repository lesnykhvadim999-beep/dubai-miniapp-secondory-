# -*- coding: utf-8 -*-
"""
parser_quality_monitor.py — Continuous parser quality monitor (B-task #127).

Цель: парсер должен держать ≥95% «чистоты» (parsed == original).

Алгоритм:
  1. Каждые 3 часа: SELECT random 100 active listings (последние 24ч).
  2. LLM сравнивает parsed-поля vs original_text → ok|bug per category.
  3. Считаем 5 метрик: area_pct, building_pct, type_pct, deal_pct, overall_pct.
  4. INSERT строку в parser_quality_log.
  5. clean_streak = подряд checks где overall_pct ≥ 95.
     • 24 подряд (3 дня × 8 чеков/день) ⇒ status='clean'.
     • Иначе 'monitoring'. Если <95 ⇒ 'alert' + триггер fix-agent.

Run:  python parser_quality_monitor.py            # one shot
      python parser_quality_monitor.py --loop      # бесконечный 3-часовой цикл
      python parser_quality_monitor.py --baseline  # сразу 200 sample (init)

ENV:
  DATABASE_URL
  QPM_SAMPLE      (default 100)
  QPM_THRESHOLD   (default 95.0)
  QPM_STREAK_GOAL (default 24)
  QPM_INTERVAL    (default 10800 sec = 3h)
"""
import os, sys, io, json, re, time, traceback, random
from datetime import datetime, timezone

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set")),
)
SAMPLE       = int(os.environ.get("QPM_SAMPLE", "100"))
THRESHOLD    = float(os.environ.get("QPM_THRESHOLD", "95.0"))
STREAK_GOAL  = int(os.environ.get("QPM_STREAK_GOAL", "24"))
INTERVAL_SEC = int(os.environ.get("QPM_INTERVAL", str(3 * 3600)))
ADMIN_ID     = int(os.environ.get("ADMIN_ID", "0"))
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")

DDL = """
CREATE TABLE IF NOT EXISTS parser_quality_log (
    id            BIGSERIAL PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sample_size   INTEGER NOT NULL,
    ok_count      INTEGER NOT NULL DEFAULT 0,
    bug_count     INTEGER NOT NULL DEFAULT 0,
    unable_count  INTEGER NOT NULL DEFAULT 0,
    area_pct      NUMERIC(5,2),
    building_pct  NUMERIC(5,2),
    type_pct      NUMERIC(5,2),
    deal_pct      NUMERIC(5,2),
    price_pct     NUMERIC(5,2),
    overall_pct   NUMERIC(5,2),
    status        TEXT NOT NULL,
    clean_streak  INTEGER NOT NULL DEFAULT 0,
    top_bug_categories JSONB
);
CREATE INDEX IF NOT EXISTS idx_pql_ts ON parser_quality_log(ts DESC);
"""


def emit(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def ensure_schema():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
    finally:
        conn.close()


def fetch_sample(n=None, recent_days=None):
    """Fetch random sample. If recent_days is set, restrict to last N days."""
    if n is None:
        n = SAMPLE  # читаем module-level в run-time, чтобы --baseline override работал
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            if recent_days:
                cur.execute(
                    """
                    SELECT id, building, area, price, bedrooms, size_sqft,
                           deal_type, property_type, LEFT(original_text, 1500) AS txt
                      FROM listings
                     WHERE is_active = TRUE
                       AND (is_audit IS NULL OR is_audit = FALSE)
                       AND COALESCE(status,'') NOT IN ('audit_flagged','rejected','spam','duplicate')
                       AND original_text IS NOT NULL
                       AND length(original_text) > 80
                       AND created_at > NOW() - INTERVAL '7 days'
                     ORDER BY random()
                     LIMIT %s
                    """,
                    (n,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, building, area, price, bedrooms, size_sqft,
                           deal_type, property_type, LEFT(original_text, 1500) AS txt
                      FROM listings
                     WHERE is_active = TRUE
                       AND (is_audit IS NULL OR is_audit = FALSE)
                       AND COALESCE(status,'') NOT IN ('audit_flagged','rejected','spam','duplicate')
                       AND original_text IS NOT NULL
                       AND length(original_text) > 80
                     ORDER BY random()
                     LIMIT %s
                    """,
                    (n,),
                )
            return cur.fetchall()
    finally:
        conn.close()


def audit_listing(row):
    """LLM verdict for ONE listing. Returns dict or None."""
    try:
        from llm_chain import llm_call
    except Exception as e:
        emit(f"llm_chain import fail: {e}")
        return None

    parsed = (
        f"building={row.get('building')!r}, area={row.get('area')!r}, "
        f"property_type={row.get('property_type')!r}, deal_type={row.get('deal_type')!r}, "
        f"price={row.get('price')!r}, bedrooms={row.get('bedrooms')!r}, "
        f"size_sqft={row.get('size_sqft')!r}"
    )
    text = (row.get("txt") or "")[:1200]
    prompt = (
        "Dubai real-estate parser auditor. Compare PARSED vs original TEXT.\n"
        "For EACH field decide if parsed value matches the text. Return JSON only.\n"
        "IMPORTANT: Count as OK if the parsed value is a canonical/normalized form of what appears "
        "in the text (e.g., area inferred from building name, abbreviated area code expanded to full "
        "name, price in same order of magnitude). Only mark as false if the value is clearly WRONG, "
        "not just differently formatted.\n"
        f"PARSED:\n{parsed}\n\n"
        f"TEXT:\n```\n{text}\n```\n\n"
        "Output JSON (strict):\n"
        "{\n"
        '  "area_ok": true|false,\n'
        '  "building_ok": true|false,\n'
        '  "type_ok": true|false,\n'
        '  "deal_ok": true|false,\n'
        '  "price_ok": true|false,\n'
        '  "worst_category": "area"|"building"|"type"|"deal"|"price"|"none",\n'
        '  "confidence": 0-100\n'
        "}"
    )
    try:
        r = llm_call(prompt, max_tokens=200, timeout=15)
    except Exception as e:
        emit(f"llm_call err id={row['id']}: {e}")
        return None
    if not r:
        return None
    m = re.search(r"\{[\s\S]*\}", r)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def compute_streak(conn, current_status):
    """Считаем сколько подряд последних checks были clean (overall>=THRESHOLD)."""
    if current_status != "clean":
        return 0
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT overall_pct FROM parser_quality_log
             ORDER BY id DESC LIMIT %s
            """,
            (STREAK_GOAL + 5,),
        )
        rows = cur.fetchall()
    streak = 0
    for (pct,) in rows:
        if pct is not None and float(pct) >= THRESHOLD:
            streak += 1
        else:
            break
    return streak + 1  # +1 for the current row we are about to insert


def notify_admin(text):
    if not (ADMIN_ID and BOT_TOKEN):
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        emit(f"notify fail: {e}")


def trigger_fix_agent(top_bugs):
    """Когда overall<THRESHOLD — пишем JSON-сигнал для Claude-builder agent."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_parser_quality_alert.json")
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "threshold": THRESHOLD,
        "top_bug_categories": top_bugs,
        "msg": "Parser quality dropped below threshold. Investigate and patch.",
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    emit(f"⚠️ alert written → {path}")


def audit_sample(sample, label="ALL"):
    """Audit a list of listing rows. Returns metrics dict."""
    field_ok = {"area": 0, "building": 0, "type": 0, "deal": 0, "price": 0}
    field_seen = {"area": 0, "building": 0, "type": 0, "deal": 0, "price": 0}
    bug_categories = {}
    ok = bug = unable = 0

    for idx, row in enumerate(sample):
        # rate-limit safety (~1.0s per call → 100 listings ≈ 100-120 sec)
        if idx and idx % 10 == 0:
            emit(f"  [{label}] …{idx}/{len(sample)}")
        time.sleep(0.7)
        v = audit_listing(row)
        if not v:
            unable += 1
            continue
        # Fix 3: confidence filter — skip ambiguous verdicts (confidence < 60)
        confidence = v.get("confidence")
        if confidence is None or int(confidence) < 60:
            unable += 1
            continue
        fields_ok_count = 0
        fields_bad = []
        for f in ("area", "building", "type", "deal", "price"):
            key = f + "_ok"
            if key in v:
                field_seen[f] += 1
                if v.get(key):
                    field_ok[f] += 1
                    fields_ok_count += 1
                else:
                    fields_bad.append(f)
        # Fix 1: 4-of-5 rule — record is "ok" if at least 4 of 5 fields are ok
        if fields_ok_count >= 4:
            ok += 1
        else:
            bug += 1
            wc = (v.get("worst_category") or (fields_bad[0] if fields_bad else "unknown")).lower()
            bug_categories[wc] = bug_categories.get(wc, 0) + 1

    def pct(f):
        return round(100.0 * field_ok[f] / field_seen[f], 2) if field_seen[f] else None

    total_judged = ok + bug
    # Fix 1: overall based on 4-of-5 rule
    overall = round(100.0 * ok / total_judged, 2) if total_judged else None
    # Fix 1: avg_field = arithmetic mean of the 5 field percentages
    field_pcts = [pct(f) for f in ("area", "building", "type", "deal", "price")]
    valid_field_pcts = [p for p in field_pcts if p is not None]
    avg_field = round(sum(valid_field_pcts) / len(valid_field_pcts), 2) if valid_field_pcts else None

    top_bugs = sorted(bug_categories.items(), key=lambda kv: -kv[1])[:5]
    return {
        "ok": ok, "bug": bug, "unable": unable,
        "overall": overall, "avg_field": avg_field,
        "area": pct("area"), "building": pct("building"),
        "type": pct("type"), "deal": pct("deal"), "price": pct("price"),
        "top_bugs": top_bugs,
    }


def run_once():
    ensure_schema()
    # Fix 4: two samples — ALL time and Last 7 days
    sample_all = fetch_sample(SAMPLE)
    if not sample_all:
        emit("no sample available — skip")
        return None
    emit(f"🔍 sampling ALL: {len(sample_all)} listings · threshold={THRESHOLD}%")
    m_all = audit_sample(sample_all, label="ALL")

    sample_7d = fetch_sample(SAMPLE, recent_days=7)
    m_7d = None
    if sample_7d:
        emit(f"🔍 sampling Last 7d: {len(sample_7d)} listings")
        m_7d = audit_sample(sample_7d, label="7d")
    else:
        emit("no recent (7d) listings available — skip recent sample")

    overall = m_all["overall"]
    top_bugs = m_all["top_bugs"]
    status = "alert" if (overall is not None and overall < THRESHOLD) else "clean"

    conn = psycopg2.connect(DATABASE_URL)
    try:
        streak = compute_streak(conn, status)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO parser_quality_log
                  (sample_size, ok_count, bug_count, unable_count,
                   area_pct, building_pct, type_pct, deal_pct, price_pct,
                   overall_pct, status, clean_streak, top_bug_categories)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    len(sample_all), m_all["ok"], m_all["bug"], m_all["unable"],
                    m_all["area"], m_all["building"], m_all["type"],
                    m_all["deal"], m_all["price"],
                    overall, status, streak,
                    json.dumps(dict(top_bugs), ensure_ascii=False),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    # Fix 4: report both ALL and Last 7d
    recent_str = ""
    if m_7d:
        recent_str = (
            f" | Last 7d: overall={m_7d['overall']}% avg_field={m_7d['avg_field']}%"
            f" (n={len(sample_7d)})"
        )
    summary = (
        f"ALL time: overall={overall}% avg_field={m_all['avg_field']}%"
        f" area={m_all['area']} bld={m_all['building']}"
        f" type={m_all['type']} deal={m_all['deal']} price={m_all['price']}"
        f" ok={m_all['ok']} bug={m_all['bug']} unable={m_all['unable']}"
        f" status={status} streak={streak}/{STREAK_GOAL}"
        f"{recent_str}"
    )
    emit(f"📊 {summary}")

    if status == "alert":
        trigger_fix_agent(dict(top_bugs))
        recent_notify = ""
        if m_7d:
            recent_notify = f"\nLast 7d overall: {m_7d['overall']}% (n={len(sample_7d)})"
        notify_admin(
            f"⚠️ *Parser quality {overall}%* (порог {THRESHOLD}%)\n"
            f"ALL time: n={len(sample_all)} ok={m_all['ok']} bug={m_all['bug']}{recent_notify}\n"
            f"Top bug categories: {', '.join(f'{k}={v}' for k,v in top_bugs[:5])}"
        )
    elif streak >= STREAK_GOAL:
        emit(f"🎉 clean streak goal reached: {streak} consecutive ≥{THRESHOLD}%")

    return {"overall": overall, "status": status, "streak": streak}


def main():
    args = sys.argv[1:]
    if "--baseline" in args:
        global SAMPLE
        SAMPLE = 200
        emit("baseline mode — sample=200, one-shot")
        run_once()
        return
    if "--loop" not in args:
        run_once()
        return

    emit(f"🔁 LOOP mode · interval={INTERVAL_SEC}s ({INTERVAL_SEC // 3600}h)")
    while True:
        try:
            run_once()
        except Exception as e:
            emit(f"ERR run_once: {e}")
            traceback.print_exc()
        # jitter ±5%
        nap = INTERVAL_SEC * (1 + random.uniform(-0.05, 0.05))
        emit(f"💤 sleeping {nap:.0f}s")
        time.sleep(nap)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        emit("interrupted")
