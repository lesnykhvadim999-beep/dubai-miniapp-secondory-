"""One-shot cleanup — обнуляет building у записей, где парсер ошибочно
вытащил маркетинговую/регуляторную фразу как имя здания.

После фикса в parser_engine.py эти кейсы больше не пройдут, но старые
записи надо привести в порядок руками.
"""
import os, sys, io
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
except Exception:
    pass

import psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "REDACTED_DSN_USE_DATABASE_URL_ENV"
)

# Regex совпадает с тем, что добавлен в _is_building_stopword
PATTERN_PREFIX = (
    r"^(all\s+licens|licens|off[\s\-]?plan|"
    r"ready\s*(?:&|and)\s*vacant|"
    r"vacant\s+(?:on|unit|possession|now|in\s+\w+|back)|"
    r"vacant\s+backstosback|"
    r"rented\s+unit|currently\s+rented|tenanted|"
    r"ready\s+to\s+move|move\s+in\s+ready|"
    r"title\s+deed|rera\s+approved|"
    r"investor\s+(?:deal|opportunity|required)|"
    r"genuine\s+(?:listing|seller|deal)|"
    r"direct\s+from\s+owner|from\s+owner|by\s+owner|"
    r"high\s+roi|best\s+roi|guaranteed\s+roi|"
    r"limited\s+(?:offer|time)|last\s+unit|"
    r"back\s+to\s+back|backstosback|mid\s+corner|midscorner|"
    r"brand\s+new)"
)
PATTERN_DATE = (
    r"^(?:vacant|available|handover|hand\s*over)\s+"
    r"(?:in\s+|on\s+|from\s+)?"
    r"(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december|q[1-4]|\d{4})"
)

SQL = f"""
UPDATE listings
   SET building = NULL,
       status = COALESCE(NULLIF(status, ''), 'audit_flagged'),
       audit_reason = COALESCE(audit_reason, 'building was regulatory/marketing phrase'),
       audit_flagged_at = COALESCE(audit_flagged_at, NOW())
 WHERE building IS NOT NULL
   AND (LOWER(building) ~ %s OR LOWER(building) ~ %s)
"""

# Сначала dry-run — посчитаем сколько
COUNT_SQL = """
SELECT COUNT(*) FROM listings
 WHERE building IS NOT NULL
   AND (LOWER(building) ~ %s OR LOWER(building) ~ %s)
"""

with psycopg2.connect(DATABASE_URL) as conn:
    with conn.cursor() as cur:
        cur.execute(COUNT_SQL, (PATTERN_PREFIX, PATTERN_DATE))
        n = cur.fetchone()[0]
        print(f"Найдено битых записей: {n}")
        if n == 0:
            print("Нечего чистить.")
        else:
            cur.execute(SQL, (PATTERN_PREFIX, PATTERN_DATE))
            print(f"Обновлено: {cur.rowcount}")
    conn.commit()
print("Done.")
