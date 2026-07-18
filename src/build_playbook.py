"""Populate the playbook: confirmed collusion cases -> text signal -> Bedrock
embedding -> flagged_patterns (CockroachDB vector index).

This is the memory-formation step: each confirmed case becomes a retrievable
pattern the agent can match future activity against.

    python -m src.build_playbook
"""
from __future__ import annotations

from . import bedrock
from .db import connect
from .playbook import add_pattern


def case_to_signal(case: dict) -> str:
    """A compact natural-language description of a collusion case (what gets embedded)."""
    kind = ("same NFTs recirculated to a prior holder (high-confidence matched-order ring)"
            if case["has_high_confidence"]
            else "repeated closed-loop trading among the same wallets (candidate ring)")
    return (
        f"Wash-trading collusion cell of {case['n_wallets']} wallets that executed "
        f"{case['n_trades']} trades in {case['n_rings']} closed loop(s) over "
        f"{case['active_days']:.0f} days across {case['n_collections']} NFT collection(s); "
        f"{kind}. Signature: near-symmetric round-trip self-dealing to fake volume."
    )


def run() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE flagged_patterns")
        conn.commit()
        cur.execute(
            "SELECT case_id, n_wallets, n_rings, has_high_confidence, n_trades, "
            "active_days, n_collections FROM collusion_cases ORDER BY n_trades DESC"
        )
        cols = [d[0] for d in cur.description]
        cases = [dict(zip(cols, r)) for r in cur.fetchall()]

    print(f"embedding {len(cases)} confirmed cases into the playbook...")
    for i, case in enumerate(cases, 1):
        signal = case_to_signal(case)
        emb = bedrock.embed(signal)
        add_pattern(
            category="wash_trade_ring",
            description=signal,
            embedding=emb,
            source_case=case["case_id"],
            outcome="confirmed",
        )
        if i % 10 == 0 or i == len(cases):
            print(f"  {i}/{len(cases)}")

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM flagged_patterns")
        print(f"playbook now holds {cur.fetchone()[0]} embedded patterns.")


if __name__ == "__main__":
    run()
