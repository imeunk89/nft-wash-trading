"""Ground-truth check for ask_memory.

The natural-language path generates SQL with an LLM, so it can be confidently
wrong. This computes each answer twice — once through the agent's MCP path, once
with a hand-written query straight to the database — and reports disagreements.

    python -m src.eval_ask
"""
from __future__ import annotations

import re

from . import ask_memory
from .db import connect

# (question, hand-written truth query). The truth query is the contract.
CASES: list[tuple[str, str]] = [
    ("how many wash-trade rings were detected on 2023-02-20?",
     "SELECT count(*) FROM collusion_cases WHERE run_label = 'daily-2023-02-20'"),
    ("how many confirmed patterns are in the agent's memory?",
     "SELECT count(*) FROM flagged_patterns WHERE outcome = 'confirmed'"),
    ("how many detected rings have a proven same-NFT loop?",
     "SELECT count(*) FROM collusion_cases WHERE has_high_confidence"),
    ("how many rings were detected in total across all daily runs?",
     "SELECT count(*) FROM collusion_cases WHERE run_label LIKE 'daily-%'"),
    ("what is the largest number of wallets in any single detected ring?",
     "SELECT max(n_wallets) FROM collusion_cases"),
    ("how many distinct days have daily detection runs?",
     "SELECT count(DISTINCT run_label) FROM collusion_cases WHERE run_label LIKE 'daily-%'"),
]

# Questions that name a result count: the row count itself is the assertion.
ROW_COUNT_CASES: list[tuple[str, int]] = [
    ("which 5 cases have the most self-trades?", 5),
    ("show me the top 3 rings by wallet count", 3),
]


def _first_number(obj) -> float | None:
    """Pull the single scalar out of a one-row result, however it's shaped."""
    if isinstance(obj, list):
        if len(obj) != 1:
            return None
        obj = obj[0]
    if isinstance(obj, dict):
        vals = [v for v in obj.values() if isinstance(v, (int, float))]
        return float(vals[0]) if len(vals) == 1 else None
    return float(obj) if isinstance(obj, (int, float)) else None


def main() -> int:
    with connect() as conn, conn.cursor() as cur:
        truths = []
        for _, q in CASES:
            cur.execute(q)
            truths.append(float(cur.fetchone()[0]))

    passed = failed = unknown = 0
    for (question, truth_sql), truth in zip(CASES, truths):
        try:
            r = ask_memory.ask(question)
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] {question}\n        {type(e).__name__}: {e}\n")
            failed += 1
            continue

        got = _first_number(r["rows"])
        sql = " ".join(r["sql"].split())
        if got is None:
            print(f"[?]    {question}\n       want {truth:g}, got a non-scalar result")
            print(f"       sql: {sql}\n       answer: {r['answer'][:120]}\n")
            unknown += 1
        elif abs(got - truth) < 1e-9:
            print(f"[PASS] {question}  -> {got:g}")
            passed += 1
        else:
            print(f"[FAIL] {question}\n       want {truth:g}, got {got:g}")
            print(f"       agent sql: {sql}")
            print(f"       truth sql: {truth_sql}\n")
            failed += 1

    for question, want_rows in ROW_COUNT_CASES:
        try:
            r = ask_memory.ask(question)
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] {question}\n        {type(e).__name__}: {e}\n")
            failed += 1
            continue
        got = r["row_count"]
        if got == want_rows:
            print(f"[PASS] {question}  -> {got} row(s)")
            passed += 1
        else:
            print(f"[FAIL] {question}\n       asked for {want_rows} rows, got {got}")
            print(f"       agent sql: {' '.join(r['sql'].split())}\n")
            failed += 1

    total = len(CASES) + len(ROW_COUNT_CASES)
    print(f"\n{passed}/{total} correct · {failed} wrong · {unknown} unscored")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
