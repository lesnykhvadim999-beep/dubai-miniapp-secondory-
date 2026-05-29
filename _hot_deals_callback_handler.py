"""Hot Deals callback handler — polling @vadim_admin_bot для inline-кнопок.

Слушает callback_query от _hot_deals_share.py:
  share:{id}  → публикует карточку в @flipluxproperty через FLIPLUX_BOT_TOKEN
  skip:{id}   → ничего, удаляет inline-клавиатуру

Env:
  ADMIN_BOT_TOKEN     — токен @vadim_admin_bot (polling)
  FLIPLUX_BOT_TOKEN   — токен бота-публикатора в @flipluxproperty (опционально:
                        если не задан — handler отвечает "не настроено", карточку не публикует)
  FLIPLUX_CHANNEL     — username канала (default "@flipluxproperty")
  DATABASE_URL        — Railway postgres

Запуск:
  py -3 _hot_deals_callback_handler.py
  (Ctrl+C для остановки.)
"""
from __future__ import annotations
import os
import sys
import time
import json
import urllib.parse
import urllib.request

import psycopg2

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_URL = os.environ.get(
    "DATABASE_URL",
    "REDACTED_DSN_USE_DATABASE_URL_ENV",
)

ADMIN_BOT_TOKEN = os.environ.get(
    "ADMIN_BOT_TOKEN",
    "8742147923:AAFkeCmIKkZSNgOuKeaLXcY4oUQztFdnrKQ",
).strip()
FLIPLUX_BOT_TOKEN = os.environ.get("FLIPLUX_BOT_TOKEN", "").strip()
FLIPLUX_CHANNEL = os.environ.get("FLIPLUX_CHANNEL", "@flipluxproperty").strip()

API = f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}"


def _post(url: str, payload: dict, timeout: int = 15) -> dict:
    data = urllib.parse.urlencode({
        k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
        for k, v in payload.items()
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_updates(offset: int) -> list[dict]:
    url = f"{API}/getUpdates?timeout=25&offset={offset}&allowed_updates=%5B%22callback_query%22%5D"
    try:
        with urllib.request.urlopen(url, timeout=35) as r:
            j = json.loads(r.read().decode("utf-8"))
        if not j.get("ok"):
            print(f"[cb] getUpdates not ok: {j}", flush=True)
            return []
        return j.get("result", [])
    except Exception as e:
        print(f"[cb] getUpdates err: {e}", flush=True)
        time.sleep(3)
        return []


def answer_callback(cb_id: str, text: str = "", alert: bool = False) -> None:
    _post(f"{API}/answerCallbackQuery", {
        "callback_query_id": cb_id,
        "text": text[:200],
        "show_alert": "true" if alert else "false",
    })


def edit_reply_markup_clear(chat_id: int, message_id: int) -> None:
    _post(f"{API}/editMessageReplyMarkup", {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": []},
    })


def edit_message_caption(chat_id: int, message_id: int, suffix: str) -> None:
    """Дописывает строку статуса в самом сообщении (если возможно)."""
    _post(f"{API}/editMessageReplyMarkup", {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": [[{"text": suffix, "callback_data": "noop"}]]},
    })


def fetch_listing(listing_id: int) -> dict | None:
    sql = """
        SELECT id, building, area, bedrooms, price, size_sqft, view,
               confidence_score, price_vs_market_percent
        FROM listings WHERE id = %s
    """
    with psycopg2.connect(DB_URL) as conn, conn.cursor() as cur:
        cur.execute(sql, (listing_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c.name for c in cur.description]
    return dict(zip(cols, row))


def format_channel_post(row: dict) -> str:
    price_m = (row["price"] or 0) / 1_000_000.0
    pct = row.get("price_vs_market_percent")
    pct_str = f"{pct:+.1f}%" if pct is not None else None
    br = row.get("bedrooms")
    br_str = f"{br}BR" if br not in (None, 0) else "Studio"
    sqft = row.get("size_sqft")
    sqft_str = f"{sqft} sqft" if sqft else None
    view = row.get("view")

    parts = [
        "🔥 <b>Hot deal</b>",
        f"{row.get('area') or ''} • {row.get('building') or ''}".strip(" •"),
    ]
    detail_bits = [br_str]
    if sqft_str:
        detail_bits.append(sqft_str)
    if view:
        detail_bits.append(str(view))
    parts.append(" • ".join(detail_bits))
    line = f"💰 <b>{price_m:.2f}M AED</b>"
    if pct_str:
        line += f" ({pct_str} vs market)"
    parts.append(line)
    parts.append("")
    parts.append("Vadim Realty • RERA BRN 65011")
    return "\n".join(parts)


def publish_to_fliplux(row: dict) -> tuple[bool, str]:
    if not FLIPLUX_BOT_TOKEN:
        return False, "FLIPLUX_BOT_TOKEN не задан"
    url = f"https://api.telegram.org/bot{FLIPLUX_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": FLIPLUX_CHANNEL,
        "text": format_channel_post(row),
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode("utf-8"))
        if j.get("ok"):
            return True, "опубликовано"
        return False, j.get("description") or "Telegram отказал"
    except Exception as e:
        return False, str(e)


def handle_callback(cb: dict) -> None:
    cb_id = cb.get("id")
    data = cb.get("data") or ""
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")

    if not (cb_id and data and chat_id and message_id):
        return

    if data == "noop":
        answer_callback(cb_id, "")
        return

    try:
        action, sid = data.split(":", 1)
        listing_id = int(sid)
    except Exception:
        answer_callback(cb_id, "Битый callback")
        return

    if action == "skip":
        edit_message_caption(chat_id, message_id, "🚫 пропущено")
        answer_callback(cb_id, "Скип")
        print(f"[cb] skip listing #{listing_id}", flush=True)
        return

    if action == "share":
        row = fetch_listing(listing_id)
        if not row:
            answer_callback(cb_id, f"Listing #{listing_id} не найден", alert=True)
            return
        ok, info = publish_to_fliplux(row)
        if ok:
            edit_message_caption(chat_id, message_id, f"✅ опубликовано в {FLIPLUX_CHANNEL}")
            answer_callback(cb_id, f"Готово: {FLIPLUX_CHANNEL}")
            print(f"[cb] shared listing #{listing_id} → {FLIPLUX_CHANNEL}", flush=True)
        else:
            edit_message_caption(chat_id, message_id, f"⚠️ ошибка: {info[:50]}")
            answer_callback(cb_id, f"Ошибка: {info[:150]}", alert=True)
            print(f"[cb] FAIL listing #{listing_id}: {info}", flush=True)
        return

    answer_callback(cb_id, f"Неизвестное действие: {action}")


def main() -> int:
    if not ADMIN_BOT_TOKEN:
        print("ADMIN_BOT_TOKEN не задан", flush=True)
        return 1
    print(f"[cb] start polling, fliplux_token={'set' if FLIPLUX_BOT_TOKEN else 'MISSING'}", flush=True)
    offset = 0
    while True:
        try:
            updates = get_updates(offset)
        except KeyboardInterrupt:
            print("[cb] stopped by user", flush=True)
            return 0
        for u in updates:
            offset = max(offset, int(u["update_id"]) + 1)
            cb = u.get("callback_query")
            if cb:
                try:
                    handle_callback(cb)
                except Exception as e:
                    print(f"[cb] handler err: {e}", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
