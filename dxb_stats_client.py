# -*- coding: utf-8 -*-
"""
dxb_stats_client.py
===================
Тонкий thread-safe клиент к pre-aggregated DLD-агрегатам (area_stats /
building_stats / market_overview), которые наполняет сервис
``dxb-stats-builder`` в БД analytics-бота.

Цель: вместо 3-5 секундного скана raw DLD таблиц получать готовый ответ
из агрегатов за <50ms.

Конфигурация:
    ANALYTICS_DATABASE_URL  (приоритет)
    READ_MODEL_DATABASE_URL (fallback — совместимость с analytics-bot)

Если переменной нет — клиент тихо возвращает None по всем методам и не
ломает бота (caller должен иметь свой fallback).

Безопасность:
    - все вызовы оборачивают исключения и возвращают None / []
    - statement_timeout=5s — read-model должен отвечать мгновенно
    - thread-local connection (1 на поток, lazy-init, авто-reconnect)
    - negative cache 60s — чтобы не дёргать БД на каждое пустое попадание

Схема БД:
    area_stats(area_key, area_name, period_month, property_type, rooms,
               deal_type, deals_count, avg_price_aed, median_price_aed,
               top_quartile_price_aed, avg_price_psf, top_quartile_psf,
               yoy_growth_pct, yoy_growth_top_pct, avg_rental_yield_pct,
               top_rental_yield_pct, computed_at)
    building_stats(building_key, building_name_display, area_key, area_name,
                   period_month, deal_type, rooms, deals_count, avg_price_aed,
                   top_quartile_price_aed, avg_price_psf, top_quartile_psf,
                   last_deal_date, last_deal_price, computed_at)
    market_overview(period_month, total_deals, total_volume_aed, avg_price_aed,
                    median_price_aed, top_quartile_price_aed, yoy_growth_pct,
                    top_growth_pct, rent_avg_yield, rent_top_yield, computed_at)
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

try:
    from stability import ttl_cache as _ttl_cache
except Exception:
    def _ttl_cache(maxsize=500, ttl=300):
        def d(f): return f
        return d

# Circuit breaker for intel_db (analytics read-model) — opens after repeated
# connection failures so callers stop retrying and return cached/None paths.
try:
    from stability import get_breaker as _get_breaker_stats
    _intel_breaker = _get_breaker_stats("intel_db")
except Exception:
    _intel_breaker = None

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_OK = True
except Exception:  # pragma: no cover
    _PSYCOPG2_OK = False

LOG = logging.getLogger("dxb_stats_client")

_DSN = (
    os.environ.get("ANALYTICS_DATABASE_URL")
    or os.environ.get("READ_MODEL_DATABASE_URL")
    or os.environ.get("DXB_STATS_DATABASE_URL")
)

AVAILABLE = bool(_DSN and _PSYCOPG2_OK)

_LOCAL = threading.local()
_NEG_CACHE: Dict[str, float] = {}
_NEG_LOCK = threading.Lock()
_NEG_TTL = 60  # сек


# ──────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────

def _conn():
    # v111: убрали "SELECT 1" перед каждым обращением (это лишний RTT 200-300мс
    # на public proxy). Ping раз в 60с.
    # Circuit breaker: when OPEN (intel DB unhealthy), skip the connect attempt.
    if _intel_breaker is not None and _intel_breaker.is_open():
        raise RuntimeError("intel_db circuit OPEN — skipping connection")
    c = getattr(_LOCAL, "conn", None)
    last_ping = getattr(_LOCAL, "conn_ping", 0)
    now_t = time.time()
    if c is not None:
        if (now_t - last_ping) < 60:
            return c
        try:
            with c.cursor() as cur:
                cur.execute("SELECT 1")
            _LOCAL.conn_ping = now_t
            return c
        except Exception:
            try: c.close()
            except Exception: pass
            _LOCAL.conn = None
    if not _DSN:
        raise RuntimeError("ANALYTICS_DATABASE_URL not configured")
    # Wrap connect via circuit breaker so repeated DB failures trip OPEN.
    if _intel_breaker is not None:
        try:
            c = _intel_breaker.call(
                psycopg2.connect, _DSN,
                connect_timeout=5, application_name="dxb_stats_client",
            )
        except Exception:
            raise
    else:
        c = psycopg2.connect(_DSN, connect_timeout=5,
                             application_name="dxb_stats_client")
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute("SET statement_timeout = 5000")
    _LOCAL.conn = c
    _LOCAL.conn_ping = now_t
    return c


def _neg_hit(key: str) -> bool:
    with _NEG_LOCK:
        exp = _NEG_CACHE.get(key)
        if exp and exp > time.time():
            return True
        if exp:
            _NEG_CACHE.pop(key, None)
    return False


def _neg_set(key: str) -> None:
    with _NEG_LOCK:
        _NEG_CACHE[key] = time.time() + _NEG_TTL


def _area_key(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return name.strip().lower()


def _norm_rooms(rooms: Optional[str]) -> str:
    if not rooms:
        return "all"
    r = str(rooms).strip().lower()
    if "studio" in r:
        return "studio"
    if r.startswith("1") or "1br" in r or "1 b" in r or "1 bed" in r:
        return "1br"
    if r.startswith("2") or "2br" in r or "2 b" in r or "2 bed" in r:
        return "2br"
    if r.startswith("3") or "3br" in r or "3 b" in r or "3 bed" in r:
        return "3br"
    if r.startswith("4") or "4br" in r or "4 b" in r or "4 bed" in r:
        return "4br"
    if r.startswith(("5", "6", "7")):
        return "4br+"
    return "all"


def _norm_prop(prop: Optional[str]) -> str:
    if not prop:
        return "all"
    p = str(prop).strip().lower()
    if "villa" in p:
        return "villa"
    if "town" in p:
        return "townhouse"
    if "office" in p:
        return "office"
    if "shop" in p or "retail" in p:
        return "retail"
    if "apart" in p or "flat" in p or "unit" in p or p == "residential":
        return "apartment"
    return "all"


def _norm_deal(deal_type: Optional[str]) -> str:
    if not deal_type:
        return "sale"
    d = str(deal_type).strip().lower()
    if d.startswith("rent") or d.startswith("ejari") or d.startswith("lease"):
        return "rent"
    return "sale"


def _period_start(months_back: int) -> dt.date:
    today = dt.date.today().replace(day=1)
    y, m = today.year, today.month
    for _ in range(months_back - 1):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return dt.date(y, m, 1)


# ──────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────

def is_available() -> bool:
    """Health-check для бота на старте."""
    if not AVAILABLE:
        return False
    try:
        with _conn().cursor() as cur:
            cur.execute("SELECT count(*) FROM area_stats")
            n = cur.fetchone()[0]
            return n > 0
    except Exception as e:
        LOG.warning("dxb_stats_client not available: %s", e)
        return False


@_ttl_cache(maxsize=500, ttl=300)
def get_area_stats(area_name: str,
                   months: int = 12,
                   property_type: Optional[str] = None,
                   rooms: Optional[str] = None,
                   deal_type: Optional[str] = None,
                   ) -> Optional[Dict[str, Any]]:
    """Агрегат по району.

    Возвращает dict со средневзвешенными метриками за N последних месяцев:
        area_name, deals, avg_price, median_price, top_quartile_price,
        avg_price_psf, top_quartile_psf, yoy_growth_pct, yoy_growth_top_pct,
        avg_rental_yield_pct, top_rental_yield_pct, period_months, source.

    None если данных нет / клиент не настроен.
    """
    if not AVAILABLE:
        return None
    ak = _area_key(area_name)
    if not ak:
        return None
    pt = _norm_prop(property_type)
    rm = _norm_rooms(rooms)
    dl = _norm_deal(deal_type)
    cache_key = f"area:{ak}:{months}:{pt}:{rm}:{dl}"
    if _neg_hit(cache_key):
        return None

    start = _period_start(months)
    sql = """
        SELECT
            MIN(area_name)                                                       AS area_name,
            SUM(deals_count)::int                                                AS deals,
            (SUM(avg_price_aed * deals_count) / NULLIF(SUM(deals_count),0))      AS avg_price,
            AVG(median_price_aed)                                                AS median_price,
            MAX(top_quartile_price_aed)                                          AS top_quartile_price,
            AVG(avg_price_psf)                                                   AS avg_price_psf,
            MAX(top_quartile_psf)                                                AS top_quartile_psf,
            AVG(yoy_growth_pct)                                                  AS yoy_growth_pct,
            MAX(yoy_growth_top_pct)                                              AS yoy_growth_top_pct,
            AVG(avg_rental_yield_pct)                                            AS avg_rental_yield_pct,
            MAX(top_rental_yield_pct)                                            AS top_rental_yield_pct,
            MAX(computed_at)                                                     AS computed_at
        FROM area_stats
        WHERE area_key = %s
          AND property_type = %s
          AND rooms = %s
          AND deal_type = %s
          AND period_month >= %s
    """
    try:
        with _conn().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (ak, pt, rm, dl, start))
            row = cur.fetchone()
    except Exception as e:
        LOG.warning("get_area_stats(%s) failed: %s", area_name, e)
        return None

    if not row or not row.get("deals"):
        _neg_set(cache_key)
        return None

    row = dict(row)
    row["period_months"] = months
    row["source"] = "dxb_stats"
    return row


@_ttl_cache(maxsize=500, ttl=300)
def get_building_stats(building_name: str,
                       months: int = 12,
                       rooms: Optional[str] = None,
                       deal_type: Optional[str] = None,
                       ) -> Optional[Dict[str, Any]]:
    """Агрегат по зданию (по building_key = lower(building_name_display))."""
    if not AVAILABLE:
        return None
    bk = _area_key(building_name)
    if not bk:
        return None
    rm = _norm_rooms(rooms)
    dl = _norm_deal(deal_type)
    cache_key = f"bld:{bk}:{months}:{rm}:{dl}"
    if _neg_hit(cache_key):
        return None

    start = _period_start(months)
    sql = """
        SELECT
            MIN(building_name_display)                                          AS building_name,
            MIN(area_name)                                                      AS area_name,
            SUM(deals_count)::int                                               AS deals,
            (SUM(avg_price_aed*deals_count)/NULLIF(SUM(deals_count),0))         AS avg_price,
            MAX(top_quartile_price_aed)                                         AS top_quartile_price,
            AVG(avg_price_psf)                                                  AS avg_price_psf,
            MAX(top_quartile_psf)                                               AS top_quartile_psf,
            MAX(last_deal_date)                                                 AS last_deal_date,
            MAX(computed_at)                                                    AS computed_at
        FROM building_stats
        WHERE building_key = %s
          AND rooms = %s
          AND deal_type = %s
          AND period_month >= %s
    """
    try:
        with _conn().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (bk, rm, dl, start))
            row = cur.fetchone()
    except Exception as e:
        LOG.warning("get_building_stats(%s) failed: %s", building_name, e)
        return None

    if not row or not row.get("deals"):
        _neg_set(cache_key)
        return None

    row = dict(row)
    row["period_months"] = months
    row["source"] = "dxb_stats"
    return row


@_ttl_cache(maxsize=8, ttl=300)
def get_market_overview(months: int = 12) -> Optional[Dict[str, Any]]:
    """Общий обзор рынка Дубая."""
    if not AVAILABLE:
        return None
    cache_key = f"mkt:{months}"
    if _neg_hit(cache_key):
        return None
    start = _period_start(months)
    sql = """
        SELECT
            SUM(total_deals)::int                                              AS deals,
            SUM(total_volume_aed)                                              AS total_volume,
            (SUM(total_volume_aed)/NULLIF(SUM(total_deals),0))                 AS avg_price,
            AVG(median_price_aed)                                              AS median_price,
            MAX(top_quartile_price_aed)                                        AS top_quartile_price,
            AVG(yoy_growth_pct)                                                AS yoy_growth_pct,
            MAX(top_growth_pct)                                                AS yoy_growth_top_pct,
            AVG(rent_avg_yield)                                                AS rent_avg_yield,
            MAX(rent_top_yield)                                                AS rent_top_yield,
            MAX(computed_at)                                                   AS computed_at
        FROM market_overview
        WHERE period_month >= %s
    """
    try:
        with _conn().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (start,))
            row = cur.fetchone()
    except Exception as e:
        LOG.warning("get_market_overview failed: %s", e)
        return None
    if not row or not row.get("deals"):
        _neg_set(cache_key)
        return None
    row = dict(row)
    row["period_months"] = months
    row["source"] = "dxb_stats"
    return row


def get_top_yield_for_area(area_name: str, months: int = 12) -> Optional[float]:
    """Premium rental yield в районе (top-quartile из агрегатов rent)."""
    s = get_area_stats(area_name, months=months, deal_type="rent")
    if not s:
        return None
    v = s.get("top_rental_yield_pct") or s.get("avg_rental_yield_pct")
    return float(v) if v else None


def get_top_growth_for_area(area_name: str, months: int = 12) -> Optional[float]:
    """Premium YoY-рост в районе (top-quartile YoY)."""
    s = get_area_stats(area_name, months=months, deal_type="sale")
    if not s:
        return None
    v = s.get("yoy_growth_top_pct") or s.get("yoy_growth_pct")
    return float(v) if v else None


# Совместимый объект-оболочка (для кода, ожидающего класс)
class DxbStatsClient:
    """Object-style wrapper. Внутри использует модульные функции."""
    available = AVAILABLE

    def get_area_stats(self, *a, **kw):       return get_area_stats(*a, **kw)
    def get_building_stats(self, *a, **kw):   return get_building_stats(*a, **kw)
    def get_market_overview(self, *a, **kw):  return get_market_overview(*a, **kw)
    def get_top_yield_for_area(self, *a, **kw): return get_top_yield_for_area(*a, **kw)
    def get_top_growth_for_area(self, *a, **kw): return get_top_growth_for_area(*a, **kw)
    def is_available(self):                   return is_available()
