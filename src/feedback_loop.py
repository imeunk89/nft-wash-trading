"""The feedback loop — how the agent's memory gets smarter with each review.

Flow:
  1. triage(): new activity is scored against memory BOTH ways —
       nearest confirmed precedent  -> reason to flag
       nearest rejected precedent   -> reason to suppress (a known false positive)
  2. an analyst reviews a flagged item and records a verdict:
       confirm() -> the activity is ADDED to memory as a confirmed pattern
                    (similar future schemes match closer = caught earlier)
       reject()  -> the activity is stored as a rejected pattern
                    (similar future noise is auto-suppressed = mistake not repeated)

Both verdicts are durable rows in CockroachDB, so the improvement survives
restarts and is shared by every agent instance — memory, not session state.

Thresholds are tuned from observed separation on real data (true wash-trade
match ~0.48 cosine distance, unrelated activity ~0.86).

    python -m src.feedback_loop triage  "three wallets bounced one NFT 200 times"
    python -m src.feedback_loop confirm "..." --case C001
    python -m src.feedback_loop reject  "..."
"""
from __future__ import annotations

import argparse

from . import bedrock
from .playbook import add_pattern, search_similar

# Cosine-distance knobs (see module docstring for the empirical basis).
FLAG_MAX_DISTANCE = 0.60      # closer than this to a confirmed pattern -> flag
SUPPRESS_MARGIN = 0.05        # rejected precedent this much closer than confirmed -> suppress


def confirm(activity_text: str, source_case: str | None = None,
            category: str = "wash_trade") -> str:
    """Analyst verdict: real. The activity itself becomes retrievable memory."""
    pid = add_pattern(category, activity_text, bedrock.embed(activity_text),
                      source_case, outcome="confirmed")
    return pid


def reject(activity_text: str, category: str = "wash_trade") -> str:
    """Analyst verdict: false positive. Remembered so it stops firing."""
    pid = add_pattern(category, activity_text, bedrock.embed(activity_text),
                      None, outcome="rejected")
    return pid


def triage(activity_text: str, k: int = 3) -> dict:
    """Score new activity against memory and return a verdict + evidence."""
    emb = bedrock.embed(activity_text)
    confirmed = search_similar(emb, k=k)                       # trusted memory
    rejected = search_similar(emb, k=1, outcomes=("rejected",))  # known noise

    d_conf = confirmed[0]["cosine_distance"] if confirmed else None
    d_rej = rejected[0]["cosine_distance"] if rejected else None

    if (d_rej is not None and d_conf is not None
            and d_rej + SUPPRESS_MARGIN < d_conf):
        verdict = "suppress"   # closest thing in memory is a known false positive
    elif d_rej is not None and d_conf is None:
        verdict = "suppress"
    elif d_conf is not None and d_conf <= FLAG_MAX_DISTANCE:
        verdict = "flag"       # resembles a confirmed manipulation precedent
    else:
        verdict = "novel"      # nothing close in memory — surface for human review

    return {
        "verdict": verdict,
        "nearest_confirmed": confirmed[0] if confirmed else None,
        "nearest_rejected": rejected[0] if rejected else None,
        "matches": confirmed,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Review loop: triage / confirm / reject")
    p.add_argument("action", choices=["triage", "confirm", "reject"])
    p.add_argument("activity", help="Free-text description of the activity")
    p.add_argument("--case", default=None, help="Related case id (for confirm)")
    args = p.parse_args()

    if args.action == "triage":
        r = triage(args.activity)
        print(f"verdict: {r['verdict'].upper()}")
        if r["nearest_confirmed"]:
            m = r["nearest_confirmed"]
            print(f"  nearest confirmed: {m['cosine_distance']:.4f}  "
                  f"({m['source_case']}) {m['description'][:70]}")
        if r["nearest_rejected"]:
            m = r["nearest_rejected"]
            print(f"  nearest rejected : {m['cosine_distance']:.4f}  {m['description'][:70]}")
    elif args.action == "confirm":
        pid = confirm(args.activity, args.case)
        print(f"learned as confirmed pattern {pid}")
    else:
        pid = reject(args.activity)
        print(f"learned as rejected pattern {pid}")


if __name__ == "__main__":
    main()
