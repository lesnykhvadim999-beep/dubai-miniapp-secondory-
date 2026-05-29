"""
_check_price_alerts.py — #48 6-hourly cron for Listing Price-Drop Alerts.

For each active listing_price_alerts row:
  1. Compare baseline_price vs current listings.price (and latest price_history).
  2. If price dropped >= target_drop_percent → send alert to user.
  3. UPDATE last_alerted_at, last_alerted_price, baseline_price = current_price.

Run from Railway cron (every 6h):
    py _check_price_alerts.py
"""
import os
import time
import requests

from db_schema import (
    get_conn,
    get_all_active_listing_price_alerts,
    update_listing_price_alert_notified,
)

BOT_TOKEN = (os.environ.get("RESALE_BOT_TOKEN")
             or os.environ.get("TELEGRAM_BOT_TOKEN", ""))
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
SLEEP = 0.35


def _send(chat_id: int, text: str):
    if not BOT_TOKEN:
        print(f"[pd-cron] WARN no BOT_TOKEN; would send to {chat_id}: {text[:80]}")
        return
    try:
        r = requests.post(
            f"{API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if not r.ok:
            print(f"[pd-cron] send fail uid={chat_id}: {r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"[pd-cron] send err uid={chat_id}: {e}")


def _format_drop(row: dict, drop_pct: float) -> str:
    lid     = row.get("listing_id")
    area    = row.get("area") or "—"
    bld     = row.get("building") or ""
    br      = row.get("bedrooms")
    base    = row.get("baseline_price") or 0
    cur_p   = row.get("current_price") or 0
    deal    = row.get("deal_type") or "sale"
    saved   = base - cur_p
    br_str  = f"{br}BR" if (br is not None and br > 0) else ("Studio" if br == 0 else "—")

    def _fmt(p):
        if p >= 1_000_000:
            return f"{p/1_000_000:.2f}M AED"
        return f"{p:,} AED"

    head = f"📉 *Price drop on listing #{lid}*"
    body = (
        f"{area}" + (f" · {bld}" if bld else "") + f" · {br_str} · {deal.upper()}\n\n"
        f"Was:  {_fmt(base)}\n"
        f"Now:  *{_fmt(cur_p)}*\n"
        f"Drop: *-{drop_pct:.1f}%*  (saved {_fmt(saved)})\n\n"
        f"/detail_{lid} — open · /pdrop_del_{lid} — stop watching"
    )
    return head + "\n\n" + body


def main():
    print(f"[pd-cron] start  has_token={bool(BOT_TOKEN)}", flush=True)
    rows = get_all_active_listing_price_alerts()
    print(f"[pd-cron] active subscriptions: {len(rows)}", flush=True)
    sent, skipped, errs = 0, 0, 0

    for r in rows:
        try:
            base   = int(r.get("baseline_price") or 0)
            cur_p  = int(r.get("current_price") or 0)
            target = float(r.get("target_drop_percent") or 5.0)
            if base <= 0 or cur_p <= 0:
                skipped += 1
                continue
            if cur_p >= base:
                # price did NOT drop (it stayed or increased) — keep baseline as-is
                skipped += 1
                continue
            drop_pct = (base - cur_p) / base * 100.0
            if drop_pct < target:
                skipped += 1
                continue
            # Got a qualifying drop — alert + reset baseline to current
            text = _format_drop(r, drop_pct)
            _send(r["user_id"], text)
            update_listing_price_alert_notified(r["id"], cur_p)
            sent += 1
            time.sleep(SLEEP)
        except Exception as e:
            errs += 1
            print(f"[pd-cron] err lpa_id={r.get('id')}: {e}", flush=True)

    print(f"[pd-cron] done  sent={sent} skipped={skipped} errs={errs}", flush=True)


if __name__ == "__main__":
    main()
