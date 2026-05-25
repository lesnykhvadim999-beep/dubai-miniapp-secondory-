"""
telethon_parser.py — Telegram channel parser using Telethon.
Implements ТЗ blocks 3.1-3.4:
- Historical parsing from 01.01.2026
- Incremental mode (only new messages)
- Image/media group handling
- Save state logic

ИСПРАВЛЕНИЯ (2026-05-12):
БАГ A: get_last_parsed_message_id() брала MAX из sync_log, где secondary_dubai
       и dubilook всегда писали last_message_id=0 (канал падал до итерации).
       Исправлено: get_real_last_message_id() смотрит В САМОЙ БД listings.

БАГ B: _needs_backfill() возвращала False если хоть один канал имел запись в
       sync_log — даже если у него там last_message_id=0. Из-за этого
       secondary_dubai/dubilook никогда не получали backfill.
       Исправлено: проверяем реальный max message_id из listings по chat_id.

БАГ C: При errors=1 и parsed=0 sync_log записывал last_message_id=0,
       затирая любой прогресс. Следующий цикл начинал с 0.
       Исправлено: если stats["last_id"]==0, берём предыдущий max из sync_log.

БАГ D: incremental с min_id=last_id НЕ включает само сообщение с id=last_id
       (Telethon: min_id строго больше). При errors отставание накапливается.
       Исправлено: min_id = last_id (Telethon использует min_id как >last_id).
"""
import os
import asyncio
import threading
import requests as _requests
from datetime import datetime, timezone, timedelta

from parser_engine import parse_message, is_spam, ai_parse_listing, merge_ai_with_parsed
from db_schema import upsert_listing, save_images, log_sync, get_last_parsed_message_id, get_conn

# ── Config ────────────────────────────────────────────────────────────────────
TELETHON_API_ID   = int(os.environ.get("TELEGRAM_API_ID", "39535588"))
TELETHON_API_HASH = os.environ.get("TELEGRAM_API_HASH", "e48ee11a80b4ede45dbe097cfbf916ff")
SESSION_STRING    = os.environ.get("SESSION_STRING", "")

# Channels to parse — extend by adding @username strings here.
# The parser will resolve chat_id at runtime if not in CHANNEL_CHAT_IDS.
# TO ADD A CHANNEL: just paste its @username below. Restart bot.
CHANNELS = [
    "flipluxproperty",                      # сторонний канал листингов (мы туда постим 1 пост, но парсим чужие объявления)
    "dubairealestatedirectorydubilook",
    "secondary_dubai",
    # Add more channels here:
    # "channel_username_1",
    # "channel_username_2",
]

# chat_id → channel name mapping (для get_real_last_message_id).
# New channels — chat_id will be resolved at runtime, missing entries are fine.
CHANNEL_CHAT_IDS = {
    "flipluxproperty":                     "1781686176",
    "dubairealestatedirectorydubilook":    "1125918023",
    "secondary_dubai":                     "2187754007",
}

BACKFILL_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
PARSE_INTERVAL = 30 * 60  # 30 minutes
BATCH_SIZE = 200           # Увеличено для быстрого catch-up после простоя
PHOTO_DOWNLOAD = True      # Download and save photos

BOT_TOKEN_UPLOAD = os.environ.get("RESALE_BOT_TOKEN", "")
BOT_API_UPLOAD   = f"https://api.telegram.org/bot{BOT_TOKEN_UPLOAD}"
# Используем Saved Messages (chat_id=user_id) вместо admin чата
# чтобы фото не мелькали в основном чате
# Для получения своего Saved Messages chat_id = тот же что и user_id
UPLOAD_CHAT_ID   = int(os.environ.get("PHOTO_BUFFER_CHAT_ID", "353806371"))


