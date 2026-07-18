"""Turn raw directed pair counts (from Dune) into confirmed wash-trade candidates.

Reads data/raw/top_pairs.csv (columns: wallet_a, wallet_b, a_buys, a_sells,
total_trades, total_usd) and computes the symmetry signal:

    symmetry_ratio = min(a_buys, a_sells) / max(a_buys, a_sells)

A pair is a candidate when it trades enough AND close to symmetrically in both
directions — the back-and-forth self-dealing tell. Thresholds are intentionally
exposed as knobs; this is exactly the domain judgment call the human should own.
"""
from __future__ import annotations

import argparse

import pandas as pd

from . import config

# Defaults — tune these. A pair that round-trips >=10 times with >=80% directional
# symmetry is a strong self-dealing candidate on LooksRare 2022.
MIN_TRADES = 10
MIN_SYMMETRY = 0.80


def score(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    lo = df[["a_buys", "a_sells"]].min(axis=1)
    hi = df[["a_buys", "a_sells"]].max(axis=1)
    df["symmetry_ratio"] = (lo / hi).where(hi > 0, 0.0).round(4)
    return df


def flag(df: pd.DataFrame, min_trades: int, min_symmetry: float) -> pd.DataFrame:
    both_directions = (df["a_buys"] > 0) & (df["a_sells"] > 0)
    is_candidate = (
        both_directions
        & (df["total_trades"] >= min_trades)
        & (df["symmetry_ratio"] >= min_symmetry)
    )
    df = df.copy()
    df["is_candidate"] = is_candidate
    return df


def run(min_trades: int, min_symmetry: float) -> pd.DataFrame:
    config.ensure_data_dirs()
    raw = config.DATA_RAW / "top_pairs.csv"
    if not raw.exists():
        raise FileNotFoundError(f"{raw} not found — run `python -m src.dune_pull` first.")

    df = pd.read_csv(raw)
    df = flag(score(df), min_trades, min_symmetry)
    df = df.sort_values(["is_candidate", "total_trades"], ascending=[False, False])

    out = config.DATA_PROCESSED / "confirmed_candidates.csv"
    df.to_csv(out, index=False)

    n_cand = int(df["is_candidate"].sum())
    print(f"{len(df)} pairs scored; {n_cand} flagged as wash-trade candidates.")
    print(f"saved -> {out}")
    if n_cand:
        cols = ["wallet_a", "wallet_b", "a_buys", "a_sells", "total_trades", "symmetry_ratio"]
        print("\nTop candidates:")
        print(df[df["is_candidate"]][cols].head(10).to_string(index=False))
    return df


def main() -> None:
    p = argparse.ArgumentParser(description="Score/flag wash-trade candidate pairs")
    p.add_argument("--min-trades", type=int, default=MIN_TRADES)
    p.add_argument("--min-symmetry", type=float, default=MIN_SYMMETRY)
    args = p.parse_args()
    run(args.min_trades, args.min_symmetry)


if __name__ == "__main__":
    main()
