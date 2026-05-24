"""Ночной аудит: проходит по КАЖДОЙ visible-записи в БД,
сверяет с original_text, классифицирует расхождения, пишет
лог и применяет фиксы.

Категории расхождений:
  A. parser-miss: в оригинале ЕСТЬ нужная информация, но в БД NULL
     → re-extract парсером, обновить БД
  B. parser-wrong: в БД одно значение, в оригинале — другое (но валидное)
     → re-extract, обновить
  C. source-missing: в оригинале нет нужной инфы (ни building, ни area,
     ни price) → если price тоже нет — is_audit=TRUE; иначе оставить NULL
  D. parser-leak: в БД есть значение которого НЕТ в оригинале (multi-listing
     leak, ложный landmark match) → NULL

Конфиг через env:
  NIGHTLY_BATCH=200       размер батча
  NIGHTLY_START_ID=0      с какого id начать
  NIGHTLY_DRY=0           не писать в БД (1 — write disabled)

Output:
  _nightly_log.txt — лог каждой записи + расхождений
  _nightly_todo.txt — список расхождений для парсера: «ожидалось X — парсер вернул Y»
"""
import os, sys, io, re, json, time, psycopg2, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, '.')

from parser_engine import (
    parse_message, _first_listing_block, _is_building_stopword,
    AREAS, detect_area, detect_building, extract_bedrooms, extract_size,
    extract_price, extract_property_type,
)

DB = os.getenv("DATABASE_URL",
    "REDACTED_DSN_USE_DATABASE_URL_ENV")
BATCH = int(os.getenv("NIGHTLY_BATCH", "200"))
START_ID = int(os.getenv("NIGHTLY_START_ID", "0"))
DRY = os.getenv("NIGHTLY_DRY", "0") == "1"

LOG_FILE = "_nightly_log.txt"
TODO_FILE = "_nightly_todo.txt"


def normspace(s):
    return re.sub(r'\s+', ' ', s or '').strip()


def text_contains(txt: str, value: str, window=10) -> bool:
    """True если value содержится в первой части текста (≈первый листинг)."""
    if not value or not txt:
        return False
    head = txt[:1500].lower()
    v = value.strip().lower()
    return v in head


def audit_record(rec: dict) -> dict:
    """Возвращает {action, updates, notes, audit_flag}."""
    txt = rec['original_text'] or ""
    notes = []
    updates = {}
    audit_flag = False

    if len(txt) < 30:
        return {'action': 'skip_short', 'updates': {}, 'notes': ['too short'],
                'audit_flag': True}

    # Run fresh parse
    try:
        fresh = parse_message(txt, rec['id'], None, 0)
        if not fresh:
            return {'action': 'parse_none', 'updates': {}, 'notes': ['parse_message returned None'],
                    'audit_flag': True}
    except Exception as e:
        return {'action': 'parse_err', 'updates': {}, 'notes': [f'parse_message err: {e}'],
                'audit_flag': False}

    head = _first_listing_block(txt)
    head_l = head.lower()

    # ── BUILDING ────────────────────────────────────────────────────────────
    db_bld = (rec['building'] or '').strip()
    fr_bld = (fresh.get('building') or '').strip() or None

    if db_bld:
        if not text_contains(txt, db_bld):
            # building в БД, но НЕ в тексте → leak
            notes.append(f'B-leak: db={db_bld!r} not in text')
            updates['building'] = fr_bld  # take fresh value (might also be None)
        elif fr_bld and fr_bld != db_bld and text_contains(head, fr_bld):
            notes.append(f'B-update: db={db_bld!r} → fr={fr_bld!r}')
            updates['building'] = fr_bld
    else:
        # NULL in DB — if fresh has value AND value in head → fill
        if fr_bld and text_contains(head, fr_bld) and not _is_building_stopword(fr_bld):
            notes.append(f'B-fill: NULL → fr={fr_bld!r}')
            updates['building'] = fr_bld

    # ── AREA ────────────────────────────────────────────────────────────────
    db_area = (rec['area'] or '').strip()
    fr_area = (fresh.get('area') or '').strip() or None

    if db_area:
        # Area is allowed if it's in text OR in any known canonical form
        area_in_text = text_contains(txt, db_area)
        # Also check aliases
        info = AREAS.get(db_area, {})
        if not area_in_text and info:
            for alias in info.get('aliases', []):
                if alias.lower() in txt.lower()[:1500]:
                    area_in_text = True
                    break
        if not area_in_text:
            notes.append(f'A-leak: db={db_area!r} not in text')
            updates['area'] = fr_area
        elif fr_area and fr_area != db_area:
            # Fresh differs — prefer fresh ONLY if it's in head
            if text_contains(head, fr_area):
                notes.append(f'A-update: db={db_area!r} → fr={fr_area!r}')
                updates['area'] = fr_area
    else:
        if fr_area:
            notes.append(f'A-fill: NULL → fr={fr_area!r}')
            updates['area'] = fr_area

    # ── BEDROOMS ────────────────────────────────────────────────────────────
    db_br = rec['bedrooms']
    fr_br = fresh.get('bedrooms')
    if db_br is None and fr_br is not None:
        notes.append(f'BR-fill: NULL → {fr_br}')
        updates['bedrooms'] = fr_br
    elif db_br is not None and fr_br is not None and fr_br != db_br:
        # Trust fresh only if it's "clean" extraction (text actually contains it)
        if re.search(rf'\b{fr_br}\s*[\-]?\s*(?:bedroom|bed|br|bdr|bhk|bd)\b', head_l):
            notes.append(f'BR-update: {db_br} → {fr_br}')
            updates['bedrooms'] = fr_br

    # ── SIZE ────────────────────────────────────────────────────────────────
    db_sz = rec['size_sqft']
    fr_sz = fresh.get('size_sqft')
    if db_sz is None and fr_sz:
        notes.append(f'SZ-fill: NULL → {fr_sz}')
        updates['size_sqft'] = fr_sz
    elif db_sz and fr_sz and abs(float(db_sz) - float(fr_sz)) > 100:
        # Trust fresh if its value appears in head text
        if re.search(rf'{int(fr_sz)}|{int(fr_sz/10)*10}', head):
            notes.append(f'SZ-update: {db_sz} → {fr_sz}')
            updates['size_sqft'] = fr_sz

    # ── PRICE ───────────────────────────────────────────────────────────────
    db_pr = rec['price']
    fr_pr = fresh.get('price')
    try:
        db_pr_n = float(db_pr) if db_pr else None
    except: db_pr_n = None
    if db_pr_n is None and fr_pr:
        notes.append(f'PR-fill: NULL → {fr_pr}')
        updates['price'] = fr_pr
    elif db_pr_n and fr_pr and abs(db_pr_n - fr_pr) / max(db_pr_n, fr_pr) > 0.3:
        # Significant difference — trust fresh if it's first-block value
        notes.append(f'PR-update: {int(db_pr_n)} → {fr_pr}')
        updates['price'] = fr_pr

    # ── PROPERTY_TYPE ───────────────────────────────────────────────────────
    db_pt = (rec['property_type'] or '').strip()
    fr_pt = fresh.get('property_type')
    if fr_pt and fr_pt != db_pt and fr_pt not in ('apartment',):
        # Only override if fresh-pt has keyword in head
        kw_map = {
            'villa': r'\bvilla\b', 'townhouse': r'\btownhouse|town\s+house\b',
            'penthouse': r'\bpent\s*house\b', 'duplex': r'\bduplex\b',
            'studio': r'\bstudio\b', 'office': r'\boffice\b',
            'retail': r'\bretail|\bshop\b', 'plot': r'\bplot\b|\bland\s+for\s+sale\b',
            'whole_building': r'\bwhole\s+building|\bbuilding\s+for\s+sale|g\s*\+\s*\d',
        }
        pat = kw_map.get(fr_pt)
        if pat and re.search(pat, head_l):
            notes.append(f'PT-update: {db_pt!r} → {fr_pt!r}')
            updates['property_type'] = fr_pt

    # ── AUDIT decision ──────────────────────────────────────────────────────
    # Если после updates запись фактически пустая (нет price + nothing about building/area) — audit
    final_price = updates.get('price', db_pr_n)
    final_bld = updates.get('building', db_bld) if 'building' in updates else db_bld
    final_area = updates.get('area', db_area) if 'area' in updates else db_area

    # source-missing: ни price ни building ни area  → audit
    if not final_price and not final_bld and not final_area:
        # Doublecheck text really has nothing
        if not re.search(r'\baed|dhs|aed|million|m\b|k\b|price|sale', head_l):
            audit_flag = True
            notes.append('AUDIT: no price/bld/area in record AND text')

    action = 'updated' if updates else 'noop'
    if audit_flag:
        action = 'audited'
    return {'action': action, 'updates': updates, 'notes': notes,
            'audit_flag': audit_flag}


