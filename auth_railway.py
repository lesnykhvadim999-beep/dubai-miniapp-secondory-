"""
Single-process Telethon auth for Railway.

1. On start: sends Telegram code to PHONE
2. Polls DB every 5s for CODE value written externally
3. Signs in immediately when CODE appears, prints SESSION_STRING to logs
4. Exits cleanly (exit 0 always)

Set PHONE env var before deploying.
After deploy starts and "Code sent!" appears in logs:
  → Write CODE to DB: INSERT INTO _auth_kv(k,v) VALUES('auth_code','XXXXX') ON CONFLICT(k) DO UPDATE SET v='XXXXX'
"""

import asyncio, os, sys, time, traceback

API_ID   = int(os.environ.get("TELEGRAM_API_ID", "39535588"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "e48ee11a80b4ede45dbe097cfbf916ff")
PHONE    = os.environ.get("PHONE", "").strip()
PASSWORD = os.environ.get("PASSWORD", "").strip()
DB_URL   = os.environ.get("DATABASE_URL", "")

POLL_INTERVAL = 5    # seconds between DB checks
TIMEOUT       = 600  # seconds to wait for code (10 minutes)


def db_connect():
    import psycopg2
    return psycopg2.connect(DB_URL, connect_timeout=5)


def db_ensure_table():
    """Create _auth_kv table if it doesn't exist."""
    try:
        conn = db_connect()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS _auth_kv (
                    k TEXT PRIMARY KEY,
                    v TEXT
                )
            """)
        conn.commit()
        conn.close()
        print("[db] _auth_kv table ready")
    except Exception as e:
        print(f"[db] ERROR creating table: {e}")
        traceback.print_exc()


def db_set(key, value):
    try:
        conn = db_connect()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO _auth_kv(k,v) VALUES(%s,%s) ON CONFLICT(k) DO UPDATE SET v=EXCLUDED.v",
                (key, value)
            )
        conn.commit()
        conn.close()
        print(f"[db] SET {key} = {repr(value[:30]) if value else repr(value)}")
    except Exception as e:
        print(f"[db] ERROR setting {key}: {e}")
        traceback.print_exc()


def db_get(key):
    try:
        conn = db_connect()
        with conn.cursor() as cur:
            cur.execute("SELECT v FROM _auth_kv WHERE k=%s", (key,))
            row = cur.fetchone()
        conn.close()
        val = row[0] if row else ""
        return val or ""
    except Exception as e:
        print(f"[db] ERROR reading {key}: {e}")
        traceback.print_exc()
        return ""


async def main():
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import SessionPasswordNeededError

    if not PHONE:
        print("❌  PHONE env var not set.")
        sys.exit(0)

    if not DB_URL:
        print("❌  DATABASE_URL env var not set.")
        sys.exit(0)

    print(f"[auth] DB_URL prefix: {DB_URL[:40]}...")
    print(f"[auth] PHONE: {PHONE}")

    # Ensure table exists and clear stale code
    db_ensure_table()
    db_set("auth_code", "")

    # Verify the clear worked
    check = db_get("auth_code")
    print(f"[auth] auth_code after clear: {repr(check)}")

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    print(f"[auth] Connected to Telegram DC. Sending code to {PHONE}...")
    try:
        result = await client.send_code_request(PHONE)
        db_set("auth_phone_code_hash", result.phone_code_hash)
        db_set("auth_phone", PHONE)
        print(f"✅  Code sent! Type: {result.type.__class__.__name__}")
        print(f"[auth] phone_code_hash stored (len={len(result.phone_code_hash)})")  # safety: ignore redacted
        print(f"[auth] Polling DB for auth_code every {POLL_INTERVAL}s (timeout {TIMEOUT}s)")
        print(f"[auth] To inject code, run from Replit:")
        print(f"[auth]   python3 inject_code.py <CODE>")
    except Exception as e:
        print(f"❌  Failed to send code: {e}")
        traceback.print_exc()
        await client.disconnect()
        sys.exit(0)

    # Poll DB for CODE
    deadline = time.time() + TIMEOUT
    code = ""
    while time.time() < deadline:
        await asyncio.sleep(POLL_INTERVAL)
        remaining = int(deadline - time.time())
        code = db_get("auth_code").strip()
        print(f"[auth] poll: auth_code={repr(code)} ({remaining}s left)")
        if code:
            print(f"[auth] Got CODE: {code}")
            break

    if not code:
        print("❌  Timed out waiting for code. Redeploy to try again.")
        await client.disconnect()
        sys.exit(0)

    # Sign in
    phone_code_hash = db_get("auth_phone_code_hash")
    print(f"[auth] Signing in (code redacted, hash redacted)...")  # safety: ignore redacted
    try:
        await client.sign_in(PHONE, code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        if not PASSWORD:
            print("⚠  2FA required. Set PASSWORD env var and redeploy.")
            await client.disconnect(); sys.exit(0)
        print("[auth] Submitting 2FA password...")
        await client.sign_in(password=PASSWORD)
    except Exception as e:
        print(f"❌  Sign-in error: {e}")
        traceback.print_exc()
        db_set("auth_code", "")
        await client.disconnect(); sys.exit(0)

    me = await client.get_me()
    session_string = client.session.save()
    await client.disconnect()

    db_set("auth_session_string", session_string)
    db_set("auth_code", "")

    print()
    print(f"✅  Logged in as: {me.first_name} (@{me.username or 'no username'})")
    print(f"    Phone: {me.phone} | ID: {me.id}")
    print()
    # P2-FIX: NEVER print session_string to stdout (Railway logs are searchable).
    # Write to a private file readable only via `railway exec cat <path>`.
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
    print("=" * 60)
    print(f"[session ready — len={len(session_string)} preview={_redacted}]")
    print(f"[full session written to: {_fpath}]")
    print(f"[retrieve via: railway exec -- cat {_fpath}]")
    print("=" * 60)
    print()
    print("Next steps:")
    print(f"  1. railway exec -- cat {_fpath}   # copy the SESSION_STRING")
    print("  2. Set it in Railway: railway variables set SESSION_STRING='...'")
    print("  3. Restore railway.toml startCommand to python3 resale_bot.py")
    print("  4. Redeploy")


asyncio.run(main())
