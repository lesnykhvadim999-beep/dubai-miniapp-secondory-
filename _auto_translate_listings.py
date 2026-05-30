"""Auto-translate Dubai property listing descriptions into RU/EN/AR/ZH.

Berserker batch translator:
- SELECT active listings with description that have no translation yet.
- For each, call LLM ONCE with structured-JSON output that includes
  source_lang + all 4 translations.
- UPSERT into `listing_translations`.

LRU cache (in-process) — same description text is not re-translated within the
same batch run. The Postgres `listing_translations` table acts as long-term
storage; LRU is only a safety-net for batches where dozens of listings share an
identical agent template.

Usage:
    py _auto_translate_listings.py                  # batch up to 100
    py _auto_translate_listings.py --limit 25
    py _auto_translate_listings.py --listing-id 123 # single listing
    py _auto_translate_listings.py --test           # process 3 listings, verbose

The script does NOT run automatically — Vadim invokes it manually.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from functools import lru_cache
from typing import Dict, Optional, Tuple

import psycopg2  # type: ignore
import psycopg2.extras  # type: ignore

# llm_chain.llm_call is the universal LLM entry-point for this project
try:
    from llm_chain import llm_call
except Exception as exc:  # pragma: no cover
    print(f"[fatal] cannot import llm_chain: {exc}", flush=True)
    raise

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_URL = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set")),
)

DEFAULT_LIMIT = 100
MAX_DESC_CHARS = 1500   # safety clip
LANG_KEYS = ("ru", "en", "ar", "zh")
SUPPORTED_LANGS = {"ru", "en", "ar", "zh"}

PROMPT_TEMPLATE = """You are translating a Dubai property listing description.
Source: "{description}"

Return a JSON with these keys:
{{
  "source_lang": "en|ru|ar|zh",
  "ru": "...",
  "en": "...",
  "ar": "...",
  "zh": "..."
}}
Keep length similar to source. Preserve building names, area names, prices verbatim.
Return ONLY the JSON object, no extra commentary."""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _conn():
    return psycopg2.connect(DB_URL)


def fetch_pending(limit: int, listing_id: Optional[int] = None):
    """Return list of (id, description) rows that still need translation.

    In practice `listings.description` is often empty while `original_text`
    holds the raw seller text — we COALESCE so the batcher actually has
    something to translate.
    """
    sql = """
        SELECT l.id, COALESCE(NULLIF(trim(l.description), ''), l.original_text)
        FROM listings l
        LEFT JOIN listing_translations t ON t.listing_id = l.id
        WHERE l.is_active = TRUE
          AND COALESCE(NULLIF(trim(l.description), ''), l.original_text) IS NOT NULL
          AND char_length(trim(COALESCE(NULLIF(trim(l.description), ''),
                                        l.original_text))) >= 10
          AND t.listing_id IS NULL
    """
    params: list = []
    if listing_id is not None:
        sql += " AND l.id = %s"
        params.append(listing_id)
    sql += " ORDER BY l.id DESC LIMIT %s"
    params.append(limit)
    with _conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return [(int(r[0]), r[1]) for r in cur.fetchall()]


def upsert_translation(listing_id: int, payload: Dict[str, str]) -> None:
    sql = """
        INSERT INTO listing_translations
            (listing_id, text_ru, text_en, text_ar, text_zh,
             source_lang, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (listing_id) DO UPDATE SET
            text_ru     = EXCLUDED.text_ru,
            text_en     = EXCLUDED.text_en,
            text_ar     = EXCLUDED.text_ar,
            text_zh     = EXCLUDED.text_zh,
            source_lang = EXCLUDED.source_lang,
            updated_at  = NOW()
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            sql,
            (
                listing_id,
                payload.get("ru"),
                payload.get("en"),
                payload.get("ar"),
                payload.get("zh"),
                payload.get("source_lang"),
            ),
        )


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_llm_json(raw: str) -> Optional[Dict[str, str]]:
    """Best-effort JSON extraction (LLMs sometimes wrap with ```json ... ```)."""
    if not raw:
        return None
    txt = raw.strip()
    if txt.startswith("```"):
        # strip fenced block
        txt = re.sub(r"^```[a-zA-Z]*", "", txt).strip()
        if txt.endswith("```"):
            txt = txt[:-3].strip()
    try:
        return json.loads(txt)
    except Exception:
        pass
    m = _JSON_BLOCK_RE.search(txt)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _validate(payload: Dict[str, str]) -> Optional[Dict[str, str]]:
    if not isinstance(payload, dict):
        return None
    src = (payload.get("source_lang") or "").lower().strip()
    if src not in SUPPORTED_LANGS:
        # try to guess from `lang` key or fall back to "en"
        src = "en"
    out: Dict[str, str] = {"source_lang": src}
    for k in LANG_KEYS:
        v = payload.get(k)
        if not isinstance(v, str) or not v.strip():
            return None
        out[k] = v.strip()
    return out


