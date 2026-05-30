"""Active learning loop for Dubai resale-bot parser_v2 (#9).

Когда админ через @vadim_admin_bot правит ошибку парсера, исправление
записывается в таблицу `parser_examples` (см. resale_bot._save_parser_example
и db_schema.SCHEMA_SQL). Этот модуль:

  * `pick_few_shot_examples(text, n=3)` — берёт top-N релевантных
    примеров: same property_type, похожая длина текста, недавние.
    Гарантирует diversity (разные здания, разные типы / ценовые
    диапазоны), чтобы не учить парсер одной и той же подсказке.

  * `format_few_shot_prompt(text, examples)` — рендерит примеры как
    блок "Input: ... Output: ..." пар, готовый к инъекции в EXTRACT
    prompt parser_v2.

Активация — env var `ACTIVE_LEARNING_ENABLED=1`. По умолчанию выключено,
пока не накопится 100+ examples.

Жёсткие лимиты:
  * максимум 3 примера (≈600 токенов = 200/пример)
  * каждый source_text усекается до 600 символов
  * каждый correct_output усекается до 400 символов JSON
"""
from __future__ import annotations
import os
import json
from typing import List, Dict, Any, Optional

try:
    import psycopg2  # noqa
    from psycopg2.extras import RealDictCursor  # noqa
except Exception:  # pragma: no cover
    psycopg2 = None  # type: ignore


MAX_FEWSHOT = 3
MAX_SRC_LEN = 600
MAX_OUT_LEN = 400
ENV_FLAG = "ACTIVE_LEARNING_ENABLED"


def is_enabled() -> bool:
    return os.environ.get(ENV_FLAG, "0") == "1"


def _get_conn():
    if psycopg2 is None:
        return None
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return None
    try:
        return psycopg2.connect(dsn, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"[active_learning] db connect err: {e}")
        return None


def _classify_text_type(text: str) -> str:
    """Cheap heuristic, mirrors resale_bot._classify_parser_example."""
    t = (text or "").lower()
    if any(k in t for k in (" office", " shop", " retail",
                              " showroom", " warehouse", "commercial")):
        return "commercial"
    if "villa" in t or "townhouse" in t or "townhse" in t:
        return "villa"
    return "apartment"


