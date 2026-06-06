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


# ── 6. Buildings backfill (daily, v128) ────────────────────────────────────────
def buildings_backfill_loop():
    """UPSERT справочник buildings из listings ежедневно 03:30 UTC.

    Нужен для:
      • быстрого fuzzy-поиска зданий по aliases (RERA / DLD short-names)
      • экспоненциального матчинга в audit-pipeline
      • справочника developer/area для UI
    """
    last_run_day = None
    while True:
        try:
            now = datetime.utcnow()
            if now.hour == 3 and now.minute >= 30 and last_run_day != now.date():
                print("[cron] Running buildings backfill...")
                import subprocess
                r = subprocess.run(["python", "_buildings_backfill.py"],
                                   cwd=os.path.dirname(__file__) or ".",
                                   check=False, timeout=600,
                                   capture_output=True, text=True)
                print("[cron] buildings_backfill out:", (r.stdout or "")[-500:])
                if r.stderr:
                    print("[cron] buildings_backfill err:", r.stderr[-500:])
                last_run_day = now.date()
        except Exception as e:
            print(f"[cron] buildings_backfill error: {e}")
        time.sleep(20 * 60)


def digest_loop():
    """Sends daily digest to admin at ~09:00 UTC.
    Audit 2026-06-05: при рестарте после 09:00 не пере-шлём сегодняшний дайджест."""
    _now0 = datetime.utcnow()
    last_sent_day = _now0.date() if _now0.hour >= 9 else None
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
            traceback.print_exc()
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


# ── 8. Hourly ecosystem report ────────────────────────────────────────────────
def hourly_report_loop():
    """Каждый час (в :02) шлёт Вадиму полный отчёт об экосистеме.
    Audit 2026-06-05: при рестарте не пере-шлём этот час + exponential backoff на ошибках."""
    _now0 = datetime.utcnow()
    # При старте предполагаем что этот час уже был обработан (избегаем дубля при рестарте)
    last_run_hour = (_now0.date(), _now0.hour) if _now0.minute >= 2 else None
    consecutive_errors = 0
    while True:
        try:
            now = datetime.utcnow()
            hour_key = (now.date(), now.hour)
            if last_run_hour != hour_key and now.minute >= 2:
                from hourly_report import send_hourly_report
                send_hourly_report(period_hours=1)
                print(f"[cron] hourly ecosystem report sent", flush=True)
                last_run_hour = hour_key
                consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            print(f"[cron] hourly report error #{consecutive_errors}: {e}", flush=True)
            traceback.print_exc()
        # Exponential backoff: 5,10,20,30,60 мин max — не DoS-им админа на PG-down
        if consecutive_errors == 0:
            time.sleep(5 * 60)
        else:
            time.sleep(min(60, 5 * (2 ** min(consecutive_errors - 1, 4))) * 60)


# ── 7. Parser-quality monitor (task #127) ──────────────────────────────────────
def parser_quality_monitor_loop():
    """Каждые 3 часа: sample 100 listings → LLM audit → log в parser_quality_log.
    Цель — держать ≥95% чистоты подряд 3 дня (24 чека)."""
    interval = int(os.environ.get("QPM_INTERVAL", str(3 * 3600)))
    while True:
        try:
            import parser_quality_monitor as pqm
            res = pqm.run_once()
            print(f"[cron] parser-quality: {res}", flush=True)
        except Exception as e:
            print(f"[cron] parser-quality err: {e}", flush=True)
            traceback.print_exc()
        time.sleep(interval)


# ── 8. Realtime accuracy monitor (task #56) ────────────────────────────────────
def realtime_accuracy_loop():
    """Каждые 60с: новые listings → second LLM verify → flag если low confidence."""
    interval = int(os.environ.get("RT_ACCURACY_INTERVAL", "60"))
    try:
        import realtime_accuracy_daemon as rtd
        rtd.ensure_schema()
    except Exception as e:
        print(f"[cron] rt-accuracy init err: {e}", flush=True)
    while True:
        try:
            import realtime_accuracy_daemon as rtd
            stats = rtd.run_cycle()
            if stats.get("scanned"):
                print(f"[cron] rt-accuracy: {stats}", flush=True)
        except Exception as e:
            print(f"[cron] rt-accuracy err: {e}", flush=True)
            traceback.print_exc()
        time.sleep(interval)


