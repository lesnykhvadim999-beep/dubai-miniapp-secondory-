"""
telethon_parser.py — Telegram channel parser using Telethon.
Implements ТЗ blocks 3.1-3.4:
- Historical parsing from 01.01.2026
- Incremental mode (only new messages)
- Image/media group handling
- Save state logic
"""
import os
import asyncio
import threading
import requests as _requests
from datetime import datetime, timezone, timedelta

from parser_engine import parse_message, is_spam, ai_parse_listing, merge_ai_with_parsed
from db_schema import upsert_listing, save_images, log_sync, get_last_parsed_message_id

# ── Config ────────────────────────────────────────────────────────────────────
TELETHON_API_ID   = int(os.environ.get("TELEGRAM_API_ID", "39535588"))
TELETHON_API_HASH = os.environ.get("TELEGRAM_API_HASH", "e48ee11a80b4ede45dbe097cfbf916ff")
SESSION_STRING    = os.environ.get("SESSION_STRING", "")

# Channels to parse
CHANNELS = [
    "flipluxproperty",
    "dubairealestatedirectorydubilook",
    "secondary_dubai",
]

BACKFILL_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
PARSE_INTERVAL = 30 * 60  # 30 minutes
BATCH_SIZE = 100
PHOTO_DOWNLOAD = True  # Download and save photos


BOT_TOKEN_UPLOAD = os.environ.get("RESALE_BOT_TOKEN", "")
BOT_API_UPLOAD   = f"https://api.telegram.org/bot{BOT_TOKEN_UPLOAD}"
UPLOAD_CHAT_ID   = 353806371  # Admin chat used as temp photo buffer


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


async def parse_channel(client, channel: str, backfill: bool = False):
    """Parse messages from a channel."""
    print(f"[telethon] Parsing @{channel} (backfill={backfill})")

    stats = {
        "parsed": 0, "new": 0, "dupes": 0,
        "hot": 0, "errors": 0, "last_id": 0
    }

    # Determine offset
    if backfill:
        offset_date = BACKFILL_DATE
        min_id = 0
    else:
        last_id = get_last_parsed_message_id(channel)
        if last_id:
            min_id = last_id
            offset_date = None
            print(f"[telethon] Incremental from message_id={last_id}")
        else:
            offset_date = BACKFILL_DATE
            min_id = 0

    try:
        entity = await client.get_entity(channel)
        processed_groups = set()  # Track processed media groups

        # During backfill: no limit — iterate ALL messages from BACKFILL_DATE
        # During incremental: limit to BATCH_SIZE per cycle
        kwargs = {"entity": entity, "limit": None if backfill else BATCH_SIZE, "reverse": True}
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

                date_str = msg_date.strftime("%Y-%m-%d") if msg_date else ""

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

                parsed = parse_message(
                    text=text,
                    message_id=message.id,
                    message_date=msg_date.isoformat() if msg_date else None,
                    chat_id=str(entity.id),
                    seller_username=seller_username,
                    image_urls=image_urls,
                )

                if not parsed:
                    continue

                # AI classification — enrich/override rule-based result
                ai_result = ai_parse_listing(text)
                if ai_result:
                    if ai_result.get("is_spam"):
                        print(f"[AI SPAM] msg={message.id} text={text[:60]!r}")
                        stats["errors"] += 1
                        continue
                    parsed = merge_ai_with_parsed(parsed, ai_result)
                    print(
                        f"[AI] {parsed['deal_type'].upper()} | "
                        f"{parsed.get('building') or '-'} | "
                        f"{parsed.get('area') or '-'} | "
                        f"{parsed.get('price') or 0:,} AED | "
                        f"conf={ai_result.get('confidence','?')}"
                    )

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

    # Log sync
    log_sync(
        channel=channel,
        parsed=stats["parsed"],
        new=stats["new"],
        dupes=stats["dupes"],
        hot=stats["hot"],
        errors=stats["errors"],
        last_msg_id=stats["last_id"],
    )

    print(f"[telethon] @{channel} done: "
          f"parsed={stats['parsed']} new={stats['new']} "
          f"dupes={stats['dupes']} hot={stats['hot']} errors={stats['errors']}")

    return stats


async def run_parser_once(backfill: bool = False):
    """Run one parse cycle for all channels."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if not SESSION_STRING:
        print("[telethon] SESSION_STRING not set — skipping parse.")
        return

    client = TelegramClient(StringSession(SESSION_STRING), TELETHON_API_ID, TELETHON_API_HASH)

    async with client:
        for channel in CHANNELS:
            await parse_channel(client, channel, backfill=backfill)
            await asyncio.sleep(2)


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


def start_scheduler():
    """
    Start the periodic parser scheduler.
    First run: backfill from Jan 2026.
    Then: incremental every 30 minutes.
    """
    import time

    def _needs_backfill() -> bool:
        """Return True only if the DB has no sync records for any channel."""
        try:
            from db_schema import get_last_parsed_message_id
            for ch in CHANNELS:
                if get_last_parsed_message_id(ch):
                    return False
        except Exception:
            pass
        return True

    def _scheduler():
        # Backfill only when DB has no prior sync records (survives restarts)
        if _needs_backfill():
            print("[telethon] Starting historical backfill from Jan 2026...")
            t = run_parser_thread(backfill=True)
            t.join()  # Wait for backfill to complete
            print("[telethon] Backfill complete.")

        # Incremental loop
        while True:
            print(f"[telethon] Incremental parse at {datetime.now().strftime('%H:%M')}")
            t = run_parser_thread(backfill=False)
            t.join()
            print(f"[telethon] Next parse in {PARSE_INTERVAL//60} minutes")
            time.sleep(PARSE_INTERVAL)

    thread = threading.Thread(target=_scheduler, daemon=True, name="scheduler")
    thread.start()
    return thread
