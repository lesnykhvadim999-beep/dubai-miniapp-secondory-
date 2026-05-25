# -*- coding: utf-8 -*-
"""
hourly_report.py — Ежечасный отчёт об экосистеме для Вадима.

Отправляет в Telegram (ADMIN_ID) полный отчёт:
  • Парсинг: по каждому каналу — сколько сообщений, новых, дублей, в аудит
  • Постинг: сколько постов отправлено в каналы
  • База: чистые объявления, staging, аудит
  • Качество парсинга (последний отчёт)
  • Статус экосистемы и известные проблемы
"""
import os
import requests
from datetime import datetime, timezone, timedelta

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID  = int(os.environ.get("ADMIN_ID", "0"))
API       = f"https://api.telegram.org/bot{BOT_TOKEN}"

DUBAI = timezone(timedelta(hours=4))

# Маппинг chat_id → понятное имя канала
CHANNEL_NAMES = {
    "1781686176": "@flipluxproperty",
    "1125918023": "@dubaidirectorydubilook",
    "2187754007": "@secondary_dubai",
}

# Маппинг channel slug → chat_id (для sync_log)
CHANNEL_SLUGS = {
    "flipluxproperty":                  "1781686176",
    "dubairealestatedirectorydubilook": "1125918023",
    "secondary_dubai":                  "2187754007",
}

CHANNEL_DISPLAY = {
    "flipluxproperty":                  "FlipLux",
    "dubairealestatedirectorydubilook": "DubaiDirectory",
    "secondary_dubai":                  "SecondaryDubai",
}