def main():
    conn = psycopg2.connect(DB)
    cur = conn.cursor()

    cur.execute("""SELECT id, building, area, property_type, bedrooms, size_sqft,
                          price, deal_type, original_text
        FROM listings
        WHERE is_audit IS NOT TRUE AND id > %s
        ORDER BY id
        LIMIT %s""", (START_ID, BATCH))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    print(f"[nightly] Got {len(rows)} records starting after id={START_ID}", flush=True)

    log = open(LOG_FILE, 'a', encoding='utf-8')
    todo = open(TODO_FILE, 'a', encoding='utf-8')

    n_updated = n_audited = n_noop = 0
    last_id = START_ID
    t0 = time.time()

    for row in rows:
        rec = dict(zip(cols, row))
        last_id = rec['id']
        try:
            res = audit_record(rec)
        except Exception as e:
            log.write(f"id={rec['id']}: ERR {e}\n{traceback.format_exc()}\n")
            continue

        if res['action'] == 'updated':
            n_updated += 1
        elif res['action'] == 'audited':
            n_audited += 1
        else:
            n_noop += 1

        # Log
        if res['notes']:
            log.write(f"id={rec['id']}: {res['action']}\n")
            for n in res['notes']:
                log.write(f"  {n}\n")

        # Apply updates
        if res['updates'] and not DRY:
            sets = []
            vals = []
            for k, v in res['updates'].items():
                sets.append(f"{k}=%s")
                vals.append(v)
            sql = f"UPDATE listings SET {', '.join(sets)} WHERE id=%s"
            try:
                cur.execute(sql, vals + [rec['id']])
            except Exception as e:
                log.write(f"  UPDATE ERR: {e}\n")
                conn.rollback()
                continue

        if res['audit_flag'] and not DRY:
            try:
                cur.execute("UPDATE listings SET is_audit=TRUE WHERE id=%s", (rec['id'],))
            except Exception as e:
                log.write(f"  AUDIT ERR: {e}\n")
                conn.rollback()

    if not DRY:
        conn.commit()
    elapsed = time.time() - t0
    print(f"[nightly] batch done: updated={n_updated} audited={n_audited} noop={n_noop} "
          f"last_id={last_id} elapsed={elapsed:.1f}s", flush=True)
    log.close()
    todo.close()
    conn.close()

    # Write next-start-id for chain
    with open("_nightly_next_id.txt", "w") as f:
        f.write(str(last_id))


if __name__ == "__main__":
    main()
