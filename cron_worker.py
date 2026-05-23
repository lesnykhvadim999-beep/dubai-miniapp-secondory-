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


# ── 6. Watchlist worker (v55) ──────────────────────────────────────────────────
# Daily 09:00 GST (05:00 UTC): шлёт юзерам дайджест новых matches их watchlist'ов.
# Weekly Sun 10:00 GST (06:00 UTC): AI-дайджест на основе price drops + новых
# hot deals + 1 рыночного инсайта.

def _watch_matches(listing, watch):
    """Listing совпадает с watchlist?"""
    wt = watch.get("watch_type")
    wv = (watch.get("watch_value") or "").strip()
    filters = watch.get("filters") or {}
    if isinstance(filters, str):
        try: filters = json.loads(filters)
        except Exception: filters = {}

    if wt == "area":
        if not wv: return False
        if (listing.get("area") or "").lower() != wv.lower(): return False
    elif wt == "building":
        if not wv: return False
        if (listing.get("building") or "").lower() != wv.lower(): return False
    elif wt == "developer":
        # developer хранится в buildings — пока пропускаем как always-False
        # (UI пока не создаёт такие подписки)
        return False
    elif wt == "price_range":
        # формат '2000000-3000000'
        try:
            mn_s, mx_s = (wv or "0-0").split("-", 1)
            mn, mx = int(mn_s or 0), int(mx_s or 0)
        except Exception:
            return False
        p = listing.get("price") or 0
        if mn and p < mn: return False
        if mx and p > mx: return False
    elif wt == "listing":
        # этот тип — подписка на ровно тот listing (для price drop alerts)
        # matching по filters: area + building + bedrooms + property_type
        if filters.get("area") and listing.get("area") != filters["area"]:
            return False
        if filters.get("building") and listing.get("building") != filters["building"]:
            return False
        if filters.get("bedrooms") is not None and \
           listing.get("bedrooms") != filters["bedrooms"]:
            return False
        if filters.get("property_type") and \
           listing.get("property_type") != filters["property_type"]:
            return False
        if filters.get("deal_type") and \
           listing.get("deal_type") != filters["deal_type"]:
            return False
    else:
        return False
    # доп. фильтры по price (если заданы)
    p = listing.get("price") or 0
    mn = filters.get("min_price"); mx = filters.get("max_price")
    if mn and p < mn: return False
    if mx and p > mx: return False
    return True