def _send(text: str) -> None:
    if not ADMIN_ID or not BOT_TOKEN:
        print("[hourly_report] No ADMIN_ID or BOT_TOKEN — skipping send")
        return
    try:
        resp = requests.post(
            f"{API}/sendMessage",
            json={
                "chat_id": ADMIN_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if not resp.ok:
            print(f"[hourly_report] Send failed: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        print(f"[hourly_report] Send error: {e}")


def _num(n, zero="0") -> str:
    """Форматируем число, ноль показываем как zero."""
    if n is None:
        return zero
    return f"{int(n):,}".replace(",", " ")


def build_report(period_hours: int = 1) -> str:
    from db_schema import get_conn
    now_utc = datetime.now(timezone.utc)
    now_dubai = datetime.now(DUBAI)
    since = now_utc - timedelta(hours=period_hours)

    conn = get_conn()
    lines = []

    try:
        with conn.cursor() as cur:

            # ── 1. ПАРСИНГ по каналам ──────────────────────────────────────────
            lines.append(
                f"📡 <b>ПАРСИНГ — последн. {period_hours}ч</b>  "
                f"<i>{now_dubai.strftime('%H:%M Dubai · %d.%m.%Y')}</i>"
            )
            lines.append("")

            total_parsed = total_new = total_dupes = total_errors = 0
            channel_has_data = False

            for slug, display in CHANNEL_DISPLAY.items():
                cur.execute("""
                    SELECT
                        COALESCE(SUM(messages_parsed), 0) AS parsed,
                        COALESCE(SUM(new_listings),    0) AS new_l,
                        COALESCE(SUM(duplicates),      0) AS dupes,
                        COALESCE(SUM(errors),          0) AS errs,
                        COUNT(*) AS cycles,
                        MAX(synced_at) AS last_sync,
                        MAX(last_message_id::bigint) AS last_id
                    FROM sync_log
                    WHERE channel = %s
                      AND synced_at >= %s
                """, (slug, since))
                r = cur.fetchone()

                # Сколько ушло в аудит за период (из listings_staging)
                chat_id = CHANNEL_SLUGS.get(slug, "")
                audit_cnt = 0
                if chat_id:
                    cur.execute("""
                        SELECT COUNT(*) AS cnt
                        FROM listings_staging
                        WHERE telegram_chat_id = %s
                          AND is_audit = TRUE
                          AND created_at >= %s
                    """, (chat_id, since))
                    a = cur.fetchone()
                    audit_cnt = int(a["cnt"] or 0)

                parsed = int(r["parsed"] or 0)
                new_l  = int(r["new_l"]  or 0)
                dupes  = int(r["dupes"]  or 0)
                errs   = int(r["errs"]   or 0)
                cycles = int(r["cycles"] or 0)
                last_sync = r["last_sync"]
                last_id   = r["last_id"] or 0

                total_parsed += parsed
                total_new    += new_l
                total_dupes  += dupes
                total_errors += errs

                # Статус иконка
                if errs > 0:
                    icon = "❌"
                elif parsed == 0:
                    icon = "😴"
                elif new_l > 0:
                    icon = "✅"
                else:
                    icon = "⚪"

                last_sync_str = ""
                if last_sync:
                    last_local = last_sync.replace(tzinfo=timezone.utc).astimezone(DUBAI)
                    last_sync_str = last_local.strftime("%H:%M")

                lines.append(
                    f"{icon} <b>{display}</b>  "
                    f"[{_num(cycles)} цикл · {_num(parsed)} сообщ]"
                )
                lines.append(
                    f"   ✨ Новых чистых: <b>{_num(new_l)}</b>  "
                    f"🔎 На аудит: <b>{_num(audit_cnt)}</b>  "
                    f"📋 Дублей: {_num(dupes)}"
                )
                if errs:
                    lines.append(f"   ⚠️ Ошибок: {_num(errs)}")
                if last_sync_str:
                    lines.append(f"   🕐 Последн. синк: {last_sync_str}  |  last_id: {_num(last_id)}")
                lines.append("")
                channel_has_data = True

            if not channel_has_data:
                lines.append("  <i>Данных нет</i>")
                lines.append("")

            # Итого по парсингу
            lines.append(
                f"📊 <b>Итого за {period_hours}ч:</b>  "
                f"Сообщ: {_num(total_parsed)} · "
                f"Новых: <b>{_num(total_new)}</b> · "
                f"Дублей: {_num(total_dupes)}"
            )
            lines.append("")

            # ── 2. ПОСТИНГ ────────────────────────────────────────────────────
            lines.append("📤 <b>ПОСТИНГ</b>")

            cur.execute("""
                SELECT COUNT(*) AS cnt,
                       MIN(created_at) AS first_p,
                       MAX(created_at) AS last_p
                FROM personal_posts_log
                WHERE created_at >= %s
                  AND status = 'success'
            """, (since,))
            pp = cur.fetchone()
            pp_cnt = int(pp["cnt"] or 0)

            cur.execute("""
                SELECT COUNT(*) AS cnt
                FROM personal_posts_log
                WHERE DATE(created_at AT TIME ZONE 'UTC' + INTERVAL '4 hours') =
                      DATE(%s AT TIME ZONE 'UTC' + INTERVAL '4 hours')
                  AND status = 'success'
            """, (now_utc,))
            pp_today = cur.fetchone()
            pp_today_cnt = int(pp_today["cnt"] or 0)

            last_post_str = ""
            if pp["last_p"]:
                lp = pp["last_p"].replace(tzinfo=timezone.utc).astimezone(DUBAI)
                last_post_str = lp.strftime("%H:%M")

            lines.append(
                f"  📌 FlipLux (@flipluxproperty):  "
                f"за {period_hours}ч — <b>{_num(pp_cnt)}</b>  |  "
                f"сегодня — <b>{_num(pp_today_cnt)}</b>/8"
            )
            if last_post_str:
                lines.append(f"     ↳ Последний пост в {last_post_str} Dubai")
            if pp_cnt == 0:
                lines.append(f"     ⚠️ Постов не было за последн. {period_hours}ч!")
            lines.append("")

            # ── 3. БАЗА ДАННЫХ — сегодня ──────────────────────────────────────
            lines.append("🗄️ <b>БАЗА ДАННЫХ</b>")

            cur.execute("""
                SELECT
                  (SELECT COUNT(*) FROM listings WHERE is_active=TRUE
                     AND (is_audit IS NULL OR is_audit=FALSE)) AS visible_total,
                  (SELECT COUNT(*) FROM listings WHERE is_active=TRUE AND is_audit=TRUE) AS audit_total,
                  (SELECT COUNT(*) FROM listings
                     WHERE created_at >= NOW() - INTERVAL '24 hours'
                       AND (is_audit IS NULL OR is_audit=FALSE)) AS new_today,
                  (SELECT COUNT(*) FROM listings_staging
                     WHERE is_audit IS NULL) AS staging_queue,
                  (SELECT COUNT(*) FROM listings_staging
                     WHERE is_audit = TRUE
                       AND created_at >= NOW() - INTERVAL '24 hours') AS audit_today
            """)
            db = cur.fetchone()

            lines.append(
                f"  📦 Всего видимых: <b>{_num(db['visible_total'])}</b>  "
                f"| 🚫 В аудите: {_num(db['audit_total'])}"
            )
            lines.append(
                f"  ✨ Новых за 24ч (чистых): <b>{_num(db['new_today'])}</b>  "
                f"| 🔍 Ушло в аудит за 24ч: {_num(db['audit_today'])}"
            )
            lines.append(
                f"  ⏳ В очереди staging: {_num(db['staging_queue'])}"
            )
            lines.append("")

            # ── 4. КАЧЕСТВО ПАРСИНГА ──────────────────────────────────────────
            cur.execute("""
                SELECT overall_pct, area_pct, building_pct, type_pct, deal_pct, price_pct,
                       clean_streak, sample_size, ts
                FROM parser_quality_log
                ORDER BY ts DESC
                LIMIT 1
            """)
            q = cur.fetchone()
            if q:
                q_time = q["ts"]
                if hasattr(q_time, 'replace'):
                    q_time = q_time.replace(tzinfo=timezone.utc).astimezone(DUBAI).strftime("%H:%M %d.%m")
                overall = float(q["overall_pct"] or 0)
                streak  = int(q["clean_streak"] or 0)
                q_icon  = "✅" if overall >= 80 else ("⚠️" if overall >= 60 else "❌")
                lines.append(
                    f"🔬 <b>КАЧЕСТВО ПАРСИНГА</b>  "
                    f"<i>(замер {q_time}, n={q['sample_size']})</i>"
                )
                lines.append(
                    f"  {q_icon} Общее: <b>{overall:.0f}%</b>  "
                    f"| Стрик чистых: {'🔥' if streak>=3 else ''}{streak}"
                )
                lines.append(
                    f"  Район: {float(q['area_pct'] or 0):.0f}%  "
                    f"Здание: {float(q['building_pct'] or 0):.0f}%  "
                    f"Тип: {float(q['type_pct'] or 0):.0f}%  "
                    f"Сделка: {float(q['deal_pct'] or 0):.0f}%  "
                    f"Цена: {float(q['price_pct'] or 0):.0f}%"
                )
            else:
                lines.append("🔬 <b>КАЧЕСТВО:</b> нет данных")
            lines.append("")

            # ── 5. СТАТУС ЭКОСИСТЕМЫ ──────────────────────────────────────────
            lines.append("⚙️ <b>СТАТУС СИСТЕМЫ</b>")
            lines.append(f"  🔄 Частота парсинга: каждые 30 минут")

            # Проверяем — не зависли ли каналы (нет активности > 60 мин)
            issues = []
            for slug, display in CHANNEL_DISPLAY.items():
                cur.execute("""
                    SELECT MAX(synced_at) as last_sync,
                           MAX(messages_parsed) FILTER (
                             WHERE synced_at >= NOW() - INTERVAL '2 hours'
                           ) AS recent_parsed
                    FROM sync_log WHERE channel = %s
                """, (slug,))
                sr = cur.fetchone()
                if sr["last_sync"]:
                    age_min = (now_utc - sr["last_sync"].replace(tzinfo=timezone.utc)).total_seconds() / 60
                    if age_min > 70:
                        issues.append(f"⚠️ {display}: нет синка уже {age_min:.0f} мин!")
                else:
                    issues.append(f"⚠️ {display}: никогда не синкался!")

            # Проверяем staging очередь (если > 500 — предупреждение)
            if db["staging_queue"] > 500:
                issues.append(f"⚠️ Очередь staging переполнена: {db['staging_queue']} записей")

            if issues:
                for issue in issues:
                    lines.append(f"  {issue}")
            else:
                lines.append("  ✅ Всё работает штатно")

            lines.append("")
            lines.append(
                f"<i>Следующий отчёт через 1 час · Resale-bot ecosystem</i>"
            )

    finally:
        conn.close()

    return "\n".join(lines)


def send_hourly_report(period_hours: int = 1) -> None:
    try:
        text = build_report(period_hours)
        print("[hourly_report] ─────────────────────────────────")
        print(text)
        print("[hourly_report] ─────────────────────────────────")
        _send(text)
    except Exception as e:
        import traceback
        print(f"[hourly_report] ERROR: {e}")
        traceback.print_exc()
        # Шлём урезанный алерт об ошибке
        try:
            _send(f"❌ <b>Hourly report error:</b> <code>{str(e)[:200]}</code>")
        except Exception:
            pass


if __name__ == "__main__":
    # Запуск вручную для теста
    print(build_report(period_hours=1))