# ── БАГ A FIX: реальный последний message_id из listings ─────────────────────
def get_real_last_message_id(channel: str) -> int:
    """
    Возвращает MAX(telegram_message_id) из таблицы listings для данного канала.
    Надёжнее чем sync_log, который мог писать 0 при ошибках.
    """
    chat_id = CHANNEL_CHAT_IDS.get(channel)
    if not chat_id:
        return 0
    try:
        from db_schema import get_conn
        conn = get_conn()
        with conn.cursor() as cur:
            # B053: исключаем аномальные message_id > 10M (фейковые ID от retro-parser)
            cur.execute(
                "SELECT COALESCE(MAX(telegram_message_id), 0) as mid "
                "FROM listings WHERE telegram_chat_id = %s "
                "AND (telegram_message_id IS NULL OR telegram_message_id < 10000000)",
                (chat_id,)
            )
            row = cur.fetchone()
        conn.close()
        return int(row["mid"]) if row and row["mid"] else 0
    except Exception as e:
        print(f"[telethon] get_real_last_message_id error for {channel}: {e}")
        return 0


async def _upload_photo(client, msg_obj) -> str | None:
    """Download photo bytes via Telethon, upload to Bot API, return file_id."""
    try:
        photo_bytes = await client.download_media(msg_obj.photo, file=bytes)
        if not photo_bytes:
            return None
        resp = _requests.post(
            f"{BOT_API_UPLOAD}/sendPhoto",
            files={"photo": ("photo.jpg", photo_bytes, "image/jpeg")},
            data={"chat_id": str(UPLOAD_CHAT_ID), "disable_notification": "true"},
            timeout=30,
        )
        data = resp.json()
        if data.get("ok"):
            file_id = data["result"]["photo"][-1]["file_id"]
            msg_id  = data["result"]["message_id"]
            # Clean up temp message so admin chat is not flooded
            try:
                _requests.post(
                    f"{BOT_API_UPLOAD}/deleteMessage",
                    data={"chat_id": str(UPLOAD_CHAT_ID), "message_id": msg_id},
                    timeout=10,
                )
            except Exception:
                pass
            print(f"[telethon] Photo uploaded → file_id {file_id[:20]}…")
            return file_id
        else:
            print(f"[telethon] Photo upload failed: {data.get('description')}")
    except Exception as e:
        print(f"[telethon] Photo upload error: {e}")
    return None


async def _download_photo(client, message) -> list[str]:
    """Download photo from message and return Bot API file_id list."""
    if not message.photo:
        return []
    fid = await _upload_photo(client, message)
    return [fid] if fid else []


async def _get_media_group_photos(client, message) -> list[str]:
    """Get all photos from a media group (album) as Bot API file_ids."""
    if not message.grouped_id:
        return await _download_photo(client, message)
    urls = []
    try:
        chat = await message.get_chat()
        msgs = await client.get_messages(
            chat, min_id=message.id - 10, max_id=message.id + 10
        )
        for m in msgs:
            if m.grouped_id == message.grouped_id and m.photo:
                fid = await _upload_photo(client, m)
                if fid:
                    urls.append(fid)
    except Exception as e:
        print(f"[telethon] Album error: {e}")
    return urls