def _diversify(rows: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    """Pick at most one example per (building, price_bucket) so the few-shot
    block teaches different shapes rather than the same listing repeated.
    Keeps query order (already sorted by relevance)."""
    out: List[Dict[str, Any]] = []
    seen_buildings: set = set()
    seen_buckets: set = set()
    for r in rows:
        co = r.get("correct_output") or {}
        if isinstance(co, str):
            try:
                co = json.loads(co)
            except Exception:
                co = {}
        b = (co.get("building") or "").strip().lower()
        p = co.get("price") or 0
        try:
            p = int(p)
        except Exception:
            p = 0
        # 4 buckets: <1M, 1-3M, 3-10M, 10M+
        bucket = 0 if p < 1_000_000 else (1 if p < 3_000_000 else
                                            (2 if p < 10_000_000 else 3))
        if b and b in seen_buildings:
            continue
        if bucket in seen_buckets and len(seen_buckets) >= 2:
            # allow up to 2 examples per bucket only if we ran out
            continue
        out.append(r)
        if b:
            seen_buildings.add(b)
        seen_buckets.add(bucket)
        if len(out) >= n:
            break
    return out


def pick_few_shot_examples(text: str, n: int = MAX_FEWSHOT) -> List[Dict[str, Any]]:
    """Pick top-N most relevant labeled examples.

    Strategy: filter to same `example_type` (apartment/villa/commercial),
    sort by recency, exclude examples explicitly disabled
    (`used_in_prompt=FALSE` AFTER admin disables it — we store TRUE as
    "OK to use"). Then diversify via `_diversify`.

    Returns at most `min(n, MAX_FEWSHOT)` rows. Each row has columns
    `source_text`, `correct_output` (dict), `example_type`,
    `created_at`, `id`.
    """
    n = max(0, min(n, MAX_FEWSHOT))
    if n == 0:
        return []
    conn = _get_conn()
    if conn is None:
        return []
    bucket = _classify_text_type(text or "")
    tlen = len(text or "")
    try:
        with conn.cursor() as cur:
            # Fetch a wider pool (≤30) for diversity filter, sorted by
            # closeness of length + recency. We ONLY pick examples where
            # used_in_prompt=TRUE — that's the explicit allow-list flag
            # admin maintains via _active_learning_audit.py.
            cur.execute(
                """
                SELECT id, source_text, correct_output, example_type,
                       created_at,
                       ABS(LENGTH(source_text) - %s) AS len_diff
                FROM parser_examples
                WHERE used_in_prompt = TRUE
                  AND (example_type = %s OR example_type IS NULL)
                ORDER BY len_diff ASC, created_at DESC
                LIMIT 30
                """,
                (tlen, bucket),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[active_learning] pick err: {e}")
        rows = []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    # parse JSONB if driver returned str
    for r in rows:
        co = r.get("correct_output")
        if isinstance(co, str):
            try:
                r["correct_output"] = json.loads(co)
            except Exception:
                r["correct_output"] = {}
    return _diversify(rows, n)


def _trim_output(d: dict) -> dict:
    """Drop null/redundant fields for compactness in prompt."""
    if not isinstance(d, dict):
        return {}
    keep_keys = (
        "deal_type", "property_type", "emirate", "area", "building",
        "bedrooms", "bathrooms", "size_sqft", "floor", "view",
        "furnishing", "price", "currency",
    )
    return {k: d.get(k) for k in keep_keys if d.get(k) not in (None, "", [], {})}


def format_few_shot_prompt(text: str, examples: List[Dict[str, Any]]) -> str:
    """Render a few-shot block prefixed before the actual extraction task.

    Output shape (each example labelled as Input/Output JSON pair, then
    the real query at the end):

        Examples of correct extractions:

        Input:
        \"\"\"<listing 1>\"\"\"
        Output:
        {"deal_type": "sale", ...}

        Input:
        \"\"\"<listing 2>\"\"\"
        Output:
        {...}

        Now extract for this listing:
        \"\"\"<text>\"\"\"

    If `examples` is empty, returns just the standalone task body so the
    caller can drop it into EXTRACT_PROMPT verbatim.
    """
    if not examples:
        return f'"""\n{(text or "")[:2000]}\n"""'
    parts: List[str] = ["Examples of correct extractions:", ""]
    for ex in examples[:MAX_FEWSHOT]:
        src = (ex.get("source_text") or "").strip()[:MAX_SRC_LEN]
        out = _trim_output(ex.get("correct_output") or {})
        out_json = json.dumps(out, ensure_ascii=False)[:MAX_OUT_LEN]
        parts.extend([
            "Input:",
            '"""',
            src,
            '"""',
            "Output:",
            out_json,
            "",
        ])
    parts.extend([
        "Now extract for this listing:",
        '"""',
        (text or "")[:2000],
        '"""',
    ])
    return "\n".join(parts)


def stats() -> Dict[str, Any]:
    """Quick stats for monitoring — used by _active_learning_audit.py."""
    conn = _get_conn()
    if conn is None:
        return {"enabled": is_enabled(), "total": 0, "active": 0}
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE used_in_prompt) AS active,
                    COUNT(*) FILTER (WHERE example_type='apartment') AS apt,
                    COUNT(*) FILTER (WHERE example_type='villa') AS villa,
                    COUNT(*) FILTER (WHERE example_type='commercial') AS comm,
                    COUNT(*) FILTER (WHERE example_type='multi_unit') AS multi,
                    COUNT(*) FILTER (WHERE example_type='spam_lookalike') AS spam
                FROM parser_examples
                """
            )
            row = dict(cur.fetchone() or {})
    except Exception as e:
        print(f"[active_learning] stats err: {e}")
        row = {}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    row["enabled"] = is_enabled()
    return row


if __name__ == "__main__":
    # Smoke test — pick examples for a Dubai Marina query and render.
    sample = "Dubai Marina 2BR for sale"
    picked = pick_few_shot_examples(sample, n=3)
    print(f"ACTIVE_LEARNING_ENABLED={is_enabled()}")
    print(f"Picked {len(picked)} example(s) for: {sample!r}")
    for i, e in enumerate(picked, 1):
        print(f"  #{i}  type={e.get('example_type')}  "
              f"src={e.get('source_text','')[:60]!r}")
    print("\n--- Rendered prompt body ---")
    print(format_few_shot_prompt(sample, picked))
