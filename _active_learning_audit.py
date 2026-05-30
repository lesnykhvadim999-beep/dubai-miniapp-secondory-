"""Admin CLI for the active learning loop (parser_examples table, issue #9).

Usage:
  py -3 _active_learning_audit.py stats
  py -3 _active_learning_audit.py list  [--days N] [--type apartment]
  py -3 _active_learning_audit.py sample [N]
  py -3 _active_learning_audit.py impact
  py -3 _active_learning_audit.py enable  <id> [<id>...]
  py -3 _active_learning_audit.py disable <id> [<id>...]
  py -3 _active_learning_audit.py pick "<sample text>"

`enable` flips used_in_prompt=TRUE — admin's allow-list, the only
examples that get injected into parser_v2.

`disable` marks examples as harmful (toggle used_in_prompt=FALSE) so they
never get pulled into the few-shot prompt again. Use when you spot a
labeled example that is itself wrong / prompt-drift inducing.
"""
from __future__ import annotations
import os
import sys
import json
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception as e:
    print(f"psycopg2 not available: {e}")
    sys.exit(1)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
TOKENS_PER_EXAMPLE = 200  # rough heuristic for prompt-size impact
EXAMPLES_INJECTED = 3


def _conn():
    if not DATABASE_URL:
        print("DATABASE_URL not set")
        sys.exit(1)
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def cmd_stats() -> None:
    from active_learning import stats
    s = stats()
    print("── parser_examples stats ──────────────────────────")
    print(f"  ACTIVE_LEARNING_ENABLED env: {s.get('enabled')}")
    print(f"  total examples:    {s.get('total', 0)}")
    print(f"  used_in_prompt=ON: {s.get('active', 0)}")
    print(f"  by type:")
    print(f"    apartment      {s.get('apt', 0)}")
    print(f"    villa          {s.get('villa', 0)}")
    print(f"    commercial     {s.get('comm', 0)}")
    print(f"    multi_unit     {s.get('multi', 0)}")
    print(f"    spam_lookalike {s.get('spam', 0)}")


def cmd_list(days: int = 30, etype: str | None = None) -> None:
    where = ["created_at > NOW() - INTERVAL '%s days'" % int(days)]
    args: List = []
    if etype:
        where.append("example_type = %s")
        args.append(etype)
    sql = (
        "SELECT id, example_type, used_in_prompt, reviewed_by, "
        "DATE_TRUNC('day', created_at) AS day, LENGTH(source_text) AS slen "
        "FROM parser_examples WHERE " + " AND ".join(where) +
        " ORDER BY created_at DESC LIMIT 200"
    )
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    conn.close()
    print(f"── parser_examples last {days} days "
          f"({etype or 'all types'}) — {len(rows)} rows ──")
    print(f"{'ID':>6} {'TYPE':<14} {'ON':<3} {'LEN':>4} {'DAY':<11} REVIEWER")
    for r in rows:
        on = "Y" if r.get("used_in_prompt") else "-"
        day = str(r.get("day"))[:10]
        print(f"{r['id']:>6} {(r.get('example_type') or ''):<14} "
              f"{on:<3} {r.get('slen', 0):>4} {day:<11} "
              f"{r.get('reviewed_by') or ''}")


def cmd_sample(n: int = 10) -> None:
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, example_type, used_in_prompt, source_text, "
            "correct_output, llm_original_output "
            "FROM parser_examples ORDER BY RANDOM() LIMIT %s",
            (int(n),),
        )
        rows = cur.fetchall()
    conn.close()
    for r in rows:
        print("=" * 60)
        print(f"#{r['id']}  type={r.get('example_type')}  "
              f"used_in_prompt={r.get('used_in_prompt')}")
        print(f"  source_text ({len(r.get('source_text') or '')} chars):")
        print(f"    {(r.get('source_text') or '')[:200]!r}")
        print("  correct_output:")
        print(f"    {json.dumps(r.get('correct_output'), ensure_ascii=False)[:300]}")
        if r.get("llm_original_output"):
            print("  llm_original_output:")
            print(f"    {json.dumps(r.get('llm_original_output'), ensure_ascii=False)[:300]}")


def cmd_impact() -> None:
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE used_in_prompt) AS active, "
            "AVG(LENGTH(source_text)) AS avg_src "
            "FROM parser_examples"
        )
        r = cur.fetchone() or {}
    conn.close()
    total = int(r.get("total") or 0)
    active = int(r.get("active") or 0)
    avg_src = float(r.get("avg_src") or 0)
    per_ex_tokens = max(TOKENS_PER_EXAMPLE, int(avg_src / 3))
    injected = min(active, EXAMPLES_INJECTED)
    extra_tokens = injected * per_ex_tokens
    print("── prompt size impact ──────────────────────────────")
    print(f"  total examples:               {total}")
    print(f"  active (used_in_prompt=TRUE): {active}")
    print(f"  avg source_text chars:        {avg_src:.0f}")
    print(f"  est tokens per example:       ~{per_ex_tokens}")
    print(f"  examples injected per call:   {injected} "
          f"(cap = {EXAMPLES_INJECTED})")
    print(f"  est EXTRACT prompt overhead:  ~{extra_tokens} tokens / call")
    if active < 100:
        print(f"\n  ⚠️  Recommend NOT enabling yet — collect 100+ "
              f"reviewed examples first (current: {active}).")


def _flip(ids: List[int], value: bool) -> None:
    if not ids:
        print("no ids")
        return
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE parser_examples SET used_in_prompt = %s WHERE id = ANY(%s) "
            "RETURNING id, example_type",
            (value, ids),
        )
        rows = cur.fetchall()
    conn.commit()
    conn.close()
    print(f"set used_in_prompt={value} for {len(rows)} rows: "
          f"{[r['id'] for r in rows]}")


def cmd_enable(ids: List[int]) -> None:
    _flip(ids, True)


def cmd_disable(ids: List[int]) -> None:
    _flip(ids, False)


def cmd_pick(text: str) -> None:
    from active_learning import pick_few_shot_examples, format_few_shot_prompt
    picked = pick_few_shot_examples(text, n=3)
    print(f"picked {len(picked)} examples for: {text!r}")
    for i, e in enumerate(picked, 1):
        print(f"  #{i}  id={e.get('id')}  type={e.get('example_type')}  "
              f"src={(e.get('source_text') or '')[:60]!r}")
    print()
    print(format_few_shot_prompt(text, picked))


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "stats":
        cmd_stats()
    elif cmd == "list":
        days = 30
        etype = None
        i = 2
        while i < len(argv):
            if argv[i] == "--days" and i + 1 < len(argv):
                days = int(argv[i + 1]); i += 2
            elif argv[i] == "--type" and i + 1 < len(argv):
                etype = argv[i + 1]; i += 2
            else:
                i += 1
        cmd_list(days=days, etype=etype)
    elif cmd == "sample":
        n = int(argv[2]) if len(argv) > 2 else 10
        cmd_sample(n)
    elif cmd == "impact":
        cmd_impact()
    elif cmd == "enable":
        cmd_enable([int(x) for x in argv[2:]])
    elif cmd == "disable":
        cmd_disable([int(x) for x in argv[2:]])
    elif cmd == "pick":
        if len(argv) < 3:
            print("usage: pick \"<text>\"")
            return 1
        cmd_pick(argv[2])
    else:
        print(f"unknown command: {cmd}")
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
