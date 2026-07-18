"""Compute per-token rarity for a collection from Alchemy metadata.

Pulls every token's traits via Alchemy getNFTsForContract, then scores rarity
with the standard trait-rarity formula:

    rarity_score(token) = sum over trait_types of  1 / P(the token's value)

where P(value) is that trait value's frequency across the whole collection.
A token gets credit for *missing* a common trait too (absence is treated as its
own value), which is how special/rare tokens (fewer traits) score highest.

Usage:
    python -m src.nft_rarity --contract 0xED5AF388653567Af2F388E6224dC7C4b3241C544 --label azuki

Writes data/raw/rarity_<label>.csv (token_id, rarity_score, rarity_rank, type).
"""
from __future__ import annotations

import argparse
import time
from collections import Counter, defaultdict

import pandas as pd
import requests

from . import config

NONE = "__none__"


def fetch_all_tokens(contract: str) -> list[dict]:
    url = config.alchemy_nft_url("getNFTsForContract")
    tokens: list[dict] = []
    page_key = None
    page = 0
    while True:
        params = {"contractAddress": contract, "withMetadata": "true", "limit": 100}
        if page_key:
            params["pageKey"] = page_key
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        body = r.json()
        for n in body.get("nfts", []):
            attrs = (n.get("raw", {}) or {}).get("metadata", {}) or {}
            attrs = attrs.get("attributes") or []
            traits = {
                a.get("trait_type"): a.get("value")
                for a in attrs
                if isinstance(a, dict) and a.get("trait_type") is not None
            }
            tokens.append({"token_id": int(n["tokenId"]), "traits": traits})
        page += 1
        page_key = body.get("pageKey")
        if page % 10 == 0 or not page_key:
            print(f"  page {page}: {len(tokens)} tokens")
        if not page_key:
            break
        time.sleep(0.15)
    return tokens


def compute_rarity(tokens: list[dict]) -> pd.DataFrame:
    total = len(tokens)
    trait_types = set()
    for t in tokens:
        trait_types.update(t["traits"].keys())

    # frequency of each value per trait type (absence counts as NONE)
    counts: dict[str, Counter] = defaultdict(Counter)
    for t in tokens:
        for tt in trait_types:
            counts[tt][t["traits"].get(tt, NONE)] += 1

    rows = []
    for t in tokens:
        score = 0.0
        for tt in trait_types:
            val = t["traits"].get(tt, NONE)
            p = counts[tt][val] / total
            score += 1.0 / p
        rows.append({
            "token_id": t["token_id"],
            "rarity_score": round(score, 2),
            "type": t["traits"].get("Type"),  # Azuki's headline trait
        })
    df = pd.DataFrame(rows).sort_values("rarity_score", ascending=False).reset_index(drop=True)
    df["rarity_rank"] = df.index + 1  # 1 = rarest
    return df.sort_values("token_id").reset_index(drop=True)


def run(contract: str, label: str) -> pd.DataFrame:
    config.ensure_data_dirs()
    print(f"Pulling traits for {contract} ...")
    tokens = fetch_all_tokens(contract)
    df = compute_rarity(tokens)
    out = config.DATA_RAW / f"rarity_{label}.csv"
    df.to_csv(out, index=False)
    print(f"scored {len(df)} tokens -> {out}")
    print("\nRarest 5:")
    print(df.nsmallest(5, "rarity_rank")[["token_id", "rarity_score", "rarity_rank", "type"]].to_string(index=False))
    return df


def main() -> None:
    p = argparse.ArgumentParser(description="Compute per-token rarity from Alchemy metadata")
    p.add_argument("--contract", required=True)
    p.add_argument("--label", required=True)
    args = p.parse_args()
    run(args.contract, args.label)


if __name__ == "__main__":
    main()