@lru_cache(maxsize=1024)
def translate_description(description: str) -> Optional[Tuple[str, str, str, str, str]]:
    """LRU-cached translator.

    Returns tuple `(source_lang, ru, en, ar, zh)` or None on failure.
    Tuple is used (not dict) because `lru_cache` requires hashable returns and
    callers will repack into a dict via `_unpack`.
    """
    desc = (description or "").strip()
    if not desc:
        return None
    if len(desc) > MAX_DESC_CHARS:
        desc = desc[:MAX_DESC_CHARS]

    prompt = PROMPT_TEMPLATE.format(description=desc)
    raw = llm_call(prompt, max_tokens=1200, timeout=30, use_cache=True)
    if not raw:
        return None
    parsed = _parse_llm_json(raw)
    valid = _validate(parsed) if parsed else None
    if not valid:
        return None
    return (
        valid["source_lang"],
        valid["ru"],
        valid["en"],
        valid["ar"],
        valid["zh"],
    )


def _unpack(tpl: Tuple[str, str, str, str, str]) -> Dict[str, str]:
    return {
        "source_lang": tpl[0],
        "ru": tpl[1],
        "en": tpl[2],
        "ar": tpl[3],
        "zh": tpl[4],
    }


# ---------------------------------------------------------------------------
# Batch driver
# ---------------------------------------------------------------------------
def run_batch(limit: int = DEFAULT_LIMIT,
              listing_id: Optional[int] = None,
              verbose: bool = False) -> Dict[str, int]:
    rows = fetch_pending(limit, listing_id=listing_id)
    if not rows:
        print("[translate] nothing to do (no pending listings)", flush=True)
        return {"ok": 0, "fail": 0, "skipped": 0}

    print(f"[translate] processing {len(rows)} listings", flush=True)
    ok = fail = skipped = 0
    t0 = time.time()
    for idx, (lid, desc) in enumerate(rows, 1):
        if not desc or not desc.strip():
            skipped += 1
            continue
        try:
            tpl = translate_description(desc)
        except Exception as exc:  # pragma: no cover
            print(f"[translate] lid={lid} crash: {exc}", flush=True)
            fail += 1
            continue
        if not tpl:
            print(f"[translate] lid={lid} LLM returned no usable JSON", flush=True)
            fail += 1
            continue
        payload = _unpack(tpl)
        try:
            upsert_translation(lid, payload)
            ok += 1
        except Exception as exc:
            print(f"[translate] lid={lid} DB upsert err: {exc}", flush=True)
            fail += 1
            continue
        # Console logging is best-effort — Windows cp1252 chokes on emoji/CJK.
        try:
            if verbose:
                msg = (
                    f"[translate] lid={lid} src={payload['source_lang']} "
                    f"ru={payload['ru'][:60]!r} en={payload['en'][:60]!r}"
                )
            else:
                msg = (
                    f"[translate] lid={lid} OK src={payload['source_lang']} "
                    f"({idx}/{len(rows)})"
                )
            try:
                print(msg, flush=True)
            except UnicodeEncodeError:
                print(msg.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass

    dt = time.time() - t0
    print(
        f"[translate] done: ok={ok} fail={fail} skipped={skipped} in {dt:.1f}s",
        flush=True,
    )
    return {"ok": ok, "fail": fail, "skipped": skipped}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_cli(argv):
    p = argparse.ArgumentParser(description="Auto-translate listing descriptions")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help="max listings per run (default 100)")
    p.add_argument("--listing-id", type=int, default=None,
                   help="translate only this listing id")
    p.add_argument("--test", action="store_true",
                   help="process 3 listings, verbose output")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_cli(argv or sys.argv[1:])
    limit = 3 if args.test else args.limit
    res = run_batch(limit=limit, listing_id=args.listing_id, verbose=args.test)
    return 0 if res["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
