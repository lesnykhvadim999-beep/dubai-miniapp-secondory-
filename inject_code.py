"""
Run this from Replit to inject the Telegram auth code into Railway DB.
Usage: python3 inject_code.py 12345
"""
import os
import sys, psycopg2

DB_URL = os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set"))

if len(sys.argv) < 2:
    print("Usage: python3 inject_code.py <CODE>")
    sys.exit(1)

code = sys.argv[1].strip()
conn = psycopg2.connect(DB_URL, connect_timeout=10)
with conn.cursor() as cur:
    cur.execute(
        "INSERT INTO _auth_kv(k,v) VALUES('auth_code',%s) ON CONFLICT(k) DO UPDATE SET v=%s",
        (code, code)
    )
conn.commit()

# Verify
with conn.cursor() as cur:
    cur.execute("SELECT v FROM _auth_kv WHERE k='auth_code'")
    row = cur.fetchone()
conn.close()

print(f"✅  Injected code: {code}")
print(f"    DB confirms: auth_code = {row[0] if row else 'NOT FOUND'}")