def run_watchlist_daily():
    """Daily digest — каждому юзеру одно сообщение со всеми matches за 24ч."""
    from db_schema import (get_conn, get_active_watchlists,
                            update_watchlist_notified)
    watches = get_active_watchlists()
    if not watches:
        return 0

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM listings
                 WHERE is_active=TRUE
                   AND (is_audit IS NULL OR is_audit=FALSE)
                   AND created_at > NOW() - INTERVAL '24 hours'
                 ORDER BY id DESC
                 LIMIT 1500
            """)
            new_listings = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    if not new_listings:
        return 0

    # Группируем matches по user_id
    by_user = {}
    for w in watches:
        if w.get("notification_freq") not in (None, "daily", "instant"):
            continue
        matches = [l for l in new_listings if _watch_matches(l, w)]
        if not matches:
            continue
        by_user.setdefault(w["user_id"], []).extend(matches[:5])

    sent = 0
    for uid, lst in by_user.items():
        # уникализуем по id, top 5
        seen, items = set(), []
        for l in lst:
            if l["id"] in seen: continue
            seen.add(l["id"]); items.append(l)
            if len(items) >= 5: break
        lines = [f"📬 *Daily Digest*  ·  {len(items)} new matches\n"]
        for l in items:
            p = l.get("price") or 0
            dt = l.get("deal_type") or "sale"
            tag = "AED/yr" if dt == "rent" else "AED"
            ps = (f"{p/1_000_000:.2f}M {tag}" if p >= 1_000_000 else f"{p:,} {tag}")
            lines.append(
                f"• 🏢 {l.get('building') or '—'}  ·  "
                f"📍 {l.get('area') or '—'}\n"
                f"  🛏 {l.get('bedrooms') if l.get('bedrooms') is not None else '—'}BR  ·  "
                f"💰 {ps}"
            )
        lines.append("\n_Open /menu to view full listings._")
        _send(uid, "\n".join(lines))
        sent += 1

    # отметим notified для всех использованных watch'ей
    for w in watches:
        if w["user_id"] in by_user:
            try: update_watchlist_notified(w["id"], weekly=False)
            except Exception: pass

    return sent


def _ai_insight_for_user(user_lists: list) -> str:
    """Cerebras llm_call → 1-2 предложения рыночного инсайта для подписок юзера."""
    try:
        from llm_chain import llm_call
        # Список интересов
        interests = []
        for l in user_lists[:5]:
            interests.append(f"- {l.get('area') or '—'} / "
                             f"{l.get('building') or '—'} / "
                             f"{l.get('bedrooms') if l.get('bedrooms') is not None else '?'}BR / "
                             f"{(l.get('price') or 0)//1000}K AED")
        prompt = (
            "Ты — Dubai real-estate advisor. Дай ОДИН короткий инсайт (1-2 "
            "предложения, max 200 chars) для инвестора с такими интересами:\n"
            + "\n".join(interests) +
            "\nПиши на русском, без emoji, без markdown, чисто инсайт по рынку."
        )
        out = llm_call(prompt, max_tokens=120, timeout=12)
        return (out or "").strip().replace("*", "").replace("`", "")[:280]
    except Exception as e:
        print(f"[cron] ai_insight err: {e}", flush=True)
        return ""


def run_watchlist_weekly():
    """Weekly AI-digest — price drops + new hot deals + рыночный инсайт."""
    from db_schema import (get_conn, get_active_watchlists,
                            update_watchlist_notified)
    watches = get_active_watchlists()
    if not watches:
        return 0

    # Группируем по user_id
    by_user = {}
    for w in watches:
        by_user.setdefault(w["user_id"], []).append(w)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # price drops за 7 дней
            cur.execute("""
                SELECT l.id, l.area, l.building, l.bedrooms,
                       l.price, l.deal_type,
                       (SELECT MAX(p2.price) FROM price_history p2
                          WHERE p2.listing_id = l.id
                            AND p2.price_date > NOW() - INTERVAL '14 days') AS prev_price
                  FROM listings l
                 WHERE l.is_active=TRUE
                   AND (l.is_audit IS NULL OR l.is_audit=FALSE)
                   AND l.price_drop_detected=TRUE
                   AND l.updated_at > NOW() - INTERVAL '7 days'
                 LIMIT 300
            """)
            drops_all = [dict(r) for r in cur.fetchall()]
            # new hot deals
            cur.execute("""
                SELECT * FROM listings
                 WHERE is_active=TRUE
                   AND (is_audit IS NULL OR is_audit=FALSE)
                   AND is_hot_deal=TRUE
                   AND created_at > NOW() - INTERVAL '7 days'
                 ORDER BY id DESC LIMIT 500
            """)
            hot_all = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    sent = 0
    for uid, user_w in by_user.items():
        # price drops matching watchlist
        drops = [d for d in drops_all
                 if any(_watch_matches(d, w) for w in user_w)
                 and d.get("prev_price") and d["prev_price"] > 0
                 and (d["prev_price"] - (d.get("price") or 0)) / d["prev_price"] > 0.03]
        hot = [h for h in hot_all
               if any(_watch_matches(h, w) for w in user_w)]

        if not drops and not hot:
            continue  # юзеру нечего показать на этой неделе

        lines = ["📊 *Weekly Market Digest*  ·  ваш Dubai watchlist\n"]
        if drops:
            lines.append("💸 *Подешевели за неделю (>3%)*")
            for d in drops[:3]:
                pp = d["prev_price"]; np = d.get("price") or 0
                pct = (pp - np) / pp * 100
                lines.append(f"  • {d.get('building') or d.get('area') or '—'}  "
                             f"{pp//1000}K → {np//1000}K AED  ({-pct:.1f}%)")
            lines.append("")
        if hot:
            lines.append("🔥 *Hot deals в ваших районах*")
            for h in hot[:3]:
                p = h.get("price") or 0
                tag = "AED/yr" if h.get("deal_type") == "rent" else "AED"
                ps = (f"{p/1_000_000:.2f}M {tag}" if p >= 1_000_000
                      else f"{p:,} {tag}")
                lines.append(
                    f"  • {h.get('building') or h.get('area') or '—'}  ·  "
                    f"{h.get('bedrooms') if h.get('bedrooms') is not None else '—'}BR  ·  {ps}"
                )
            lines.append("")
        # AI insight
        seed_listings = (drops + hot)[:5]
        if seed_listings:
            insight = _ai_insight_for_user(seed_listings)
            if insight:
                lines.append(f"💡 *Инсайт недели*\n  _{insight}_")
        lines.append("\n_/menu — открыть бот · /watch — управление подписками_")
        _send(uid, "\n".join(lines))
        sent += 1
        for w in user_w:
            try: update_watchlist_notified(w["id"], weekly=True)
            except Exception: pass

    return sent


def watchlist_daily_loop():
    """09:00 GST = 05:00 UTC ежедневно."""
    last_run_day = None
    while True:
        try:
            now = datetime.utcnow()
            if now.hour == 5 and last_run_day != now.date():
                n = run_watchlist_daily()
                print(f"[cron] watchlist daily: sent {n} digests", flush=True)
                last_run_day = now.date()
        except Exception as e:
            print(f"[cron] watchlist daily error: {e}", flush=True)
            traceback.print_exc()
        time.sleep(15 * 60)


def watchlist_weekly_loop():
    """Воскресенье 10:00 GST = 06:00 UTC."""
    last_run_week = None
    while True:
        try:
            now = datetime.utcnow()
            week_key = (now.year, now.isocalendar()[1])
            if now.weekday() == 6 and now.hour == 6 and last_run_week != week_key:
                n = run_watchlist_weekly()
                print(f"[cron] watchlist weekly: sent {n} ai digests", flush=True)
                last_run_week = week_key
        except Exception as e:
            print(f"[cron] watchlist weekly error: {e}", flush=True)
            traceback.print_exc()
        time.sleep(30 * 60)


# ── Entry point ────────────────────────────────────────────────────────────────
def start_all():
    threading.Thread(target=alerts_loop, daemon=True).start()
    threading.Thread(target=digest_loop, daemon=True).start()
    threading.Thread(target=rebenchmark_loop, daemon=True).start()
    threading.Thread(target=photo_dedup_loop, daemon=True).start()
    threading.Thread(target=watchlist_daily_loop, daemon=True).start()
    threading.Thread(target=watchlist_weekly_loop, daemon=True).start()
    start_health_server(port=int(os.environ.get("PORT", "8080")))
    print("[cron] All workers started (including v55 watchlist).")


if __name__ == "__main__":
    print(_digest_text())
