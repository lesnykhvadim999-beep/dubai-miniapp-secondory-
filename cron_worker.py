# -*- coding: utf-8 -*-
"""
cron_worker.py — Background scheduler for the resale bot.

Jobs:
  • Price alerts          — every 30 min: scan new listings, notify users.
  • Daily admin digest    — 09:00 UTC: send admin a stats summary.
  • DLD re-benchmark      — weekly: rebuild price_benchmarks.json.

Started as a daemon thread from resale_bot.main().
"""
import os, time, json, threading, traceback
from datetime import datetime, timedelta, timezone

import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API       = f"https://api.telegram.org/bot{BOT_TOKEN}"
ADMIN_ID  = int(os.environ.get("ADMIN_ID", "0"))


def _send(cid, text, **kw):
    try:
        requests.post(f"{API}/sendMessage",
                      json={"chat_id": cid, "text": text, "parse_mode": "Markdown", **kw},
                      timeout=10)
    except Exception as e:
        print(f"[cron] send fail: {e}")


# ── 1. Price-alert worker ──────────────────────────────────────────────────────
def _alert_matches(listing, alert):
    """Returns True if listing matches alert filters."""
    if alert.get("deal_type") and listing.get("deal_type") != alert["deal_type"]:
        return False
    if alert.get("property_type") and listing.get("property_type") != alert["property_type"]:
        return False
    if alert.get("emirate") and listing.get("emirate") != alert["emirate"]:
        return False
    if alert.get("area") and listing.get("area") != alert["area"]:
        return False
    if alert.get("bedrooms") is not None and listing.get("bedrooms") != alert["bedrooms"]:
        return False
    p = listing.get("price")
    if alert.get("min_price") and (not p or p < alert["min_price"]): return False
    if alert.get("max_price") and (not p or p > alert["max_price"]): return False
    return True


def run_alerts_once():
    from db_schema import get_conn, get_all_active_alerts, update_alert_last_notified
    alerts = get_all_active_alerts()
    if not alerts:
        return 0

    conn = get_conn()
    sent = 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM listings
                WHERE is_active = TRUE
                  AND (is_audit IS NULL OR is_audit = FALSE)
                  AND created_at > NOW() - INTERVAL '6 hours'
                ORDER BY id DESC
                LIMIT 500
            """)
            new_listings = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    if not new_listings:
        return 0

    for alert in alerts:
        last_id = alert.get("last_listing_id") or 0
        matches = [l for l in new_listings
                   if l["id"] > last_id and _alert_matches(l, alert)]
        if not matches:
            continue
        top = matches[:3]
        for lst in top:
            price = lst.get("price") or 0
            dt    = lst.get("deal_type") or "sale"
            tag   = "AED/yr" if dt == "rent" else "AED"
            price_s = (f"{price/1_000_000:.2f}M {tag}" if price >= 1_000_000
                       else f"{price:,} {tag}")
            text = (
                f"🔔 *NEW MATCH FOR YOUR ALERT*\n\n"
                f"🏢 {lst.get('building') or '—'}\n"
                f"📍 {lst.get('area') or '—'}, {lst.get('emirate') or '—'}\n"
                f"🛏 {lst.get('bedrooms') if lst.get('bedrooms') is not None else '—'} BR  ·  "
                f"{int(lst.get('size_sqft') or 0)} sqft\n"
                f"💰 {price_s}\n\n"
                f"Open in bot: /menu"
            )
            _send(alert["uid"], text)
            sent += 1
        update_alert_last_notified(alert["id"], top[0]["id"])

    return sent


def alerts_loop():
    while True:
        try:
            n = run_alerts_once()
            if n:
                print(f"[cron] price-alerts: sent {n} notifications")
        except Exception as e:
            print(f"[cron] alerts error: {e}")
            traceback.print_exc()
        time.sleep(30 * 60)   # 30 min


# ── 2. Daily admin digest ──────────────────────────────────────────────────────
def _digest_text():
    from db_schema import get_conn
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                  (SELECT COUNT(*) FROM listings WHERE is_active=TRUE
                     AND (is_audit IS NULL OR is_audit=FALSE)) AS visible,
                  (SELECT COUNT(*) FROM listings WHERE is_active=TRUE AND is_audit=TRUE) AS hidden,
                  (SELECT COUNT(*) FROM listings WHERE is_active=TRUE AND is_hot_deal=TRUE
                     AND (is_audit IS NULL OR is_audit=FALSE)) AS hot,
                  (SELECT COUNT(*) FROM listings
                     WHERE created_at > NOW() - INTERVAL '24 hours'
                       AND (is_audit IS NULL OR is_audit=FALSE)) AS new_24h,
                  (SELECT COUNT(*) FROM users) AS users_total,
                  (SELECT COUNT(*) FROM users WHERE last_seen > NOW() - INTERVAL '24 hours') AS users_active,
                  (SELECT COUNT(*) FROM leads WHERE created_at > NOW() - INTERVAL '24 hours') AS leads_24h,
                  (SELECT COUNT(*) FROM favorites WHERE created_at > NOW() - INTERVAL '24 hours') AS favs_24h,
                  (SELECT COUNT(*) FROM price_alerts WHERE is_active=TRUE) AS alerts_active
            """)
            r = cur.fetchone()
    finally:
        conn.close()

    return (
        f"📊 *DAILY DIGEST*\n"
        f"`{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}`\n\n"
        f"*База*\n"
        f"  Видимых:      {r['visible']:,}\n"
        f"  В audit:      {r['hidden']:,}\n"
        f"  Hot deals:    {r['hot']:,}\n"
        f"  Новых (24ч):  {r['new_24h']:,}\n\n"
        f"*Активность*\n"
        f"  Пользователей всего: {r['users_total']:,}\n"
        f"  Активных (24ч):      {r['users_active']:,}\n"
        f"  Лидов (24ч):         {r['leads_24h']:,}\n"
        f"  В избранное (24ч):   {r['favs_24h']:,}\n"
        f"  Активных alerts:     {r['alerts_active']:,}\n"
    )


