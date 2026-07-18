"""Demo: the memory visibly gets smarter as cases are reviewed.

Two measurable behaviors, using real Bedrock embeddings + the live CockroachDB
playbook (the 37 confirmed collusion cases as base memory):

  A. RECALL grows  — confirming a new scheme makes the next, differently-worded
                     variant match much CLOSER than anything did before.
  B. PRECISION grows — rejecting a false positive makes similar benign activity
                     auto-suppress instead of firing again.

Idempotent: patterns learned by this demo carry source_case 'DEMO-…' and are
wiped at the start of each run, so the base playbook is never polluted.

    python -m src.demo_learning
"""
from __future__ import annotations

from .db import connect
from .feedback_loop import confirm, reject, triage

# A scheme variant NOT in the base playbook (funding-pattern flavored).
SCHEME_V1 = ("Two fresh wallets funded minutes apart by the same parent address "
             "traded a single NFT back and forth about forty times at nearly "
             "identical prices.")
# The 'next day' variant — same scheme, completely different wording.
SCHEME_V2 = ("A sibling pair of wallets bankrolled from one common source "
             "ping-ponged the same token dozens of times at flat prices.")

# Benign activity that superficially smells like volume games.
BENIGN_V1 = ("One collector bought ten different NFTs from ten unrelated sellers "
             "during a marketplace fee-rebate promotion week.")
BENIGN_V2 = ("A user purchased a batch of NFTs from many distinct sellers while "
             "trading-fee rebates were live.")


def cleanup() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM flagged_patterns WHERE source_case LIKE 'DEMO-%' "
                    "OR (outcome = 'rejected' AND source_case IS NULL)")
        conn.commit()


def show(label: str, r: dict) -> float | None:
    d = r["nearest_confirmed"]["cosine_distance"] if r["nearest_confirmed"] else None
    print(f"  {label}")
    print(f"    verdict: {r['verdict'].upper()}"
          + (f"   (nearest confirmed {d:.4f}"
             f" — {r['nearest_confirmed']['source_case']})" if d is not None else ""))
    if r["nearest_rejected"]:
        print(f"    nearest rejected: {r['nearest_rejected']['cosine_distance']:.4f}")
    return d


def main() -> None:
    cleanup()
    print("=" * 72)
    print("A. RECALL — confirming one case makes the next variant match closer")
    print("=" * 72)
    print("\n[1] New scheme arrives (not yet in memory):")
    d0 = show("scheme v1", triage(SCHEME_V1))

    print("\n[2] Analyst CONFIRMS it -> written to CockroachDB as memory")
    confirm(SCHEME_V1, source_case="DEMO-LEARNED-1")

    print("\n[3] Next day, the same scheme reappears in different words:")
    d1 = show("scheme v2", triage(SCHEME_V2))

    if d0 is not None and d1 is not None:
        print(f"\n  >> match distance {d0:.4f} -> {d1:.4f} "
              f"(improved {100 * (d0 - d1) / d0:.0f}%) — caught earlier & more confidently")

    print()
    print("=" * 72)
    print("B. PRECISION — rejecting a false positive stops repeat mistakes")
    print("=" * 72)
    print("\n[4] Benign burst of buying gets triaged:")
    show("benign v1", triage(BENIGN_V1))

    print("\n[5] Analyst REJECTS it -> remembered as a known false positive")
    reject(BENIGN_V1)

    print("\n[6] Similar benign activity next week:")
    show("benign v2", triage(BENIGN_V2))

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT outcome, count(*) FROM flagged_patterns GROUP BY outcome")
        counts = dict(cur.fetchall())
    print(f"\nplaybook now: {counts}")


if __name__ == "__main__":
    main()
