#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pending_images_retry.py
=======================
Cron-скрипт. Раз в час идёт по pending_image_uploads и для каждой записи,
у которой listing_id уже существует в listings, инсёртит картинку в
listing_images, обновляет cover/has_images у listings, помечает запись
как resolved_at = NOW().

Если listing всё ещё не существует — увеличивает attempts. После
MAX_ATTEMPTS запись остаётся в очереди для ручного разбора (resolved_at
остаётся NULL, но last_error содержит причину).

CLI: python pending_images_retry.py
"""
from __future__ import annotations

import logging
import os
import sys
import time

import psycopg2
from psycopg2.extras import RealDictCursor

LOG = logging.getLogger("piu-retry")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
BATCH_LIMIT = int(os.environ.get("PIU_BATCH_LIMIT", "500"))
MAX_ATTEMPTS = int(os.environ.get("PIU_MAX_ATTEMPTS", "48"))  # ~2 дня по часу


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    if not DATABASE_URL:
        LOG.error("DATABASE_URL is not set")
        return 2

    t0 = time.time()
    c = psycopg2.connect(DATABASE_URL, connect_timeout=20,
                         cursor_factory=RealDictCursor,
                         application_name="piu-retry")
    c.autocommit = False

    resolved = 0
    waiting = 0
    abandoned = 0

    try:
        # Тянем pending, у которых ещё не было слишком много попыток.
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT id, listing_id, image_url, sort_order, attempts
                FROM pending_image_uploads
                WHERE resolved_at IS NULL
                  AND attempts < %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (MAX_ATTEMPTS, BATCH_LIMIT),
            )
            rows = cur.fetchall()
        LOG.info("picked %d pending rows", len(rows))

        # Группируем по listing_id, чтобы один SELECT EXISTS на listing.
        by_listing: dict[int, list[dict]] = {}
        for r in rows:
            by_listing.setdefault(r["listing_id"], []).append(r)

        for lid, items in by_listing.items():
            try:
                with c.cursor() as cur:
                    cur.execute("SELECT 1 FROM listings WHERE id=%s", (lid,))
                    exists = cur.fetchone() is not None
                if not exists:
                    # Просто инкрементим attempts, ждём дальше.
                    with c.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE pending_image_uploads
                            SET attempts = attempts + 1,
                                last_attempt_at = NOW(),
                                last_error = 'listing still missing'
                            WHERE id = ANY(%s)
                            """,
                            ([it["id"] for it in items],),
                        )
                    c.commit()
                    waiting += len(items)
                    # Если приближаемся к лимиту — лог.
                    if items[0]["attempts"] + 1 >= MAX_ATTEMPTS:
                        abandoned += len(items)
                        LOG.warning("listing_id=%s reached MAX_ATTEMPTS (%d urls)",
                                    lid, len(items))
                    continue

                # Listing существует — вставляем картинки.
                with c.cursor() as cur:
                    # сортируем по sort_order чтобы первая стала cover
                    items_sorted = sorted(items, key=lambda x: (x["sort_order"] or 0))
                    for it in items_sorted:
                        cur.execute(
                            """
                            INSERT INTO listing_images(listing_id, url, sort_order)
                            VALUES(%s, %s, %s)
                            ON CONFLICT DO NOTHING
                            """,
                            (lid, it["image_url"], it["sort_order"] or 0),
                        )
                    cover = items_sorted[0]["image_url"]
                    cur.execute(
                        """
                        UPDATE listings
                        SET has_images = TRUE,
                            cover_image_url = COALESCE(cover_image_url, %s)
                        WHERE id = %s
                        """,
                        (cover, lid),
                    )
                    cur.execute(
                        """
                        UPDATE pending_image_uploads
                        SET resolved_at = NOW(),
                            attempts = attempts + 1,
                            last_attempt_at = NOW(),
                            last_error = NULL
                        WHERE id = ANY(%s)
                        """,
                        ([it["id"] for it in items],),
                    )
                c.commit()
                resolved += len(items)
                LOG.info("resolved listing_id=%s urls=%d", lid, len(items))
            except Exception as e:
                c.rollback()
                LOG.error("listing_id=%s retry failed: %s", lid, e)
                try:
                    with c.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE pending_image_uploads
                            SET attempts = attempts + 1,
                                last_attempt_at = NOW(),
                                last_error = %s
                            WHERE id = ANY(%s)
                            """,
                            (str(e)[:500], [it["id"] for it in items]),
                        )
                    c.commit()
                except Exception:
                    c.rollback()
    finally:
        try:
            c.close()
        except Exception:
            pass

    LOG.info("done in %.1fs — resolved=%d waiting=%d abandoned=%d",
             time.time() - t0, resolved, waiting, abandoned)
    return 0


if __name__ == "__main__":
    sys.exit(main())