# ── 4. DLD re-benchmark (weekly) ───────────────────────────────────────────────
def rebenchmark_loop():
    """Re-build price_benchmarks.json from DLD every Monday 04:00 UTC."""
    last_run_day = None
    while True:
        try:
            now = datetime.utcnow()
            # Monday = 0
            if now.weekday() == 0 and now.hour == 4 and last_run_day != now.date():
                print("[cron] Running DLD re-benchmark...")
                import subprocess
                subprocess.run(["python", "build_benchmarks.py"],
                               cwd=os.path.dirname(__file__) or ".",
                               check=False, timeout=600)
                last_run_day = now.date()
                if ADMIN_ID:
                    _send(ADMIN_ID, "✅ DLD price_benchmarks.json rebuilt.")
        except Exception as e:
            print(f"[cron] rebenchmark error: {e}")
        time.sleep(30 * 60)


# ── 5. Photo dedup (daily) ─────────────────────────────────────────────────────
def photo_dedup_loop():
    """Re-run photo dedup every night 02:00 UTC."""
    last_run_day = None
    while True:
        try:
            now = datetime.utcnow()
            if now.hour == 2 and last_run_day != now.date():
                print("[cron] Running photo_dedup...")
                import subprocess
                subprocess.run(["python", "photo_dedup.py"],
                               cwd=os.path.dirname(__file__) or ".",
                               check=False, timeout=300)
                last_run_day = now.date()
        except Exception as e:
            print(f"[cron] photo_dedup error: {e}")
        time.sleep(30 * 60)


def digest_loop():
    """Sends daily digest to admin at ~09:00 UTC."""
    last_sent_day = None
    while True:
        try:
            now = datetime.utcnow()
            if now.hour == 9 and last_sent_day != now.date():
                if ADMIN_ID:
                    _send(ADMIN_ID, _digest_text())
                    print(f"[cron] daily digest sent to {ADMIN_ID}")
                last_sent_day = now.date()
        except Exception as e:
            print(f"[cron] digest error: {e}")
        time.sleep(15 * 60)


# ── 3. Health-check endpoint ───────────────────────────────────────────────────
def start_health_server(port=8080):
    """Tiny HTTP server returning {"status":"ok"} so Railway can monitor."""
    try:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    from db_schema import get_conn
                    conn = get_conn()
                    with conn.cursor() as cur:
                        cur.execute("SELECT COUNT(*) AS n FROM listings WHERE is_active=TRUE")
                        n = cur.fetchone()["n"]
                    conn.close()
                    payload = {"status": "ok", "active_listings": n,
                               "time": datetime.utcnow().isoformat()}
                    code = 200
                except Exception as e:
                    payload = {"status": "error", "error": str(e)}
                    code = 500
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args, **_kw):
                pass

        srv = ThreadingHTTPServer(("0.0.0.0", port), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        print(f"[cron] health server on :{port}")
    except Exception as e:
        print(f"[cron] health server fail: {e}")


# ── Entry point ────────────────────────────────────────────────────────────────
def start_all():
    threading.Thread(target=alerts_loop, daemon=True).start()
    threading.Thread(target=digest_loop, daemon=True).start()
    threading.Thread(target=rebenchmark_loop, daemon=True).start()
    threading.Thread(target=photo_dedup_loop, daemon=True).start()
    start_health_server(port=int(os.environ.get("PORT", "8080")))
    print("[cron] All workers started.")


if __name__ == "__main__":
    print(_digest_text())
