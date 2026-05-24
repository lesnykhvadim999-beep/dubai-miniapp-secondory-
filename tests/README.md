# Parser regression tests

Run before any parser_engine.py commit:

    cd C:\Projects\resale-bot
    pytest tests/ -v

If FAIL — fix before push.

## Files

- `test_parser_engine.py` — 77 pytest cases covering B001-B026 fixes.
- `test_parser_v133.py` — older script-style smoke tests (30 cases, run via `python tests/test_parser_v133.py`).

## Coverage (test_parser_engine.py)

| Class | Covers | Bug refs |
|---|---|---|
| `TestDealTypeV133` | 10 sale + 10 rent + 5 ambiguous + 2 edge | B005 (rental-yield trap) |
| `TestRentPeriod` | yearly/monthly/daily + RU + price-fallback + unknown | rent_period detection |
| `TestPropertyType` | studio/apartment/villa/TH/penthouse/duplex/plot/office | B003 (plot), B004 (office-tower-apartment) |
| `TestArea` | JVC expansion, Burj view ≠ Downtown, full names | B001/B002/B026 |
| `TestBuildingLandmarks` | Burj Khalifa/Palm/Atlantis/SZR/Marina Skyline view-guard, whitelisted residences | B002 + iconic landmarks |
| `TestSubClusterArtifacts` | Cluster N / Phase N / Type B → NULL; real sub-communities (Sidra, Maple) kept | villa audit |
| `TestMarketingPhrasesStoplist` | LAST DAY / Act fast → NULL building | B003 stoplist |

## When tests fail

A test failure means **real bug in parser_engine.py** — fix root cause, do NOT loosen the test.

Each new bug found by these tests should be added to `bug_knowledge_base.md` (see MEMORY.md).

## History

- 24.05.2026 — initial 30+ tests for B005 v133 refactor (`test_parser_v133.py`).
- 25.05.2026 — full pytest suite, 77 tests (`test_parser_engine.py`).
  - Found and fixed: `_RENT_HARD_RE` missing `annual rent` keyword (B027).