async def parse_channel(client, channel: str, backfill: bool = False,
                         force_from_id: int = 0):
    """
    Parse messages from a channel.

    Args:
        force_from_id: если > 0, парсим начиная с этого message_id
                       (используется для catchup после пропуска)
    """
    print(f"[telethon] Parsing @{channel} (backfill={backfill}, force_from_id={force_from_id})")

    stats = {
        "parsed": 0, "new": 0, "dupes": 0,
        "hot": 0, "errors": 0, "last_id": 0
    }

    # БАГ A + C FIX: определяем offset через реальные данные в listings
    if backfill or force_from_id == 0 and not backfill:
        if backfill:
            offset_date = BACKFILL_DATE
            min_id = 0
        else:
            # Incremental: берём реальный максимум из БД (не из sync_log!)
            real_last = get_real_last_message_id(channel)
            sync_last = get_last_parsed_message_id(channel) or 0
            # Берём максимум из обоих источников для надёжности
            min_id = max(real_last, sync_last)
            offset_date = None
            if min_id > 0:
                print(f"[telethon] Incremental from message_id={min_id} "
                      f"(db_max={real_last}, sync_log={sync_last})")
            else:
                # Нет истории — делаем backfill
                print(f"[telethon] No history for @{channel}, switching to backfill")
                offset_date = BACKFILL_DATE
                min_id = 0
    else:
        # Принудительный catchup с указанного id
        min_id = force_from_id
        offset_date = None
        print(f"[telethon] Forced catchup from message_id={min_id}")

    # Сохраняем предыдущий last_id на случай если канал упадёт (БАГ C FIX)
    prev_last_id = get_real_last_message_id(channel)

    try:
        entity = await client.get_entity(channel)
        processed_groups = set()  # Track processed media groups

        kwargs = {
            "entity": entity,
            "limit": None if backfill else BATCH_SIZE,
            "reverse": True,
        }
        if offset_date:
            kwargs["offset_date"] = offset_date
        if min_id:
            kwargs["min_id"] = min_id

        async for message in client.iter_messages(**kwargs):
            try:
                stats["parsed"] += 1

                # Text
                text = getattr(message, 'text', None) or getattr(message, 'message', None) or ""
                _cap = getattr(message, 'caption', None)
                if _cap:
                    text = _cap

                # Skip short/empty
                if len(text.strip()) < 20 and not message.photo:
                    continue

                # Date
                msg_date = message.date
                if msg_date and msg_date.tzinfo is None:
                    msg_date = msg_date.replace(tzinfo=timezone.utc)

                # Skip before backfill date
                if msg_date and msg_date < BACKFILL_DATE:
                    continue

                # Skip if spam (quick check before heavy processing)
                if is_spam(text) and not message.photo:
                    continue

                # Seller
                sender = message.sender
                seller_username = None
                if sender:
                    seller_username = getattr(sender, "username", None)

                # Photos — handle media groups (albums)
                image_urls = []
                if message.photo or message.media:
                    group_id = message.grouped_id
                    if group_id:
                        if group_id not in processed_groups:
                            processed_groups.add(group_id)
                            image_urls = await _get_media_group_photos(client, message)
                        else:
                            continue  # Already processed this album
                    else:
                        image_urls = await _download_photo(client, message)

                # Parse
                if not text.strip() and not image_urls:
                    continue

                # ── Phase 2: LLM-powered multi-listing split ─────────────────
                # Если пост содержит несколько объявлений — разбиваем через
                # Groq и парсим каждое отдельно. Каждый chunk → отдельная запись.
                from parser_engine import _is_likely_multi_listing, _llm_split_all_listings
                chunks_to_parse = None
                if _is_likely_multi_listing(text) and \
                        os.environ.get("LLM_MULTI_SPLIT_ALL", "1") != "0":
                    try:
                        chunks_to_parse = _llm_split_all_listings(text)
                        if chunks_to_parse and len(chunks_to_parse) > 1:
                            print(f"[telethon] Multi-listing: split into {len(chunks_to_parse)} chunks (msg_id={message.id})")
                    except Exception as _e:
                        print(f"[telethon] LLM split-all err: {_e}")
                        chunks_to_parse = None
                if not chunks_to_parse:
                    chunks_to_parse = [text]

                # Parse each chunk separately
                multi_saved = 0
                for chunk_idx, chunk_text in enumerate(chunks_to_parse):
                    # Image attached only to FIRST chunk (one photo per post)
                    chunk_images = image_urls if chunk_idx == 0 else None
                    # Each chunk gets a synthetic message_id offset so listing_keys differ
                    chunk_msg_id = message.id if chunk_idx == 0 else (message.id * 100 + chunk_idx)

                    parsed = parse_message(
                        text=chunk_text,
                        message_id=chunk_msg_id,
                        message_date=msg_date.isoformat() if msg_date else None,
                        chat_id=str(entity.id),
                        seller_username=seller_username,
                        image_urls=chunk_images,
                    )

                    if not parsed:
                        continue

                    # Save remaining chunks via continue-flow at end of loop
                    if chunk_idx == 0:
                        parsed_first = parsed
                        # For chunk #0, use original control flow below
                        text = chunk_text  # update text variable for downstream
                        parsed = parsed_first
                        break  # Process chunk #0 below as before
                    else:
                        # Direct save for chunks ≥ 1 (no semantic dedup, dedup via content)
                        try:
                            cid, is_new = upsert_listing(parsed)
                            if is_new:
                                multi_saved += 1
                                stats["new"] += 1
                                if parsed.get("is_hot_deal"):
                                    stats["hot"] += 1
                        except Exception as _e:
                            print(f"[telethon] multi-chunk #{chunk_idx} save err: {_e}")

                if multi_saved:
                    print(f"[telethon] Saved {multi_saved} additional chunks from msg_id={message.id}")

                if not parsed:
                    continue

                # AI classification DISABLED — rule-based parser_engine now handles
                # all field extraction (building/area/price/deal_type/property_type/
                # view/floor/bathrooms/size/furnishing) with DLD benchmark validation.
                # See parser_engine.py commits 8acb23b..675de9d for the full set of fixes.
                # If reactivation is needed, restore the block from git history.

                # === SEMANTIC DEDUP ===
                _seller = parsed.get('seller_username') or ''
                _building = parsed.get('building') or ''
                _size = parsed.get('size_sqft') or parsed.get('bua_sqft') or 0
                _area = parsed.get('area') or ''
                _price = parsed.get('price') or 0
                if _seller and _building and _size and _area:
                    try:
                        # Local re-import — bullet-proof against Railway pyc cache issues
                        from db_schema import get_conn as _get_conn_local
                        _conn = _get_conn_local()
                        with _conn.cursor() as _cur:
                            _cur.execute(
                                "SELECT id, price FROM listings WHERE seller_username=%s AND building=%s AND area=%s AND (size_sqft=%s OR bua_sqft=%s) AND status='active' ORDER BY created_at DESC LIMIT 1",
                                (_seller, _building, _area, _size, _size)
                            )
                            _row = _cur.fetchone()
                        _conn.close()
                        if _row:
                            _old_price = _row["price"] or 0
                            if not _price or not _old_price or _price >= _old_price:
                                stats["duplicates"] += 1
                                continue
                    except Exception as _e:
                        print(f"[SEMDEP ERROR] {_e}")
                # Save to DB
                listing_id, is_new = upsert_listing(parsed)

                if is_new:
                    stats["new"] += 1
                    if parsed.get("is_hot_deal"):
                        stats["hot"] += 1
                    # Save images
                    if image_urls:
                        save_images(listing_id, image_urls)
                else:
                    stats["dupes"] += 1

                stats["last_id"] = max(stats["last_id"], message.id)

            except Exception as e:
                stats["errors"] += 1
                print(f"[telethon] Message error id={message.id}: {e}")

    except Exception as e:
        print(f"[telethon] Channel error @{channel}: {e}")
        stats["errors"] += 1

    # БАГ C FIX: если итерация не дала ни одного сообщения (например канал недоступен),
    # сохраняем предыдущий last_id чтобы не затереть прогресс нулём
    final_last_id = stats["last_id"] if stats["last_id"] > 0 else prev_last_id

    # Log sync
    log_sync(
        channel=channel,
        parsed=stats["parsed"],
        new=stats["new"],
        dupes=stats["dupes"],
        hot=stats["hot"],
        errors=stats["errors"],
        last_msg_id=final_last_id,
    )

    print(f"[telethon] @{channel} done: "
          f"parsed={stats['parsed']} new={stats['new']} "
          f"dupes={stats['dupes']} hot={stats['hot']} errors={stats['errors']} "
          f"last_id={final_last_id}")

    return stats


