"""_investment_heatmap.py — Investment Heatmap для Dubai.

Источник данных: DLD DB (dld_sale_archive) — реальные сделки. Считаем
percentile_cont(0.5) медиану цены по area за последние 7 дней (cur) и
7 предыдущих дней (prev). WoW window привязан к MAX(instance_date) в БД —
не к CURRENT_DATE (DLD выгружают с лагом).

Генерирует PNG scatter-plot поверх Dubai bbox с цветовой шкалой:
  - red  (рост >+5%)
  - grey (стабильно ±5%)
  - green (падение <-5%)

Возвращает (png_path, summary_text). PNG кэшируется на сутки:
  %TEMP%/heatmap_{YYYY-MM-DD}.png
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from typing import List, Tuple

import psycopg2
import psycopg2.extras

DLD_DB = (
    "postgresql://postgres:REDACTED_ARCHIVE_DB_PASSWORD"
    "@switchback.proxy.rlwy.net:23244/railway"
)

# Approx coords (lat, lon) для районов Dubai. Используем DLD area_name_en,
# плюс несколько алиасов под Intel-style имена.
AREA_COORDS = {
    # Marina / coastal
    "Marsa Dubai":                 (25.0805, 55.1403),   # Dubai Marina
    "Dubai Marina":                (25.0805, 55.1403),
    "Al Thanyah Fifth":            (25.0698, 55.1424),   # JLT
    "Jumeirah Lake Towers":        (25.0698, 55.1424),
    "Palm Jumeirah":               (25.1124, 55.1390),
    "Bluewaters":                  (25.0791, 55.1239),
    "Jumeirah Beach Residence":    (25.0782, 55.1339),
    # Downtown / Business Bay
    "Burj Khalifa":                (25.1972, 55.2744),
    "Downtown Dubai":              (25.1972, 55.2744),
    "Business Bay":                (25.1857, 55.2766),
    "Al Wasl":                     (25.1903, 55.2469),
    "City Walk":                   (25.2073, 55.2575),
    # JVC / JVT / Arjan / Dubailand
    "Al Barsha South Fourth":      (25.0588, 55.2089),   # JVC area
    "Jumeirah Village Circle":     (25.0588, 55.2089),
    "Al Barsha South Third":       (25.0539, 55.1953),   # JVT
    "Al Barshaa South Third":      (25.0539, 55.1953),
    "Al Barsha":                   (25.1117, 55.1953),
    "Al Hebiah Fifth":             (25.0014, 55.2453),   # Damac Hills area
    "Al Hebiah Fourth":            (25.0297, 55.2589),
    "Al Hebiah Third":             (25.0344, 55.2722),
    "Wadi Al Safa 5":              (25.0594, 55.2772),   # Liwan/MBR City vicinity
    "Wadi Al Safa 3":              (25.0628, 55.2853),
    "Wadi Al Safa 2":              (25.1683, 55.3147),   # MBR City
    "Wadi Al Safa 4":              (25.1561, 55.3014),
    # South Dubai
    "Madinat Al Mataar":           (24.8961, 55.1631),   # Dubai South
    "Jabal Ali First":             (24.9836, 55.1722),   # Dubai Investment Park
    "Jabal Ali Industrial First":  (25.0086, 55.1456),
    "Al Khairan First":            (25.1683, 55.3147),
    "Al Yelayiss 1":               (24.9728, 55.2853),
    "Al Yelayiss 2":               (24.9728, 55.2853),
    "Al Yufrah 1":                 (24.9333, 55.3261),
    "Al Yufrah 2":                 (24.9333, 55.3261),
    # Old Dubai / Deira
    "Palm Deira":                  (25.2872, 55.3247),
    "Al Satwa":                    (25.2261, 55.2675),
    "Al Aweer First":              (25.1631, 55.4583),
    "Hadaeq Sheikh Mohammed Bin Rashid": (25.1561, 55.3014),
    # Hills / Ranches
    "Dubai Hills Estate":          (25.1094, 55.2486),
    "Hadaeq Sheikh Mohammed":      (25.1094, 55.2486),
    "Al Yelayiss":                 (24.9669, 55.2733),
    "Mirdif":                      (25.2186, 55.4225),
    "Al Khawaneej Second":         (25.2317, 55.4756),
    "Al Khawaneej First":          (25.2256, 55.4639),
    # Furjan / Discovery
    "Al Furjan":                   (25.0288, 55.1456),
    "Jebel Ali":                   (25.0086, 55.1456),
    # Silicon Oasis / Academic
    "Nadd Hessa":                  (25.1186, 55.3789),
    "Dubai Silicon Oasis":         (25.1186, 55.3789),
    "Wadi Al Amardi":              (25.1614, 55.4083),
    # Arjan / Sports City / Studio City
    "Al Barsha South Second":      (25.0567, 55.2467),
    "Al Barsha South First":       (25.0419, 55.2389),
    "Al Hebiah First":             (25.0367, 55.2419),
    "Al Hebiah Second":            (25.0344, 55.2722),
    # Misc Jumeirah
    "Jumeirah First":              (25.2086, 55.2433),
    "Jumeirah Second":             (25.2186, 55.2533),
    "Jumeirah Third":              (25.2286, 55.2633),
    "Umm Suqeim Third":            (25.1394, 55.1972),
    "Al Sufouh First":              (25.0964, 55.1644),
    "Al Sufouh Second":             (25.1014, 55.1797),
    # JBR area
    "JBR":                         (25.0782, 55.1339),
}

# Bbox для Dubai (lat_min, lat_max, lon_min, lon_max)
DXB_BBOX = (24.85, 25.35, 55.05, 55.55)


# ──────────────────────────────────────────────────────────────────────────────
# Data fetch
# ──────────────────────────────────────────────────────────────────────────────
_FETCH_SQL = """
WITH bounds AS (
  SELECT MAX(instance_date::date) AS dmax FROM dld_sale_archive
),
filtered AS (
  SELECT area_name_en,
         instance_date::date AS d,
         actual_worth::numeric AS price
  FROM dld_sale_archive, bounds
  WHERE property_type_en = 'Unit'
    AND area_name_en IS NOT NULL
    AND actual_worth IS NOT NULL
    AND actual_worth ~ '^[0-9.]+$'
    AND instance_date::date >= bounds.dmax - INTERVAL '14 days'
),
agg AS (
  SELECT area_name_en,
         CASE WHEN d > (SELECT dmax - INTERVAL '7 days' FROM bounds)
              THEN 'cur' ELSE 'prev' END AS bucket,
         COUNT(*) AS n,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY price) AS med
  FROM filtered
  WHERE price BETWEEN 100000 AND 100000000
  GROUP BY 1, 2
)
SELECT area_name_en,
       MAX(CASE WHEN bucket='cur'  THEN med END)::float AS med_cur,
       MAX(CASE WHEN bucket='cur'  THEN n   END)        AS n_cur,
       MAX(CASE WHEN bucket='prev' THEN med END)::float AS med_prev,
       MAX(CASE WHEN bucket='prev' THEN n   END)        AS n_prev,
       (SELECT dmax FROM bounds)                        AS data_max_date
