"""
One-time Telethon userbot authentication — outputs SESSION_STRING.

Run in Shell:
    python3 auth_telethon.py

You will be asked for:
  1. Your phone number  (+7..., +971..., etc.)
  2. The login code Telegram sends you
  3. Your 2FA password (if enabled)

After success the SESSION_STRING is printed — copy it to Railway Variables.
"""

import asyncio, os
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
)

API_ID   = int(os.environ.get("TELEGRAM_API_ID",   "39535588"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "e48ee11a80b4ede45dbe097cfbf916ff")

print()
print("=" * 55)
print("  Telethon userbot — one-time authentication")
print("=" * 55)
print(f"  API_ID   : {API_ID}")
print(f"  API_HASH : {API_HASH[:8]}...")
print()

async def main():
    session = StringSession()
    client = TelegramClient(session, API_ID, API_HASH)
    await client.connect()
    print("✓ Connected to Telegram DC")

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"✓ Already logged in as {me.first_name} (@{me.username})")
    else:
        phone = input("Enter phone number (e.g. +79001234567): ").strip()
        if not phone:
            print("❌ No phone entered. Exiting.")
            await client.disconnect()
            return

        try:
            result = await client.send_code_request(phone)
            print(f"✓ Code sent via {result.type.__class__.__name__}")
        except FloodWaitError as e:
            print(f"❌ Too many attempts — wait {e.seconds} seconds and try again.")
            await client.disconnect()
            return
        except Exception as e:
            print(f"❌ Failed to send code: {e}")
            await client.disconnect()
            return

        code = input("Enter the code from Telegram (digits only): ").strip()

        try:
            await client.sign_in(phone, code)
        except PhoneCodeInvalidError:
            print("❌ Code is incorrect. Run the script again.")
            await client.disconnect()
            return
        except PhoneCodeExpiredError:
            print("❌ Code expired. Run the script again.")
            await client.disconnect()
            return
        except SessionPasswordNeededError:
            print("⚠  Two-step verification is enabled.")
            password = input("Enter your 2FA password: ").strip()
            try:
                await client.sign_in(password=password)
            except Exception as e:
                print(f"❌ 2FA failed: {e}")
                await client.disconnect()
                return
        except Exception as e:
            print(f"❌ Sign-in error: {e}")
            await client.disconnect()
            return

    me = await client.get_me()
    session_string = client.session.save()

    print()
    print(f"✅  Logged in as: {me.first_name} (@{me.username or 'no username'})")
    print(f"    Phone : {me.phone}")
    print(f"    ID    : {me.id}")
    print()
    # P2-FIX: NEVER print session_string to stdout (logs are searchable).
    import os as _os, secrets as _secrets, tempfile as _tempfile
    _tmp_dir = _tempfile.gettempdir()
    _fname = f"session_{_secrets.token_hex(8)}.txt"
    _fpath = _os.path.join(_tmp_dir, _fname)
    with open(_fpath, "w", encoding="utf-8") as _f:
        _f.write(session_string)
    try:
        _os.chmod(_fpath, 0o600)
    except Exception:
        pass
    _redacted = session_string[:6] + "***REDACTED***" + session_string[-4:]
    print("=" * 55)
    print(f"[session ready — len={len(session_string)} preview={_redacted}]")
    print(f"[full session written to: {_fpath}]")
    print(f"[retrieve locally: cat {_fpath}  (or `railway exec -- cat {_fpath}`)]")
    print("=" * 55)
    print()
    print("Next step: set SESSION_STRING in Railway → Variables → redeploy.")
    print()

    await client.disconnect()

asyncio.run(main())
