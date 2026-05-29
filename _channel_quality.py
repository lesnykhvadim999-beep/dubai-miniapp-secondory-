"""Per-channel quality dashboard — daily HTML report into @vadim_admin_bot.

For each known source channel, computes (over the last 7 days):
  - total listings extracted
  - avg confidence_score
  - % needs_manual_review
  - % with building filled
  - % with price filled

Best / worst summary at the bottom.

Run manually:
  ADMIN_BOT_TOKEN=... python _channel_quality.py

ENV:
  DATABASE_URL    — defaults to Railway resale DB
  ADMIN_BOT_TOKEN — required (passed to admin_notify)
  ADMIN_CHAT_ID   — optional (defaults to Vadim)
"""
from __future__ import annotations
import os
import sys
import io
from datetime import datetime, timezone

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
from psycopg2.extras import RealDictCursor

from admin_notify import admin_notify

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "REDACTED_DSN_USE_DATABASE_URL_ENV",
)

# chat_id -> @handle
CHANNELS = {
    1781686176: "flipluxproperty",
    1125918023: "dubairealestatedirectorydubilook",
    2187754007: "secondary_dubai",
}


def fetch_stats() -> dict[int, dict]:
    """Return per-channel stats for the last 7 days."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    telegram_chat_id                                       AS chat_id,
                    COUNT(*)                                               AS total,
                    AVG(confidence_score)::float                           AS avg_conf,
                    SUM(CASE WHEN needs_manual_review THEN 1 ELSE 0 END)   AS mr,
                    SUM(CASE WHEN building   IS NOT NULL
                              AND building <> '' THEN 1 ELSE 0 END)        AS with_bld,
                    SUM(CASE WHEN price IS NOT NULL
                              AND price > 0    THEN 1 ELSE 0 END)          AS with_price
                  FROM listings
                 WHERE created_at > NOW() - INTERVAL '7 days'
                   AND telegram_chat_id = ANY(%s)
                 GROUP BY telegram_chat_id
                """,
                ([str(k) for k in CHANNELS.keys()],),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    out: dict[int, dict] = {}
    for r in rows:
        try:
            out[int(r["chat_id"])] = r
        except (TypeError, ValueError):
            pass
    return out


def pct(num, denom) -> int:
    if not denom:
        return 0
    return int(round(100.0 * float(num) / float(denom)))


def fmt_block(handle: str, st: dict | None) -> str:
    if not st or not st.get("total"):
        return f"<b>@{handle}</b>\n• Listings: 0 (no data)\n"
    total = int(st["total"])
    conf = st.get("avg_conf") or 0.0
    mr = int(st.get("mr") or 0)
    bld = int(st.get("with_bld") or 0)
    pr = int(st.get("with_price") or 0)
    return (
        f"<b>@{handle}</b>\n"
        f"• Listings: {total}\n"
        f"• Avg conf: {conf:.2f}\n"
        f"• Manual review: {pct(mr, total)}%\n"
        f"• With building: {pct(bld, total)}%\n"
        f"• With price: {pct(pr, total)}%\n"
    )


def score(st: dict | None) -> float:
    """Composite quality score: penalize spam-heavy channels."""
    if not st or not st.get("total"):
        return -1.0
    total = float(st["total"])
    conf = float(st.get("avg_conf") or 0.0)
    mr = float(st.get("mr") or 0.0) / total
    bld = float(st.get("with_bld") or 0.0) / total
    pr = float(st.get("with_price") or 0.0) / total
    return conf * 0.4 + bld * 0.25 + pr * 0.25 - mr * 0.3


def build_report() -> str:
    stats = fetch_stats()
    lines = ["📊 <b>Per-channel quality (last 7d)</b>", ""]
    scored: list[tuple[int, str, float, dict | None]] = []
    # stable order: secondary_dubai → dubairealestatedirectorydubilook → flipluxproperty
    order = [2187754007, 1125918023, 1781686176]
    for chat_id in order:
        handle = CHANNELS[chat_id]
        st = stats.get(chat_id)
        lines.append(fmt_block(handle, st))
        scored.append((chat_id, handle, score(st), st))

    valid = [s for s in scored if s[2] >= 0]
    if valid:
        best = max(valid, key=lambda s: s[2])
        worst = min(valid, key=lambda s: s[2])
        lines.append(f"Лучший: @{best[1]} 🥇")
        worst_reason = ""
        st = worst[3] or {}
        total = int(st.get("total") or 0)
        mr_pct = pct(int(st.get("mr") or 0), total) if total else 0
        bld_pct = pct(int(st.get("with_bld") or 0), total) if total else 0
        if mr_pct >= 25:
            worst_reason = " (много manual review / спама)"
        elif bld_pct < 70:
            worst_reason = " (мало building)"
        elif (st.get("avg_conf") or 0) < 0.7:
            worst_reason = " (низкий confidence)"
        lines.append(f"Худший: @{worst[1]}{worst_reason}")

    lines.append("")
    lines.append(
        f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>"
    )
    return "\n".join(lines)


def main() -> int:
    text = build_report()
    print(text)
    ok = admin_notify(text)
    print("sent:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
