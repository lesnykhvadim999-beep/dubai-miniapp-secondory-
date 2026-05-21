# -*- coding: utf-8 -*-
"""
dashboard.py — Streamlit admin dashboard.

Run locally:
  streamlit run dashboard.py

Deploy on Railway as a SEPARATE service:
  1. Add a new service from the same repo
  2. Start command:  streamlit run dashboard.py --server.port=$PORT --server.address=0.0.0.0
  3. Set env DATABASE_URL (same as bot) and DASHBOARD_PASSWORD (any string)

Single-page password-protected admin view:
  • Today / 7d / 30d KPIs
  • Listings by emirate / area / property_type
  • Conversions (leads → buildings)
  • Audit funnel & top reasons
  • Hot deals
  • Charts over time
"""
import os
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

DB_URL = os.environ.get("DATABASE_URL", "")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")


def get_conn():
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)


def query(sql, params=()):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()


# ── Login ────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Resale Bot · Admin", layout="wide", page_icon="🏠")

if PASSWORD:
    if "auth" not in st.session_state:
        st.session_state.auth = False
    if not st.session_state.auth:
        pwd = st.text_input("Password", type="password")
        if st.button("Login"):
            if pwd == PASSWORD:
                st.session_state.auth = True
                st.rerun()
            else:
                st.error("Wrong password")
        st.stop()

# ── Header ────────────────────────────────────────────────────────────────────
st.title("🏠 Dubai Resale Bot · Admin Dashboard")
st.caption(datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))

# ── KPI row ──────────────────────────────────────────────────────────────────
col1, col2, col3, col4, col5 = st.columns(5)
k = query("""
    SELECT
      (SELECT COUNT(*) FROM listings WHERE is_active=TRUE
         AND (is_audit IS NULL OR is_audit=FALSE)) AS visible,
      (SELECT COUNT(*) FROM listings WHERE is_active=TRUE AND is_audit=TRUE) AS hidden,
      (SELECT COUNT(*) FROM listings WHERE is_active=TRUE AND is_hot_deal=TRUE
         AND (is_audit IS NULL OR is_audit=FALSE)) AS hot,
      (SELECT COUNT(*) FROM listings WHERE created_at > NOW() - INTERVAL '24 hours'
         AND (is_audit IS NULL OR is_audit=FALSE)) AS new_24h,
      (SELECT COUNT(*) FROM users) AS users_total,
      (SELECT COUNT(*) FROM users WHERE last_seen > NOW() - INTERVAL '24 hours') AS active_24h,
      (SELECT COUNT(*) FROM leads WHERE created_at > NOW() - INTERVAL '24 hours') AS leads_24h,
      (SELECT COUNT(*) FROM favorites) AS favs,
      (SELECT COUNT(*) FROM price_alerts WHERE is_active=TRUE) AS alerts
""").iloc[0]

col1.metric("Visible listings", f"{int(k['visible']):,}")
col2.metric("Hidden (audit)",   f"{int(k['hidden']):,}")
col3.metric("New (24h)",        f"{int(k['new_24h']):,}", f"+{int(k['hot'])} hot")
col4.metric("Active users (24h)", f"{int(k['active_24h']):,}", f"{int(k['users_total'])} total")
col5.metric("Leads (24h)",      f"{int(k['leads_24h']):,}", f"❤️ {int(k['favs'])}  🔔 {int(k['alerts'])}")

st.divider()

# ── Listings by emirate / type ────────────────────────────────────────────────
left, right = st.columns(2)
with left:
    st.subheader("By Emirate")
    df = query("""
        SELECT COALESCE(emirate,'—') AS emirate, COUNT(*) AS listings
        FROM listings WHERE is_active=TRUE AND (is_audit IS NULL OR is_audit=FALSE)
        GROUP BY emirate ORDER BY listings DESC
    """)
    st.bar_chart(df.set_index("emirate"))

with right:
    st.subheader("By Property Type")
    df = query("""
        SELECT COALESCE(property_type,'?') AS type, COUNT(*) AS listings
        FROM listings WHERE is_active=TRUE AND (is_audit IS NULL OR is_audit=FALSE)
        GROUP BY property_type ORDER BY listings DESC
    """)
    st.bar_chart(df.set_index("type"))

st.divider()

# ── New listings over time ────────────────────────────────────────────────────
st.subheader("New listings per day (last 30 days)")
df = query("""
    SELECT DATE(created_at) AS day, COUNT(*) AS new_listings
    FROM listings WHERE created_at > NOW() - INTERVAL '30 days'
      AND (is_audit IS NULL OR is_audit=FALSE)
    GROUP BY day ORDER BY day
""")
if not df.empty:
    df["day"] = pd.to_datetime(df["day"])
    st.line_chart(df.set_index("day"))

st.divider()

# ── Top areas by listings ─────────────────────────────────────────────────────
left, right = st.columns(2)
with left:
    st.subheader("Top 15 Areas")
    df = query("""
        SELECT COALESCE(area,'—') AS area, COUNT(*) AS listings,
               ROUND(AVG(price)::numeric/1000000, 2) AS avg_price_M
        FROM listings WHERE is_active=TRUE AND deal_type='sale'
          AND (is_audit IS NULL OR is_audit=FALSE)
        GROUP BY area ORDER BY listings DESC LIMIT 15
    """)
    st.dataframe(df, use_container_width=True, hide_index=True)

with right:
    st.subheader("Top 15 Buildings by Leads (30d)")
    df = query("""
        SELECT lst.building, lst.area, COUNT(*) AS leads
        FROM leads l JOIN listings lst ON lst.id = l.listing_id
        WHERE l.created_at > NOW() - INTERVAL '30 days'
          AND l.action IN ('book','contact','save','view')
        GROUP BY lst.building, lst.area
        ORDER BY leads DESC LIMIT 15
    """)
    st.dataframe(df, use_container_width=True, hide_index=True)

st.divider()

# ── Audit funnel ──────────────────────────────────────────────────────────────
st.subheader("Audit reasons (top 20)")
df = query("""
    SELECT
      SPLIT_PART(audit_reason, ';', 1) AS reason,
      COUNT(*) AS cnt
    FROM listings WHERE is_active=TRUE AND is_audit=TRUE
    GROUP BY reason ORDER BY cnt DESC LIMIT 20
""")
st.dataframe(df, use_container_width=True, hide_index=True)

st.divider()

# ── Hot deals ─────────────────────────────────────────────────────────────────
st.subheader("Live Hot Deals (sale, last 7 days)")
df = query("""
    SELECT id, building, area, emirate, bedrooms, size_sqft, price,
           deal_quality, price_vs_market_percent
    FROM listings
    WHERE is_active=TRUE AND is_hot_deal=TRUE AND deal_type='sale'
      AND (is_audit IS NULL OR is_audit=FALSE)
      AND created_at > NOW() - INTERVAL '7 days'
    ORDER BY price_vs_market_percent ASC NULLS LAST
    LIMIT 30
""")
st.dataframe(df, use_container_width=True, hide_index=True)

st.caption("Auto-refresh: press F5. Data source: Railway Postgres (resale-bot).")
