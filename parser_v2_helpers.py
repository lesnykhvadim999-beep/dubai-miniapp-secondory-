"""Parser v2 helpers — runtime utilities used by the parser and the
multi-message merge phase.

Currently exposes:

  find_message_siblings(chat_id, author, sent_at, conn, window_minutes=2)
      Returns list of listing-id rows that look like siblings of the
      given message — same chat, same author, sent within ``window_minutes``
      either side of ``sent_at``. Ordered by message_date ASC.

Designed to be cheap (single indexed query) so it can be called from
hot paths in telethon_parser / staging processor.
"""
from __future__ import annotations

from datetime import timedelta
from typing import List, Dict, Any, Optional


def find_message_siblings(
    chat_id: str,
    author: Optional[str],
    sent_at,
    conn,
    window_minutes: int = 2,
    include_inactive: bool = False,
) -> List[Dict[str, Any]]:
    """Return rows that are likely siblings of a given message.

    Siblings = same telegram_chat_id + same seller_username, sent within
    ``window_minutes`` of ``sent_at`` (either direction).

    Parameters
    ----------
    chat_id : str
        telegram_chat_id (string).
    author : str | None
        seller_username. If None, returns [].
    sent_at : datetime
        message_date of the anchor message.
    conn : psycopg2 connection
        Existing DB connection (caller is responsible for commit/close).
    window_minutes : int
        Half-width of the sibling window in minutes. Default 2.
    include_inactive : bool
        If True, also returns rows with is_active=FALSE (e.g. already merged).

    Returns
    -------
    list[dict]
        Each dict has keys:
        id, telegram_chat_id, telegram_message_id, seller_username,
        message_date, area, price, building, bedrooms, size_sqft,
        v2_source_message_text, has_images, is_active, status.
        Ordered by message_date ASC (oldest first).
    """
    if not chat_id or not author or sent_at is None:
        return []

    window = timedelta(minutes=window_minutes)
    lo = sent_at - window
    hi = sent_at + window

    active_clause = "" if include_inactive else " AND is_active = TRUE "

    sql = f"""
        SELECT id,
               telegram_chat_id,
               telegram_message_id,
               seller_username,
               message_date,
               area,
               price,
               building,
               bedrooms,
               size_sqft,
               v2_source_message_text,
               has_images,
               is_active,
               status
        FROM listings
        WHERE telegram_chat_id = %s
          AND seller_username  = %s
          AND message_date BETWEEN %s AND %s
          {active_clause}
        ORDER BY message_date ASC, id ASC
    """
    cur = conn.cursor()
    try:
        cur.execute(sql, (str(chat_id), author, lo, hi))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        cur.close()


def is_complete(row: Dict[str, Any]) -> bool:
    """True if a listing row already has all 4 core fields filled."""
    return (
        bool(row.get("area"))
        and row.get("price") is not None
        and row.get("bedrooms") is not None
        and bool(row.get("building"))
    )


def pick_primary(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the 'primary' listing from a sibling group.

    Heuristic:
      1. Prefer the row with the longest v2_source_message_text (most context).
      2. Tie-break: row that already has price.
      3. Tie-break: row with the smallest id (earliest inserted).
    """
    if not rows:
        return None

    def score(r: Dict[str, Any]):
        txt_len = len(r.get("v2_source_message_text") or "")
        has_price = 1 if r.get("price") is not None else 0
        return (txt_len, has_price, -int(r.get("id") or 0))

    return max(rows, key=score)