# ── Entry point ────────────────────────────────────────────────────────────────
# ── New scheduled jobs (added 2026-05-29) ─────────────────────────────────
def _run_script_periodic(script_name: str, interval_sec: int, label: str):
    """Generic loop: subprocess.run python script every N seconds."""
    import subprocess
    while True:
        try:
            time.sleep(interval_sec)
            here = os.path.dirname(os.path.abspath(__file__))
            print(f"[cron:{label}] running {script_name}")
            subprocess.run(["python", os.path.join(here, script_name)],
                           timeout=600, check=False)
        except Exception as e:
            print(f"[cron:{label}] error: {e}")


def saved_searches_loop():
    """Hourly — new matches for user-saved searches."""
    _run_script_periodic("_check_saved_searches_alerts.py", 3600, "saved_searches")


def price_alerts_loop():
    """Every 6 h — price-drop alerts."""
    _run_script_periodic("_check_price_alerts.py", 6 * 3600, "price_alerts")


def cross_source_loop():
    """Hourly — duplicate detection + merge."""
    _run_script_periodic("_cross_source_verification.py", 3600, "cross_source")


def daily_backup_loop():
    """Daily — pg_dump + R2 upload."""
    _run_script_periodic("_daily_backup.py", 24 * 3600, "backup")


def daily_backup_github_loop():
    """Daily at 03:00 UTC — pg_dump + GitHub Releases upload (improvement #6).

    Sleeps until next 03:00 UTC, then runs `_backup_to_github.py`. On error,
    sleeps 1h and retries (rather than skipping a full day).
    """
    import subprocess
    while True:
        time.sleep(_wait_until_utc(3, 0))
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            script = os.path.join(here, "_backup_to_github.py")
            if os.path.exists(script):
                print("[cron:backup_github] running", flush=True)
                subprocess.run(["python", script], timeout=2400, check=False)
            else:
                print(f"[cron:backup_github] script missing: {script}",
                      flush=True)
                time.sleep(3600)
        except Exception as e:
            print(f"[cron:backup_github] error: {e}", flush=True)
            time.sleep(3600)


def channel_quality_loop():
    """Daily — per-channel quality digest."""
    _run_script_periodic("_channel_quality.py", 24 * 3600, "channel_quality")


