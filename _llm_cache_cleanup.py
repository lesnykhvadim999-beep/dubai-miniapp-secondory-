"""Daily cron — purge expired LLM cache rows.

Usage:
    py _llm_cache_cleanup.py

Env:
    DATABASE_URL — resale-bot Postgres DSN
"""
import os
import sys
from datetime import datetime

try:
    import psycopg2
except ImportError:
    print("psycopg2 not installed")
    sys.exit(1)


def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)
    conn = psycopg2.connect(dsn, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM llm_cache WHERE expires_at < NOW();")
            deleted = cur.rowcount
            cur.execute("SELECT COUNT(*) FROM llm_cache;")
            remaining = cur.fetchone()[0]
        conn.commit()
        print(f"[{datetime.utcnow().isoformat()}] llm_cache cleanup: "
              f"deleted={deleted} remaining={remaining}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