FROM agg
GROUP BY 1
HAVING MAX(CASE WHEN bucket='cur'  THEN n END) >= 3
   AND MAX(CASE WHEN bucket='prev' THEN n END) >= 3
ORDER BY (COALESCE(MAX(CASE WHEN bucket='cur' THEN n END),0)
        + COALESCE(MAX(CASE WHEN bucket='prev' THEN n END),0)) DESC
LIMIT %s
"""


def fetch_area_growth(top_n: int = 30) -> Tuple[List[dict], str]:
    """Возвращает (rows, anchor_date_str).
    Каждый dict: area, current_median, previous_median, growth_pct, volume.
    anchor_date_str — дата, по которой строится «эта неделя» (MAX(instance_date)).
    """
    conn = psycopg2.connect(DLD_DB, connect_timeout=20)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute(_FETCH_SQL, (top_n,))
            rows = c.fetchall()
    finally:
        conn.close()

    anchor = ""
    out: List[dict] = []
    for r in rows:
        cur = r["med_cur"]
        prev = r["med_prev"]
        n_cur = int(r["n_cur"] or 0)
        n_prev = int(r["n_prev"] or 0)
        if not cur or not prev or prev <= 0:
            continue
        growth = (cur - prev) / prev * 100.0
        if abs(growth) > 200:   # отсекаем выбросы
            continue
        anchor = str(r["data_max_date"]) if r["data_max_date"] else anchor
        out.append({
            "area": r["area_name_en"],
            "current_median": float(cur),
            "previous_median": float(prev),
            "growth_pct": float(growth),
            "volume": n_cur + n_prev,
            "n_cur": n_cur,
            "n_prev": n_prev,
        })
    return out, anchor


def _hash_coord(name: str) -> Tuple[float, float]:
    """Stable pseudo-random coord внутри Dubai bbox для неизвестных area."""
    h = abs(hash(name))
    lat = DXB_BBOX[0] + (DXB_BBOX[1] - DXB_BBOX[0]) * ((h % 1000) / 1000.0)
    lon = DXB_BBOX[2] + (DXB_BBOX[3] - DXB_BBOX[2]) * (((h // 1000) % 1000) / 1000.0)
    return lat, lon


def _coord_for(area: str) -> Tuple[float, float]:
    if area in AREA_COORDS:
        return AREA_COORDS[area]
    al = area.lower()
    for k, v in AREA_COORDS.items():
        if k.lower() in al or al in k.lower():
            return v
    return _hash_coord(area)


# ──────────────────────────────────────────────────────────────────────────────
# Rendering
# ──────────────────────────────────────────────────────────────────────────────
def _color_for(growth_pct: float) -> str:
    if growth_pct > 5:
        return "#e63946"   # red — рост
    if growth_pct < -5:
        return "#2a9d8f"   # green — падение
    return "#9aa0a6"       # grey — стабильно


def _fmt_money(v: float) -> str:
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}K"
    return f"{v:.0f}"


def render_heatmap_png(data: List[dict], out_path: str,
                       anchor_date: str = "") -> str:
    """Рендерит scatter PNG. Возвращает out_path."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 8), dpi=120)
    fig.patch.set_facecolor("#0f1419")
    ax.set_facecolor("#1a1f29")

    ax.set_xlim(DXB_BBOX[2], DXB_BBOX[3])
    ax.set_ylim(DXB_BBOX[0], DXB_BBOX[1])
    ax.grid(True, linestyle="--", linewidth=0.3, color="#3a414d", alpha=0.5)
    ax.tick_params(colors="#9aa0a6", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#3a414d")

    xs, ys, cs, ss = [], [], [], []
    for d in data:
        lat, lon = _coord_for(d["area"])
        xs.append(lon)
        ys.append(lat)
        cs.append(_color_for(d["growth_pct"]))
        ss.append(max(80, min(900, d["volume"] * 3)))

    ax.scatter(xs, ys, c=cs, s=ss, alpha=0.78,
               edgecolors="white", linewidths=0.8, zorder=3)

    top = sorted(data, key=lambda x: x["volume"], reverse=True)[:10]
    for d in top:
        lat, lon = _coord_for(d["area"])
        label = f"{d['area']}\n{d['growth_pct']:+.1f}%"
        ax.annotate(label, (lon, lat),
                    textcoords="offset points", xytext=(8, 8),
                    fontsize=8, color="white",
                    bbox=dict(boxstyle="round,pad=0.25",
                              fc="#0f1419", ec="#3a414d", alpha=0.85),
                    zorder=5)

    title = "Dubai Investment Heatmap · WoW (week-over-week)"
    if anchor_date:
        title += f" · anchor: {anchor_date}"
    ax.set_title(
        title + "\nred = +5% growth · grey = ±5% stable · green = -5% drop",
        color="white", fontsize=12, pad=14, loc="left",
    )
    ax.set_xlabel("Longitude", color="#9aa0a6", fontsize=9)
    ax.set_ylabel("Latitude",  color="#9aa0a6", fontsize=9)

    from matplotlib.patches import Patch
    legend_elems = [
        Patch(facecolor="#e63946", edgecolor="white", label="Growth >+5%"),
        Patch(facecolor="#9aa0a6", edgecolor="white", label="Stable ±5%"),
        Patch(facecolor="#2a9d8f", edgecolor="white", label="Drop <-5%"),
    ]
    ax.legend(handles=legend_elems, loc="lower left",
              facecolor="#0f1419", edgecolor="#3a414d",
              labelcolor="white", fontsize=8, framealpha=0.9)

    ax.text(0.99, 0.02, "Vadim Realty · resale-bot",
            transform=ax.transAxes, fontsize=7, color="#5a6270",
            ha="right", va="bottom", alpha=0.7)

    fig.tight_layout()
    fig.savefig(out_path, facecolor=fig.get_facecolor(),
                bbox_inches="tight", dpi=120)
    plt.close(fig)
    return out_path


def build_summary_text(data: List[dict], lang: str = "ru",
                       anchor_date: str = "") -> str:
    """Текстовый summary: топ-5 рост / топ-5 падение."""
    if not data:
        return ("⚠️ Нет данных для построения карты."
                if lang == "ru" else "⚠️ No data available.")

    by_growth = sorted(data, key=lambda x: x["growth_pct"], reverse=True)
    top_up = by_growth[:5]
    top_dn = [d for d in by_growth if d["growth_pct"] < 0][-5:][::-1]

    if lang == "ru":
        header_up = "📈 *Растут больше всего* (WoW"
        if anchor_date:
            header_up += f", на {anchor_date}"
        header_up += "):"
        header_dn = "\n📉 *Падают:*"
        med_lbl = "медиана"
    else:
        header_up = "📈 *Top growth* (WoW"
        if anchor_date:
            header_up += f", as of {anchor_date}"
        header_up += "):"
        header_dn = "\n📉 *Drops:*"
        med_lbl = "median"

    lines = [header_up]
    for i, d in enumerate(top_up, 1):
        if d["growth_pct"] <= 0:
            break
        lines.append(
            f"{i}. {d['area']}: {d['growth_pct']:+.1f}% "
            f"({med_lbl} {_fmt_money(d['current_median'])} AED, "
            f"n={d['n_cur']})"
        )

    if top_dn:
        lines.append(header_dn)
        for i, d in enumerate(top_dn, 1):
            lines.append(
                f"{i}. {d['area']}: {d['growth_pct']:+.1f}% "
                f"({med_lbl} {_fmt_money(d['current_median'])} AED, "
                f"n={d['n_cur']})"
            )

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Cache + public entry
# ──────────────────────────────────────────────────────────────────────────────
def _cache_path() -> str:
    base = tempfile.gettempdir()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return os.path.join(base, f"heatmap_{today}.png")


def get_heatmap(lang: str = "ru", period_code: str = "WoW",
                force: bool = False) -> Tuple[str, str]:
    """Public API: returns (png_path, summary_text). period_code kept for API
    compatibility; current implementation is week-over-week."""
    png_path = _cache_path()
    try:
        data, anchor = fetch_area_growth(top_n=30)
    except Exception as e:
        print(f"[heatmap] fetch err: {e}", flush=True)
        return "", build_summary_text([], lang=lang)
    if not data:
        return "", build_summary_text([], lang=lang, anchor_date=anchor)
    if force or not os.path.exists(png_path):
        render_heatmap_png(data, png_path, anchor_date=anchor)
    summary = build_summary_text(data, lang=lang, anchor_date=anchor)
    return png_path, summary


if __name__ == "__main__":
    import sys
    lang = sys.argv[1] if len(sys.argv) > 1 else "ru"
    p, s = get_heatmap(lang=lang, force=True)
    print("PNG:", p)
    print("size:", os.path.getsize(p) if p and os.path.exists(p) else 0)
    print("---")
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode("ascii", "replace").decode())
