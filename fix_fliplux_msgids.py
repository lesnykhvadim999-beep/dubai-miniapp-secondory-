"""
B053 fix: обнуляем фейковые telegram_message_id > 1,000,000 для @flipluxproperty
(telegram_chat_id = '1781686176').
82 записи вставлены 22.05 с ID 76955537-76955618 (не настоящие TG message IDs).
После фикса get_real_last_message_id() вернёт 769690, и парсер возобновит работу.
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL", "")

conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
conn.autocommit = False

with conn.cursor() as cur:
    # Проверяем сколько записей будем чинить
    cur.execute("""
        SELECT COUNT(*) as cnt, MAX(telegram_message_id) as max_id, MIN(telegram_message_id) as min_id
        FROM listings
        WHERE telegram_chat_id = '1781686176'
          AND telegram_message_id > 1000000
    """)
    row = cur.fetchone()
    print(f"[fix] Records to fix: {row['cnt']}, ID range: {row['min_id']} – {row['max_id']}")

    # Обнуляем фейковые message_id
    cur.execute("""
        UPDATE listings
        SET telegram_message_id = NULL
        WHERE telegram_chat_id = '1781686176'
          AND telegram_message_id > 1000000
    """)
    print(f"[fix] Updated {cur.rowcount} rows")

    # Проверяем новый MAX
    cur.execute("""
        SELECT COALESCE(MAX(telegram_message_id), 0) as new_max
        FROM listings
        WHERE telegram_chat_id = '1781686176'
    """)
    row = cur.fetchone()
    print(f"[fix] New MAX telegram_message_id for flipluxproperty: {row['new_max']}")

conn.commit()
conn.close()
print("[fix] Done. Parser will resume from correct position on next cycle.")
