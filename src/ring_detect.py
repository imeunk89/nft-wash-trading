"""Detect wash-trade RINGS (matched orders) — the blind spot of pairwise detection.

A ring is a closed loop of trades across 3+ wallets (A->B->C->A). Each individual
pair in a ring can look asymmetric, so the symmetry heuristic in pair_heuristic.py
misses them. This module finds them with graph cycle detection, and grades them the
way a real surveillance desk would (option C):

  * high_confidence — the SAME NFT (contract, token_id) toured 3+ wallets and returned
                      to a prior holder. Near-irrefutable: the identical asset came
                      back to where it started.
  * candidate       — a set of 3+ wallets that repeatedly trade among themselves in a
                      closed loop (any NFTs). Strong suspicion, needs corroboration.

Matched-order rings are, per the IRS definition Oh (2024) cites, trades among 3+
wallets within ~7 days; we flag whether each ring falls inside a 7-day-equivalent
block window.

Reads every data/raw/looksrare_sales_*.csv; writes data/processed/rings.csv.
"""
from __future__ import annotations

import argparse
import itertools
from glob import glob

import networkx as nx
import pandas as pd

from . import config

# ~7 days of Ethereum blocks at ~12s/block. Used to flag the IRS 7-day matched-order
# window without needing exact per-trade timestamps (we only stored block numbers).
BLOCKS_PER_7D = 7 * 24 * 3600 // 12  # 50,400

MIN_RING_SIZE = 3          # 2-wallet round-trips are pair_heuristic's job, not rings
MAX_RING_LEN = 5           # bound cycle search; rings beyond this are rare + costly
MIN_EDGE_WEIGHT = 2        # candidate edges = a wallet sold to another >=2 times
MAX_CANDIDATE_CYCLES = 20000  # backstop; log if we hit it (no silent truncation)


def load_sales() -> pd.DataFrame:
    files = sorted(glob(str(config.DATA_RAW / "looksrare_sales_*.csv")))
    if not files:
        raise FileNotFoundError(
            "No data/raw/looksrare_sales_*.csv — run `python -m src.alchemy_pull` first."
        )
    df = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
    df["buyer"] = df["buyer"].str.lower()
    df["seller"] = df["seller"].str.lower()
    df["token_id"] = df["token_id"].astype("string")
    df = df[df["buyer"] != df["seller"]]  # drop identity trades
    print(f"loaded {len(df)} trades from {len(files)} file(s)")
    return df


def same_token_rings(df: pd.DataFrame) -> dict[frozenset, list[str]]:
    """High-confidence rings: the identical NFT looped through 3+ wallets.

    Returns {frozenset(wallets): [ "contract:token_id", ... ]}.
    """
    rings: dict[frozenset, list[str]] = {}
    for (contract, token), g in df.groupby(["nft_contract", "token_id"], sort=False):
        if len(g) < MIN_RING_SIZE:
            continue
        g = g.sort_values("block")
        G = nx.DiGraph()
        G.add_edges_from(zip(g["seller"], g["buyer"]))  # seller -> buyer (asset flow)
        for cyc in nx.simple_cycles(G, length_bound=MAX_RING_LEN):
            if len(cyc) >= MIN_RING_SIZE:
                rings.setdefault(frozenset(cyc), []).append(f"{contract}:{token}")
    return rings


def candidate_ring_sets(df: pd.DataFrame) -> tuple[list[frozenset], bool]:
    """Candidate rings: 3+ wallets that repeatedly trade in a closed loop (any NFT)."""
    edges = (
        df.groupby(["seller", "buyer"]).size().reset_index(name="n")
    )
    edges = edges[edges["n"] >= MIN_EDGE_WEIGHT]
    G = nx.DiGraph()
    for s, b, n in zip(edges["seller"], edges["buyer"], edges["n"]):
        G.add_edge(s, b, weight=int(n))

    sets: list[frozenset] = []
    capped = False
    for i, cyc in enumerate(nx.simple_cycles(G, length_bound=MAX_RING_LEN)):
        if len(cyc) >= MIN_RING_SIZE:
            sets.append(frozenset(cyc))
        if i + 1 >= MAX_CANDIDATE_CYCLES:
            capped = True
            break
    return sets, capped


