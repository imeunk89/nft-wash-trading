"""Load the local pipeline outputs into CockroachDB (idempotent — truncates first).

    python -m src.crdb_load

Loads:
  nft_trades            <- data/raw/looksrare_sales_*.csv
  wallet_pair_features  <- data/processed/confirmed_candidates.csv
  collusion_cases       <- data/processed/cases.csv
  wallets               <- data/processed/wallet_features.csv (if present)
"""
from __future__ import annotations

import math
from glob import glob

import pandas as pd

from . import config
from .db import connect


def _n(v):
    """NaN -> None, numpy scalars -> python."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v.item() if hasattr(v, "item") else v


def _copy(cur, table, columns, rows):
    cur.execute(f"TRUNCATE {table}")
    collist = ", ".join(columns)
    with cur.copy(f"COPY {table} ({collist}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)


def load() -> None:
    with connect() as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            # nft_trades (big) via COPY
            files = sorted(glob(str(config.DATA_RAW / "looksrare_sales_*.csv")))
            trades = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
            _copy(cur, "nft_trades",
                  ["marketplace", "nft_contract", "token_id", "buyer", "seller",
                   "price_eth", "block_number", "tx_hash"],
                  (("looksrare", r.nft_contract, str(r.token_id), r.buyer, r.seller,
                    _n(r.eth), int(r.block), _n(r.tx_hash))
                   for r in trades.itertuples(index=False)))
            print(f"nft_trades: {len(trades)}")

            # wallet_pair_features (medium) via COPY
            pairs = pd.read_csv(config.DATA_PROCESSED / "confirmed_candidates.csv")
            _copy(cur, "wallet_pair_features",
                  ["wallet_a", "wallet_b", "a_buys", "a_sells", "total_trades",
                   "symmetry_ratio", "is_candidate"],
                  ((r.wallet_a, r.wallet_b, int(r.a_buys), int(r.a_sells),
                    int(r.total_trades), float(r.symmetry_ratio), bool(r.is_candidate))
                   for r in pairs.itertuples(index=False)))
            print(f"wallet_pair_features: {len(pairs)}")

            # collusion_cases (small)
            cases = pd.read_csv(config.DATA_PROCESSED / "cases.csv")
            cur.execute("TRUNCATE collusion_cases")
            cur.executemany(
                "INSERT INTO collusion_cases (case_id, n_wallets, n_rings, "
                "has_high_confidence, n_trades, total_eth, active_days, "
                "n_collections, wallets) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(r.case_id, int(r.n_wallets), int(r.n_rings), bool(r.has_high_confidence),
                  int(r.n_trades), _n(r.total_eth), _n(r.active_days),
                  int(r.n_collections), r.wallets)
                 for r in cases.itertuples(index=False)])
            print(f"collusion_cases: {len(cases)}")

            # wallets (small, optional)
            wf_path = config.DATA_PROCESSED / "wallet_features.csv"
            if wf_path.exists():
                wf = pd.read_csv(wf_path)
                cur.execute("TRUNCATE wallets")
                cur.executemany(
                    "INSERT INTO wallets (address, first_tx_ts, first_funded_by, "
                    "first_funded_ts, first_funded_eth, funding_source_count) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    [(r.address, _n(r.first_tx_ts), _n(r.first_funded_by),
                      _n(r.first_funded_ts), _n(r.first_funded_value_eth),
                      _n(r.funding_source_count))
                     for r in wf.itertuples(index=False)])
                print(f"wallets: {len(wf)}")

        conn.commit()
    print("load complete.")


if __name__ == "__main__":
    load()
