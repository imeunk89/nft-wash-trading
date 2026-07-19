"""Pull NFT sales from the Alchemy NFT API (getNFTSales) — primary data source.

Fully programmatic (no manual UI step): given a calendar month, it resolves the
block range via Etherscan, pages through every LooksRare sale in that range, and
writes two files to data/raw/:

  * looksrare_sales_<month>.csv  — one row per sale (buyer, seller, eth, block, tx)
  * top_pairs.csv                — directed counts collapsed into unordered wallet
                                   pairs with the symmetry columns the heuristic
                                   expects (wallet_a, wallet_b, a_buys, a_sells,
                                   total_trades, total_eth)

Usage:
    python -m src.alchemy_pull --month 2022-01
    python -m src.alchemy_pull --from-block 13916166 --to-block 14100000

Docs: https://www.alchemy.com/docs/reference/getnftsales
"""
from __future__ import annotations

import argparse
import calendar
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from . import config

MARKETPLACE = "looksrare"
PAGE_LIMIT = 1000
REQUEST_PAUSE = 0.2       # gentle throttle; Alchemy free tier is generous
MAX_RETRIES = 4
SAFETY_MAX_PAGES = 500    # backstop against an unbounded loop (~500k sales)


# --- block-range resolution via Etherscan -------------------------------------
def block_for_timestamp(ts: int, closest: str, session: requests.Session) -> int:
    params = {
        "module": "block", "action": "getblocknobytime",
        "timestamp": ts, "closest": closest,
        "chainid": config.ETH_CHAIN_ID, "apikey": config.get_etherscan_key(),
    }
    resp = session.get(config.ETHERSCAN_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "1":
        raise RuntimeError(f"Etherscan block lookup failed: {data}")
    return int(data["result"])


def month_block_range(year: int, month: int, session: requests.Session) -> tuple[int, int]:
    start = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())
    last_day = calendar.monthrange(year, month)[1]
    end = int(datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc).timestamp())
    from_block = block_for_timestamp(start, "after", session)
    to_block = block_for_timestamp(end, "before", session)
    print(f"  {year}-{month:02d} -> blocks {from_block}..{to_block}")
    return from_block, to_block


# --- sales fetch --------------------------------------------------------------
def _fee_units(fee: dict | None) -> float:
    if not fee:
        return 0.0
    try:
        return int(fee.get("amount", "0")) / (10 ** int(fee.get("decimals", 18)))
    except (ValueError, TypeError):
        return 0.0


def fetch_sales(from_block: int, to_block: int, session: requests.Session,
                marketplace: str | None = MARKETPLACE) -> list[dict]:
    """Fetch sales in a block range. marketplace=None returns ALL marketplaces."""
    url = config.alchemy_nft_url("getNFTSales")
    base_params = {
        "fromBlock": from_block, "toBlock": to_block,
        "order": "asc", "limit": PAGE_LIMIT,
    }
    if marketplace:
        base_params["marketplace"] = marketplace
    sales: list[dict] = []
    page_key = None
    for page in range(1, SAFETY_MAX_PAGES + 1):
        params = dict(base_params)
        if page_key:
            params["pageKey"] = page_key

        for attempt in range(MAX_RETRIES):
            resp = session.get(url, params=params, timeout=60)
            if resp.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError("Alchemy getNFTSales kept returning 429 (rate limited).")

        body = resp.json()
        batch = body.get("nftSales") or body.get("sales") or []
        sales.extend(batch)
        page_key = body.get("pageKey")
        if page % 5 == 0 or not page_key:
            print(f"  page {page}: +{len(batch)} (total {len(sales)})")
        if not page_key:
            break
        time.sleep(REQUEST_PAUSE)
    else:
        print(f"  ! stopped at SAFETY_MAX_PAGES={SAFETY_MAX_PAGES}; range may be truncated")
    return sales


def sales_to_dataframe(sales: list[dict]) -> pd.DataFrame:
    rows = []
    for s in sales:
        buyer = (s.get("buyerAddress") or "").lower()
        seller = (s.get("sellerAddress") or "").lower()
        if not buyer or not seller:
            continue
        rows.append({
            "buyer": buyer,
            "seller": seller,
            "eth": _fee_units(s.get("sellerFee")),
            "block": int(s.get("blockNumber", 0)),
            "tx_hash": s.get("transactionHash"),
            "nft_contract": (s.get("contractAddress") or "").lower(),
            "token_id": s.get("tokenId"),
            "marketplace": s.get("marketplace") or "unknown",
        })
    return pd.DataFrame(rows)


# --- aggregation into the pair schema the heuristic consumes -------------------
def aggregate_pairs(sales_df: pd.DataFrame) -> pd.DataFrame:
    df = sales_df[sales_df["buyer"] != sales_df["seller"]].copy()  # drop self-trades
    df["wallet_a"] = df[["buyer", "seller"]].min(axis=1)
    df["wallet_b"] = df[["buyer", "seller"]].max(axis=1)
    df["a_is_buyer"] = df["buyer"] == df["wallet_a"]

    grp = df.groupby(["wallet_a", "wallet_b"], sort=False)
    pairs = grp.agg(
        a_buys=("a_is_buyer", "sum"),
        total_trades=("a_is_buyer", "size"),
        total_eth=("eth", "sum"),
    ).reset_index()
    pairs["a_sells"] = pairs["total_trades"] - pairs["a_buys"]
    pairs = pairs[["wallet_a", "wallet_b", "a_buys", "a_sells", "total_trades", "total_eth"]]
    return pairs.sort_values("total_trades", ascending=False)


def run(from_block: int, to_block: int, label: str) -> pd.DataFrame:
    config.ensure_data_dirs()
    session = requests.Session()

    print(f"Fetching LooksRare sales, blocks {from_block}..{to_block} ...")
    sales = fetch_sales(from_block, to_block, session)
    print(f"  total sales fetched: {len(sales)}")

    sales_df = sales_to_dataframe(sales)
    raw_path = config.DATA_RAW / f"looksrare_sales_{label}.csv"
    sales_df.to_csv(raw_path, index=False)
    print(f"  saved raw sales -> {raw_path}")

    pairs = aggregate_pairs(sales_df)
    pairs_path = config.DATA_RAW / "top_pairs.csv"
    pairs.to_csv(pairs_path, index=False)
    print(f"  saved {len(pairs)} pairs -> {pairs_path}")
    return pairs


def main() -> None:
    p = argparse.ArgumentParser(description="Pull LooksRare sales from Alchemy getNFTSales")
    p.add_argument("--month", help="Calendar month YYYY-MM (e.g. 2022-01)")
    p.add_argument("--from-block", type=int)
    p.add_argument("--to-block", type=int)
    args = p.parse_args()

    session = requests.Session()
    if args.month:
        year, month = (int(x) for x in args.month.split("-"))
        from_block, to_block = month_block_range(year, month, session)
        label = args.month
    elif args.from_block and args.to_block:
        from_block, to_block = args.from_block, args.to_block
        label = f"{from_block}_{to_block}"
    else:
        raise SystemExit("Provide --month YYYY-MM or both --from-block and --to-block.")

    run(from_block, to_block, label)


if __name__ == "__main__":
    main()
