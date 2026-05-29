"""Auto-aliases generator — weekly proposal of new BUILDING_ALIASES.

Pipeline:
1. Query DLD `dld_sale_archive` for TOP-30 buildings (last 30 days, COUNT >= 50).
2. For each building check if it (or marketing variations) already present
   in BUILDING_ALIASES dicts of resale-bot's two consumer bots:
     - C:/Projects/roi-bot/ROI-bot/building_search.py
     - C:/Projects/channel-bot-new/Channel-Bot-new/telegram-bot/building_search.py
3. Buildings NOT present → candidates.
4. Build HTML report, send to @vadim_admin_bot.
5. Persist candidates JSON to `_auto_aliases_proposed_{YYYY-MM-DD}.json` for
   later manual apply.

Cron suggestion: `0 9 * * MON` (Mon 09:00 UTC = 13:00 Dubai).

Env:
  ARCHIVE_DATABASE_URL — Railway dld_sale_archive DB
  ADMIN_BOT_TOKEN      — @vadim_admin_bot token
  ADMIN_CHAT_ID        — optional (defaults to Vadim)
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple

# psycopg2 is already in resale-bot deps
import psycopg2  # type: ignore
import psycopg2.extras  # type: ignore

# Reuse the universal admin notifier (same dir)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from admin_notify import admin_notify  # type: ignore  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ARCHIVE_DB_ENV = "ARCHIVE_DATABASE_URL"
TOP_N = 30
MIN_DEALS = 50
WINDOW_DAYS = 30

ALIAS_FILES = [
    Path(r"C:/Projects/roi-bot/ROI-bot/building_search.py"),
    Path(r"C:/Projects/channel-bot-new/Channel-Bot-new/telegram-bot/building_search.py"),
]

OUT_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Step 1 — fetch top buildings from DLD archive
# ---------------------------------------------------------------------------
def fetch_top_buildings() -> List[Tuple[str, int, str]]:
    """Return list of (building_name_en, deals_count, top_area)."""
    dsn = os.environ.get(ARCHIVE_DB_ENV, "").strip()
    if not dsn:
        raise RuntimeError(f"{ARCHIVE_DB_ENV} not set")

    # DLD archive lags ~50 days behind real-time → anchor window on MAX(instance_date)
    sql = """
        WITH max_d AS (
            SELECT MAX(instance_date)::date AS d FROM dld_sale_archive
        )
        SELECT
            building_name_en,
            COUNT(*) AS deals,
            MODE() WITHIN GROUP (ORDER BY area_name_en) AS top_area
        FROM dld_sale_archive, max_d
        WHERE building_name_en IS NOT NULL
          AND TRIM(building_name_en) <> ''
          AND instance_date::date >= (max_d.d - (%s || ' days')::interval)::date
        GROUP BY building_name_en
        HAVING COUNT(*) >= %s
        ORDER BY deals DESC
        LIMIT %s
    """
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (WINDOW_DAYS, MIN_DEALS, TOP_N))
            return [(r[0], int(r[1]), r[2] or "") for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Step 2 — parse BUILDING_ALIASES dicts from existing bots
# ---------------------------------------------------------------------------
def _extract_aliases(py_file: Path) -> Tuple[Set[str], Set[str]]:
    """Return (keys_lower, canonical_values_lower) from BUILDING_ALIASES."""
    keys: Set[str] = set()
    vals: Set[str] = set()
    if not py_file.exists():
        return keys, vals
    src = py_file.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return keys, vals
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        else:
            continue
        if name != "BUILDING_ALIASES":
            continue
        value = node.value
        if not isinstance(value, ast.Dict):
            continue
        for k, v in zip(value.keys, value.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.add(k.value.strip().lower())
            if isinstance(v, ast.List):
                for elt in v.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        vals.add(elt.value.strip().lower())
        break
    return keys, vals


def load_existing_aliases() -> Tuple[Set[str], Set[str]]:
    all_keys: Set[str] = set()
    all_vals: Set[str] = set()
    for f in ALIAS_FILES:
        k, v = _extract_aliases(f)
        all_keys.update(k)
        all_vals.update(v)
    return all_keys, all_vals


# ---------------------------------------------------------------------------
# Step 3 — generate marketing variations and decide if building is "new"
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _short_name(building: str) -> str:
    """Drop trailing developer / type tokens to get a short token."""
    s = _norm(building)
    # strip common suffixes
    s = re.sub(r"\b(tower|residences?|residence|building|apartments?|by .+)$", "", s).strip()
    return s


def variations_for(building: str) -> List[str]:
    short = _short_name(building) or _norm(building)
    full = _norm(building)
    return list({
        full,
        short,
        f"{short} residences",
        f"{short} residence",
        f"{short} tower",
        f"the {short}",
        f"{short} by emaar",
        f"{short} by damac",
        f"{short} by danube",
        f"{short} by sobha",
        f"{short} by binghatti",
    })


def is_covered(building: str, keys: Set[str], vals: Set[str]) -> bool:
    name_l = _norm(building)
    if name_l in keys or name_l in vals:
        return True
    # also covered if any alias key is substring of building name or vice-versa
    for k in keys:
        if k and (k in name_l or name_l in k):
            return True
    for v in vals:
        if v and (v == name_l or v in name_l or name_l in v):
            return True
    return False


# ---------------------------------------------------------------------------
# Step 4 — build report + persist JSON
# ---------------------------------------------------------------------------
def build_report(candidates: List[Dict]) -> Tuple[str, str]:
    """Return (html_text, batch_hash)."""
    if not candidates:
        return ("✅ <b>Weekly aliases proposal</b>\n\nНет новых buildings без alias за неделю.", "")

    payload = json.dumps([c["building"] for c in candidates], ensure_ascii=False)
    batch_hash = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]

    lines = [
        "📋 <b>Weekly aliases proposal</b>",
        "",
        f"Топ-{TOP_N} buildings в DLD за {WINDOW_DAYS} дней (>= {MIN_DEALS} deals).",
        f"Новых без alias: <b>{len(candidates)}</b>",
        "",
    ]
    for c in candidates[:25]:  # Telegram 4096 char limit safety
        suggested = c["suggested_key"]
        canonical = c["building"]
        lines.append(
            f"• <b>{canonical}</b> ({c['deals']} deals, {c['area']}) — нет alias\n"
            f"   <code>\"{suggested}\": [\"{canonical}\"]</code>"
        )
    if len(candidates) > 25:
        lines.append(f"\n…и ещё {len(candidates) - 25} в JSON-файле.")
    lines.append("")
    lines.append(f"Apply all: <code>/apply_aliases {batch_hash}</code>")
    return ("\n".join(lines), batch_hash)


def persist_json(candidates: List[Dict], batch_hash: str) -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = OUT_DIR / f"_auto_aliases_proposed_{date_str}.json"
    out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "batch_hash": batch_hash,
        "window_days": WINDOW_DAYS,
        "min_deals": MIN_DEALS,
        "candidates": candidates,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print(f"[auto_aliases] start {datetime.now(timezone.utc).isoformat()}")

    try:
        top = fetch_top_buildings()
    except Exception as e:
        msg = f"⚠️ <b>auto_aliases</b> failed: <code>{e}</code>"
        print(msg)
        admin_notify(msg)
        return 1
    print(f"[auto_aliases] fetched {len(top)} top buildings")

    keys, vals = load_existing_aliases()
    print(f"[auto_aliases] existing alias keys={len(keys)}, canonical values={len(vals)}")

    candidates: List[Dict] = []
    for building, deals, area in top:
        if is_covered(building, keys, vals):
            continue
        short = _short_name(building) or _norm(building)
        candidates.append({
            "building": building,
            "deals": deals,
            "area": area,
            "suggested_key": short,
            "variations": variations_for(building),
        })

    print(f"[auto_aliases] candidates: {len(candidates)}")

    text, batch_hash = build_report(candidates)
    out_file = persist_json(candidates, batch_hash) if candidates else None
    if out_file:
        print(f"[auto_aliases] wrote {out_file}")

    ok = admin_notify(text)
    print(f"[auto_aliases] admin_notify sent={ok}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
