"""Generate app/demo_data.json — precomputed responses for the public read-only demo.

The deployed demo runs with a read-only CockroachDB user and NO AWS credentials, so
every Bedrock-backed response (and every write) is replayed from this snapshot
instead of being recomputed. The values are real: they come from one genuine run
against the live cluster and live Bedrock — this only freezes them.

    python -m src.make_demo_snapshot     # needs admin DB creds + AWS keys

Read-only endpoints (cases, catch, case detail, stats) are NOT snapshotted; the
demo serves those live from the read-only user, so the evidence stays genuine.
"""
from __future__ import annotations

import json

from app.main import (Activity, Question, api_ask, api_case_basis, api_cases,
                      api_catch, api_confirm, api_reject, api_reset, api_triage)
from . import config

SCHEME_V1 = ("Two fresh wallets funded minutes apart by the same parent address traded a "
             "single NFT back and forth about forty times at nearly identical prices.")
SCHEME_V2 = ("A sibling pair of wallets bankrolled from one common source ping-ponged the "
             "same token dozens of times at flat prices.")
BENIGN_V1 = ("One collector bought ten different NFTs from ten unrelated sellers during a "
             "marketplace fee-rebate promotion week.")
BENIGN_V2 = ("A user purchased a batch of NFTs from many distinct sellers while trading-fee "
             "rebates were live.")
ASK_EGS = [
    "how many rings were detected in total across all daily runs?",
    "how many detected rings have a proven same-NFT loop?",
    "what is the largest number of wallets in any single detected ring?",
    "which 5 cases have the most self-trades?",
]


def run() -> None:
    api_reset()                      # start from the 37-pattern baseline
    snap: dict = {"triage": {}, "confirm": {}, "reject": {}, "ask": {}, "case_basis": {}}

    # --- the guided story, replayed in order so the stats/distances are the real ones
    print("story...")
    snap["triage"][SCHEME_V1] = api_triage(Activity(activity=SCHEME_V1))
    snap["confirm"][SCHEME_V1] = api_confirm(Activity(activity=SCHEME_V1))
    snap["triage"][SCHEME_V2] = api_triage(Activity(activity=SCHEME_V2))
    snap["triage"][BENIGN_V1] = api_triage(Activity(activity=BENIGN_V1))
    snap["reject"][BENIGN_V1] = api_reject(Activity(activity=BENIGN_V1))
    snap["triage"][BENIGN_V2] = api_triage(Activity(activity=BENIGN_V2))
    d1 = snap["triage"][SCHEME_V1]["nearest_confirmed"]["distance"]
    d2 = snap["triage"][SCHEME_V2]["nearest_confirmed"]["distance"]
    print(f"  learning captured: {d1} -> {d2}")

    # --- Ask the memory (managed MCP server) examples
    print("ask examples...")
    for q in ASK_EGS:
        snap["ask"][q] = api_ask(Question(question=q))

    # --- ruling basis for every case a judge can click
    ids = [c["case_id"] for c in api_cases()] + [c["case_id"] for c in api_catch()["catches"]]
    ids = list(dict.fromkeys(ids))
    print(f"case_basis for {len(ids)} cases...")
    for i, cid in enumerate(ids, 1):
        snap["case_basis"][cid] = api_case_basis(cid)
        if i % 25 == 0:
            print(f"  {i}/{len(ids)}")

    snap["reset"] = api_reset()      # and leave the cluster on the clean baseline

    out = config.ROOT / "app" / "demo_data.json"
    out.write_text(json.dumps(snap, indent=1, default=str))
    print(f"wrote {out}  ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    run()