def ring_stats(df: pd.DataFrame, wallets: frozenset) -> dict:
    inside = df[df["seller"].isin(wallets) & df["buyer"].isin(wallets)]
    span = int(inside["block"].max() - inside["block"].min()) if len(inside) else 0
    # NOTE: span covers ALL trades among the ring's wallets across the whole dataset,
    # i.e. how long this closed-loop cluster stayed active — NOT a single 7-day matched
    # order. A cluster that trades among itself for weeks is *more* suspicious, so we
    # report the duration honestly and let the analyst judge rather than force a boolean.
    return {
        "n_trades": len(inside),
        "total_eth": round(float(inside["eth"].sum()), 4),
        "active_days": round(span / (BLOCKS_PER_7D / 7), 1),
    }


def consolidate_cases(
    df: pd.DataFrame, ring_sets: set[frozenset], high_sets: set[frozenset]
) -> pd.DataFrame:
    """Merge overlapping rings into distinct collusion CASES (connected components).

    66 rings that share wallets are really a handful of collusion cells. A surveillance
    desk reports cells, not raw rings — so we union rings that touch into one case.
    """
    G = nx.Graph()
    for ws in ring_sets:
        G.add_nodes_from(ws)
        G.add_edges_from(itertools.combinations(sorted(ws), 2))

    rows = []
    for comp in nx.connected_components(G):
        comp = frozenset(comp)
        inside = df[df["seller"].isin(comp) & df["buyer"].isin(comp)]
        rows.append({
            "n_wallets": len(comp),
            "n_rings": sum(1 for ws in ring_sets if ws <= comp),
            "has_high_confidence": any(ws <= comp for ws in high_sets),
            **ring_stats(df, comp),
            "n_collections": int(inside["nft_contract"].nunique()),
            "wallets": ";".join(sorted(comp)),
        })
    cases = pd.DataFrame(rows).sort_values("n_trades", ascending=False).reset_index(drop=True)
    cases.insert(0, "case_id", [f"C{i:03d}" for i in range(1, len(cases) + 1)])
    return cases


def run() -> pd.DataFrame:
    config.ensure_data_dirs()
    df = load_sales()

    high = same_token_rings(df)
    cand, capped = candidate_ring_sets(df)
    if capped:
        print(f"  ! candidate cycle search hit cap ({MAX_CANDIDATE_CYCLES}); results partial")

    all_sets = set(high) | set(cand)
    rows = []
    for ws in all_sets:
        stats = ring_stats(df, ws)
        is_high = ws in high
        rows.append({
            "tier": "high_confidence" if is_high else "candidate",
            "ring_size": len(ws),
            "same_token": is_high,
            **stats,
            "sample_tokens": ";".join(list(dict.fromkeys(high[ws]))[:3]) if is_high else "",
            "wallets": ";".join(sorted(ws)),
        })

    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values(
            ["tier", "n_trades"], ascending=[True, False]  # 'candidate' < 'high_confidence' alphabetically, so flip below
        )
        # Put high_confidence first regardless of alphabetical order:
        out["_t"] = (out["tier"] != "high_confidence").astype(int)
        out = out.sort_values(["_t", "n_trades"], ascending=[True, False]).drop(columns="_t")

    path = config.DATA_PROCESSED / "rings.csv"
    out.to_csv(path, index=False)

    n_high = int((out["tier"] == "high_confidence").sum()) if len(out) else 0
    n_cand = int((out["tier"] == "candidate").sum()) if len(out) else 0
    print(f"\nRings found: {n_high} high-confidence (same-NFT loop), {n_cand} candidate.")
    print(f"saved -> {path}")
    # Consolidate overlapping rings into distinct collusion cases.
    cases = consolidate_cases(df, all_sets, set(high))
    cases_path = config.DATA_PROCESSED / "cases.csv"
    cases.to_csv(cases_path, index=False)
    print(f"\nConsolidated {len(all_sets)} rings into {len(cases)} distinct collusion cases.")
    print(f"saved -> {cases_path}")
    if len(cases):
        show = cases.head(10).copy()
        show["wallets"] = show["wallets"].str.slice(0, 22) + "..."
        cols = ["case_id", "n_wallets", "n_rings", "has_high_confidence",
                "n_trades", "total_eth", "active_days", "n_collections", "wallets"]
        print("\nTop collusion cases:")
        print(show[cols].to_string(index=False))
    return out


def main() -> None:
    argparse.ArgumentParser(description="Detect wash-trade rings (matched orders)").parse_args()
    run()


if __name__ == "__main__":
    main()
