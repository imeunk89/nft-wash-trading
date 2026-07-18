"""Trace each candidate wallet's funding history via the Etherscan API.

For every wallet appearing in data/processed/confirmed_candidates.csv, this pulls
the full normal + internal transaction history and extracts the precursor features
we hypothesize distinguish wash-trading wallets from legitimate ones:

  * first_funded_by / first_funded_ts   — who sent this wallet its first ETH, when
  * first_funded_value_eth              — size of that first funding
  * funding_source_count                — # distinct addresses that ever funded it
                                          (legit wallets: many sources over time;
                                           wash wallets: often a single funder)
  * first_tx_ts                         — the wallet's very first on-chain activity

Output: data/processed/wallet_features.csv

Free Etherscan tier is 5 req/sec / 100k per day; we self-throttle below that.
Uses the Etherscan v2 unified endpoint (chainid=1 for Ethereum mainnet).
"""
from __future__ import annotations

import argparse
import time
from collections import OrderedDict

import pandas as pd
import requests

from . import config

RATE_LIMIT_PER_SEC = 4          # stay safely under the free-tier 5/sec cap
MAX_RETRIES = 4
WEI_PER_ETH = 10**18


class _RateLimiter:
    def __init__(self, per_sec: float):
        self._min_interval = 1.0 / per_sec
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last = time.monotonic()


_limiter = _RateLimiter(RATE_LIMIT_PER_SEC)


def _get(session: requests.Session, params: dict) -> list[dict]:
    """Call Etherscan and return the `result` list, handling rate-limit retries.

    Etherscan returns status="0" both for genuine 'No transactions found' (result
    is an empty list) and for errors/rate limits (result is a string message).
    """
    full = {**params, "chainid": config.ETH_CHAIN_ID, "apikey": config.get_etherscan_key()}
    for attempt in range(MAX_RETRIES):
        _limiter.wait()
        resp = session.get(config.ETHERSCAN_BASE, params=full, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result")

        if data.get("status") == "1":
            return result if isinstance(result, list) else []
        # status == "0"
        if isinstance(result, list):
            return []  # legitimately empty (e.g. "No transactions found")
        msg = str(result or data.get("message", ""))
        if "rate limit" in msg.lower() or "max calls" in msg.lower():
            time.sleep(1.5 * (attempt + 1))  # back off and retry
            continue
        return []  # some other non-fatal "0" (treat as no data)
    return []


def _incoming_value_transfers(txs: list[dict], addr: str) -> list[dict]:
    """Incoming ETH transfers to `addr` with positive value and no error."""
    addr = addr.lower()
    out = []
    for t in txs:
        if t.get("to", "").lower() != addr:
            continue
        if int(t.get("value", "0")) <= 0:
            continue
        if t.get("isError", "0") == "1":
            continue
        out.append(t)
    return out


def trace_wallet(session: requests.Session, addr: str) -> dict:
    normal = _get(session, {"module": "account", "action": "txlist",
                            "address": addr, "startblock": 0, "endblock": 99999999,
                            "sort": "asc"})
    internal = _get(session, {"module": "account", "action": "txlistinternal",
                              "address": addr, "startblock": 0, "endblock": 99999999,
                              "sort": "asc"})

    all_txs = normal + internal
    first_tx_ts = min((int(t["timeStamp"]) for t in all_txs), default=None)

    incoming = _incoming_value_transfers(normal, addr) + _incoming_value_transfers(internal, addr)
    incoming.sort(key=lambda t: int(t["timeStamp"]))

    funders = {t["from"].lower() for t in incoming}
    first = incoming[0] if incoming else None

    return {
        "address": addr,
        "first_tx_ts": first_tx_ts,
        "n_tx_total": len(all_txs),
        "first_funded_by": first["from"].lower() if first else None,
        "first_funded_ts": int(first["timeStamp"]) if first else None,
        "first_funded_value_eth": (int(first["value"]) / WEI_PER_ETH) if first else None,
        "funding_source_count": len(funders),
        "n_incoming_transfers": len(incoming),
    }


def collect_wallets(candidates_only: bool) -> list[str]:
    path = config.DATA_PROCESSED / "confirmed_candidates.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — run `python -m src.pair_heuristic` first.")
    df = pd.read_csv(path)
    if candidates_only and "is_candidate" in df.columns:
        df = df[df["is_candidate"]]
    wallets = OrderedDict()  # preserve order, dedupe
    for _, row in df.iterrows():
        wallets[str(row["wallet_a"]).lower()] = None
        wallets[str(row["wallet_b"]).lower()] = None
    return list(wallets)


def run(candidates_only: bool, limit: int | None) -> pd.DataFrame:
    config.ensure_data_dirs()
    wallets = collect_wallets(candidates_only)
    if limit:
        wallets = wallets[:limit]
    print(f"Tracing {len(wallets)} wallets via Etherscan...")

    session = requests.Session()
    rows = []
    for i, addr in enumerate(wallets, 1):
        rows.append(trace_wallet(session, addr))
        if i % 10 == 0 or i == len(wallets):
            print(f"  {i}/{len(wallets)}")

    df = pd.DataFrame(rows)
    out = config.DATA_PROCESSED / "wallet_features.csv"
    df.to_csv(out, index=False)
    print(f"saved -> {out}")
    return df


def main() -> None:
    p = argparse.ArgumentParser(description="Trace wallet funding history via Etherscan")
    p.add_argument("--all-pairs", action="store_true",
                   help="Trace wallets from ALL pairs, not just flagged candidates")
    p.add_argument("--limit", type=int, default=None, help="Cap number of wallets (for testing)")
    args = p.parse_args()
    run(candidates_only=not args.all_pairs, limit=args.limit)


if __name__ == "__main__":
    main()
