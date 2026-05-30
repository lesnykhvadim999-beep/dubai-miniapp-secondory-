"""PHASE W2 — collect all agent contacts from duplicates into canonical.

Adds columns:
  agent_names TEXT[]      — all unique agent names from duplicates
  agent_channels TEXT[]   — all unique source channels (e.g. "@dubai_marina_listings")
  dup_listing_ids BIGINT[] — IDs of all collapsed dupes (for traceability)

For each canonical listing (agent_count > 1), pull every related dup_canonical_id
and aggregate phone, agent_name, source_channel.
"""
from __future__ import annotations
import os
import psycopg2

DB = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set")),
)


def norm_phone(p):
    if not p:
        return None
    s = "".join(c for c in str(p) if c.isdigit())
    if len(s) < 8:
        return None
    return s[-9:] if len(s) > 9 else s


def main():
    c = psycopg2.connect(DB, connect_timeout=10)
    cur = c.cursor()

    # 1. Schema
    cur.execute("""
        ALTER TABLE listings ADD COLUMN IF NOT EXISTS agent_names TEXT[] DEFAULT '{}';
        ALTER TABLE listings ADD COLUMN IF NOT EXISTS agent_channels TEXT[] DEFAULT '{}';
        ALTER TABLE listings ADD COLUMN IF NOT EXISTS dup_listing_ids BIGINT[] DEFAULT '{}';
    """)
    c.commit()
    print("schema updated")

    # 2. Get all canonicals with agent_count > 1
    cur.execute("""
        SELECT id FROM listings
        WHERE v2_extracted_at IS NOT NULL AND is_active=TRUE
          AND COALESCE(agent_count, 1) > 1
    """)
    canons = [r[0] for r in cur.fetchall()]
    print(f"canonicals with multi agents: {len(canons)}")

    updated = 0
    examples = []
    for canon_id in canons:
        # All dups + the canonical itself
        cur.execute("""
            SELECT phone, agent_name, source, seller_username, telegram_chat_id, id
            FROM listings
            WHERE id = %s OR dedup_canonical_id = %s
        """, (canon_id, canon_id))
        rows = cur.fetchall()
        phones = set()
        names = set()
        channels = set()
        ids = []
        for ph, name, src, username, chat_id, lid in rows:
            ids.append(lid)
            np = norm_phone(ph)
            if np:
                phones.add(np)
            if name and name.strip():
                names.add(name.strip())
            # Build channel identifier: prefer @username, fallback to source/chat_id
            if username and username.strip():
                channels.add("@" + username.strip().lstrip("@"))
            elif src and src.strip():
                channels.add(src.strip())

        cur.execute("""
            UPDATE listings
            SET agent_phones  = %s,
                agent_names   = %s,
                agent_channels= %s,
                dup_listing_ids = %s
            WHERE id = %s
        """, (
            sorted(phones), sorted(names), sorted(channels),
            sorted([i for i in ids if i != canon_id]),
            canon_id,
        ))
        updated += 1
        if len(examples) < 5 and (phones or names):
            examples.append((canon_id, len(rows), phones, names, channels))

    c.commit()
    print(f"updated: {updated}")
    print("\nSample (canonical id, dup count, phones, names, channels):")
    for cid, n, p, nm, ch in examples:
        print(f"  id={cid}: {n} dups → {len(p)} phones, {len(nm)} names, {len(ch)} channels")
        if p: print(f"     phones: {sorted(p)[:5]}")
        if nm: print(f"     names:  {sorted(nm)[:5]}")
        if ch: print(f"     chans:  {sorted(ch)[:5]}")

    c.close()


if __name__ == "__main__":
    main()
