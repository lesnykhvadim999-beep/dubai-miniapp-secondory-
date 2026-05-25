"""
B053 sweep: обнуляем фейковые telegram_message_id > 1,000,000 по всем каналам.
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL", "")
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
conn.autocommit = False

CHANNELS = {
    "flipluxproperty":                  "1781686176",
    "dubairealestatedirectorydubilook": "1125918023",
    "secondary_dubai":                  "2187754007",
}

with conn.cursor() as cur:
    for name, chat_id in CHANNELS.items():
        cur.execute("""
            SELECT COUNT(*) as cnt, MAX(telegram_message_id) as max_id
            FROM listings
            WHERE telegram_chat_id = %s AND telegram_message_id > 1000000
        """, (chat_id,))
        row = cur.fetchone()
        if row["cnt"] == 0:
            print(f"[{name}] OK — нет аномальных ID")
            continue

        print(f"[{name}] Fixing {row['cnt']} records, max bad ID = {row['max_id']}")
        cur.execute("""
            UPDATE listings SET telegram_message_id = NULL
            WHERE telegram_chat_id = %s AND telegram_message_id > 1000000
        """, (chat_id,))
        print(f"[{name}] Updated {cur.rowcount} rows")

        cur.execute("""
            SELECT COALESCE(MAX(telegram_message_id), 0) as new_max
            FROM listings WHERE telegram_chat_id = %s
        """, (chat_id,))
        row2 = cur.fetchone()
        print(f"[{name}] New MAX = {row2['new_max']}")

conn.commit()
conn.close()
print("\nDone. All channels will resume parsing from correct position.")