def _wait_until_utc(hour: int, minute: int = 0) -> int:
    """Seconds until next UTC clock matching hour:minute."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds())


def daily_digest_loop():
    """Daily 05:00 UTC = 09:00 Dubai — DLD digest to @vadim_admin_bot."""
    import subprocess
    while True:
        time.sleep(_wait_until_utc(5, 0))
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            digest = os.path.join(os.path.dirname(here),
                                   "dubai-dld-analytics-bot-main",
                                   "daily_digest.py")
            if os.path.exists(digest):
                print("[cron:daily_digest] running")
                subprocess.run(["python", digest], timeout=300, check=False)
        except Exception as e:
            print(f"[cron:daily_digest] error: {e}")


def weekly_aliases_loop():
    """Monday 09:00 UTC = 13:00 Dubai — auto-aliases proposals."""
    import subprocess
    from datetime import datetime, timezone
    while True:
        time.sleep(_wait_until_utc(9, 0))
        # Only run on Mondays (weekday 0)
        if datetime.now(timezone.utc).weekday() != 0:
            continue
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            print("[cron:weekly_aliases] running")
            subprocess.run(["python", os.path.join(here, "_auto_aliases.py")],
                           timeout=300, check=False)
        except Exception as e:
            print(f"[cron:weekly_aliases] error: {e}")


def lead_followup_loop():
    """Every 30 min — auto-followup leads at 24/48/72h."""
    import subprocess
    while True:
        time.sleep(30 * 60)
        try:
            script = ("C:/Projects/lead-bot/Lead-bot/telegram-bot/"
                      "_lead_followup_cron.py")
            if os.path.exists(script):
                print("[cron:lead_followup] running")
                subprocess.run(["python", script], timeout=300, check=False)
        except Exception as e:
            print(f"[cron:lead_followup] error: {e}")


# ── F4 (2026-06-06): stale-parser watchdog ─────────────────────────────────
def stale_parser_loop():
    """Каждые 6ч проверяем когда последний раз парсер вставил новый листинг.
    Если >48ч без new_listings → шлём админу alert. Защищает от тихого падения
    парсера (например content_hash отбивает всё или Telethon не получает posts)."""
    last_alert_day = None
    consecutive_alerts = 0
    while True:
        try:
            from db_schema import get_conn
            now = datetime.utcnow()
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT MAX(created_at) AS last_new,
                               EXTRACT(EPOCH FROM (NOW() - MAX(created_at)))/3600 AS hours_ago
                        FROM listings WHERE is_active=TRUE
                    """)
                    r = cur.fetchone()
            finally:
                conn.close()
            hours_ago = float(r["hours_ago"] or 0)
            if hours_ago >= 48 and ADMIN_ID and last_alert_day != now.date():
                msg = (f"⚠️ *Парсер тихо встал?*\n\n"
                       f"Последний новый листинг: *{int(hours_ago)} ч назад*\n"
                       f"({r['last_new']})\n\n"
                       f"Возможные причины:\n"
                       f"• content_hash отбивает всё (canon backfill\\?)\n"
                       f"• Telethon не получает сообщения\n"
                       f"• Все каналы тихие\n\n"
                       f"Проверь sync\\_log и Telethon.")
                _send(ADMIN_ID, msg)
                consecutive_alerts += 1
                last_alert_day = now.date()
                print(f"[cron] stale-parser alert sent (hours_ago={int(hours_ago)})", flush=True)
            elif hours_ago < 24:
                consecutive_alerts = 0
        except Exception as e:
            print(f"[cron] stale_parser error: {e}", flush=True)
            traceback.print_exc()
        time.sleep(6 * 3600)  # 6 hours


def _safe_thread_start(target, name=None):
    """Launch a daemon thread, never raise. Returns Thread or None."""
    try:
        t = threading.Thread(target=target, name=name or target.__name__, daemon=True)
        t.start()
        return t
    except Exception as e:
        print(f"[cron] failed to start {name or target.__name__}: {e}", flush=True)
        return None


def start_all():
    """Launch all cron daemon threads. NEVER blocks — always returns quickly.

    Each thread is daemon=True so they die with the main process. Each loop
    body has try/except so single failure doesn't kill the thread.
    Health server is launched LAST and never blocks (binds in a daemon thread).
    """
    started = 0
    for fn in (alerts_loop, digest_loop, rebenchmark_loop, photo_dedup_loop,
               buildings_backfill_loop, watchlist_daily_loop,
               watchlist_weekly_loop, parser_quality_monitor_loop,
               hourly_report_loop, realtime_accuracy_loop,
               # New jobs (29.05.2026)
               saved_searches_loop, price_alerts_loop, cross_source_loop,
               daily_backup_loop, daily_backup_github_loop,
               channel_quality_loop, daily_digest_loop,
               weekly_aliases_loop, lead_followup_loop,
               # F4 (2026-06-06): stale parser watchdog
               stale_parser_loop):
        if _safe_thread_start(fn):
            started += 1

    # Health server: only start if HEALTH_PORT explicitly set (to avoid
    # conflict with start_metrics_server, which already binds $PORT).
    health_port_env = os.environ.get("HEALTH_PORT")
    if health_port_env:
        try:
            start_health_server(port=int(health_port_env))
        except Exception as e:
            print(f"[cron] health server skipped: {e}", flush=True)
    print(f"[cron] All workers started: {started}/18 daemon threads.", flush=True)


if __name__ == "__main__":
    print(_digest_text())