async def run_parser_once(backfill: bool = False):
    """Run one parse cycle for all channels."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if not SESSION_STRING:
        print("[telethon] SESSION_STRING not set — skipping parse.")
        return

    from telethon.network import ConnectionTcpObfuscated
    # Build proxy from env if MTPROXY_* set: format ip:port:secret
    proxy = None
    mtp = os.environ.get("MTPROXY")
    if mtp:
        try:
            host, port, secret = mtp.split(":", 2)
            proxy = (host, int(port), secret)
        except Exception as e:
            print(f"[telethon] Bad MTPROXY env: {e}")
    socks = os.environ.get("SOCKS5_PROXY")  # format host:port[:user:pass]
    if socks and not proxy:
        try:
            import socks as _socks
            parts = socks.split(":")
            if len(parts) >= 2:
                proxy = (_socks.SOCKS5, parts[0], int(parts[1]),
                         True,
                         parts[2] if len(parts) > 2 else None,
                         parts[3] if len(parts) > 3 else None)
        except Exception as e:
            print(f"[telethon] Bad SOCKS5_PROXY env: {e}")

    client = TelegramClient(StringSession(SESSION_STRING), TELETHON_API_ID, TELETHON_API_HASH,
                            connection=ConnectionTcpObfuscated,
                            connection_retries=15, retry_delay=5, timeout=30,
                            request_retries=8,
                            use_ipv6=os.environ.get("USE_IPV6", "0") == "1",
                            proxy=proxy)

    async with client:
        for channel in CHANNELS:
            await parse_channel(client, channel, backfill=backfill)
            await asyncio.sleep(2)


async def run_catchup(channels: list[str] = None):
    """
    Catchup: парсим пропущенные сообщения для каждого канала
    начиная с реального последнего message_id в БД.
    Используется для восстановления после простоя парсера.
    """
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if not SESSION_STRING:
        print("[telethon] SESSION_STRING not set — skipping catchup.")
        return

    channels = channels or CHANNELS
    from telethon.network import ConnectionTcpObfuscated
    # Build proxy from env if MTPROXY_* set: format ip:port:secret
    proxy = None
    mtp = os.environ.get("MTPROXY")
    if mtp:
        try:
            host, port, secret = mtp.split(":", 2)
            proxy = (host, int(port), secret)
        except Exception as e:
            print(f"[telethon] Bad MTPROXY env: {e}")
    socks = os.environ.get("SOCKS5_PROXY")  # format host:port[:user:pass]
    if socks and not proxy:
        try:
            import socks as _socks
            parts = socks.split(":")
            if len(parts) >= 2:
                proxy = (_socks.SOCKS5, parts[0], int(parts[1]),
                         True,
                         parts[2] if len(parts) > 2 else None,
                         parts[3] if len(parts) > 3 else None)
        except Exception as e:
            print(f"[telethon] Bad SOCKS5_PROXY env: {e}")

    client = TelegramClient(StringSession(SESSION_STRING), TELETHON_API_ID, TELETHON_API_HASH,
                            connection=ConnectionTcpObfuscated,
                            connection_retries=15, retry_delay=5, timeout=30,
                            request_retries=8,
                            use_ipv6=os.environ.get("USE_IPV6", "0") == "1",
                            proxy=proxy)

    print(f"[telethon] Starting CATCHUP for channels: {channels}")

    async with client:
        for channel in channels:
            real_last = get_real_last_message_id(channel)
            print(f"[telethon] CATCHUP @{channel}: resuming from msg_id={real_last}")
            # Если для канала вообще нет данных — делаем полный backfill
            if real_last == 0:
                await parse_channel(client, channel, backfill=True)
            else:
                await parse_channel(client, channel, backfill=False, force_from_id=real_last)
            await asyncio.sleep(3)

    print("[telethon] CATCHUP complete.")


def run_parser_thread(backfill: bool = False):
    """Run parser in a background thread."""
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_parser_once(backfill=backfill))
        except Exception as e:
            print(f"[telethon] Thread error: {e}")
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True, name="telethon-parser")
    t.start()
    return t


def run_catchup_thread(channels: list[str] = None):
    """Run catchup in a background thread."""
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_catchup(channels))
        except Exception as e:
            print(f"[telethon] Catchup thread error: {e}")
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True, name="telethon-catchup")
    t.start()
    return t


def start_scheduler():
    """
    Start the periodic parser scheduler.
    First run: catchup from last known message_id in DB.
    Then: incremental every 30 minutes.

    БАГ B FIX: _needs_backfill теперь проверяет реальные данные в listings,
    а не sync_log который мог иметь last_message_id=0.
    """
    import time

    def _needs_backfill(channel: str) -> bool:
        """
        Возвращает True если для канала нет РЕАЛЬНЫХ данных в listings.
        Проверяем именно listings (не sync_log который мог писать 0).
        """
        return get_real_last_message_id(channel) == 0

    def _scheduler():
        # Startup delay чтобы старый Railway-контейнер успел отключиться от Telegram
        # перед тем как новый подключится. Без задержки → конфликт сессий
        # → AuthKeyDuplicated и бан ключа.
        startup_delay = int(os.environ.get("TELETHON_STARTUP_DELAY_SEC", "60"))
        if startup_delay > 0:
            print(f"[telethon] Startup delay {startup_delay}s (avoid session conflict with old container)...")
            time.sleep(startup_delay)
        print("[telethon] Scheduler started. Checking channel state...")

        # Для каждого канала определяем нужен ли backfill
        needs_catchup = []
        needs_backfill_list = []

        for ch in CHANNELS:
            last_id = get_real_last_message_id(ch)
            if last_id == 0:
                print(f"[telethon] @{ch}: no data → BACKFILL needed")
                needs_backfill_list.append(ch)
            else:
                print(f"[telethon] @{ch}: last_id={last_id} → CATCHUP from that point")
                needs_catchup.append(ch)

        # Сначала catchup для каналов с данными
        if needs_catchup:
            print(f"[telethon] Running CATCHUP for: {needs_catchup}")

            def _catchup():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(run_catchup(needs_catchup))
                except Exception as e:
                    print(f"[telethon] Catchup error: {e}")
                finally:
                    loop.close()

            t = threading.Thread(target=_catchup, daemon=True, name="catchup")
            t.start()
            t.join()

        # Потом backfill для каналов без данных
        if needs_backfill_list:
            print(f"[telethon] Running BACKFILL for: {needs_backfill_list}")
            for ch in needs_backfill_list:
                def _bf(channel=ch):
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    from telethon import TelegramClient
                    from telethon.sessions import StringSession
                    if not SESSION_STRING:
                        return
                    client = TelegramClient(StringSession(SESSION_STRING),
                                            TELETHON_API_ID, TELETHON_API_HASH)
                    async def _run():
                        async with client:
                            await parse_channel(client, channel, backfill=True)
                    try:
                        loop.run_until_complete(_run())
                    except Exception as e:
                        print(f"[telethon] Backfill error @{channel}: {e}")
                    finally:
                        loop.close()

                t = threading.Thread(target=_bf, daemon=True, name=f"backfill-{ch}")
                t.start()
                t.join()

        # Incremental loop
        while True:
            print(f"[telethon] Incremental parse at {datetime.now().strftime('%H:%M:%S')}")
            t = run_parser_thread(backfill=False)
            t.join()
            print(f"[telethon] Next parse in {PARSE_INTERVAL//60} minutes")
            time.sleep(PARSE_INTERVAL)

    thread = threading.Thread(target=_scheduler, daemon=True, name="scheduler")
    thread.start()
    return thread


