"""Daily detection run — "today's catch".

Simulates a scheduled surveillance job: pull one day's trades, detect new wash-trade
rings, and rank them by how closely they resemble CONFIRMED cases already in the
playbook memory (AWS Bedrock embedding + CockroachDB vector search). The memory is
what turns a raw pile of new rings into a prioritized worklist.

    python -m src.daily_run --date 2022-02-20

In production this is the body of an AWS Lambda on an EventBridge daily schedule,
pointed at the last 24h of blocks instead of a historical date.
"""
from __future__ import annotations

import argparse
import datetime as dt

import requests

from . import bedrock
from .alchemy_pull import block_for_timestamp, fetch_sales, sales_to_dataframe
from .build_playbook import case_to_signal
from .db import connect
from .playbook import search_similar
from .ring_detect import candidate_ring_sets, consolidate_cases, same_token_rings

HIGH_PRIORITY_DISTANCE = 0.40  # <= this to a confirmed case => escalate


def day_block_range(date_str: str, session: requests.Session) -> tuple[int, int]:
    y, m, d = (int(x) for x in date_str.split("-"))
    start = int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp())
    end = start + 86400
    return (block_for_timestamp(start, "after", session),
            block_for_timestamp(end, "before", session))


def run(date_str: str) -> None:
    session = requests.Session()
    fb, tb = day_block_range(date_str, session)
    print(f"=== TODAY'S CATCH · {date_str} ===")
    print(f"scanning LooksRare blocks {fb}..{tb} …")

    df = sales_to_dataframe(fetch_sales(fb, tb, session))
    print(f"  {len(df)} trades in window")

    high = same_token_rings(df)
    cand, _ = candidate_ring_sets(df)
    cases = consolidate_cases(df, set(high) | set(cand), set(high))
    if not len(cases):
        print("no new suspected rings today.")
        return

    # Score each new ring against the confirmed playbook memory.
    scored = []
    for i, c in enumerate(cases.itertuples(index=False), 1):
        cd = {"n_wallets": c.n_wallets, "n_rings": c.n_rings,
              "has_high_confidence": c.has_high_confidence, "n_trades": c.n_trades,
              "active_days": c.active_days, "n_collections": c.n_collections}
        emb = bedrock.embed(case_to_signal(cd))
        confirmed = [m for m in search_similar(emb, k=5) if m["outcome"] == "confirmed"]
        near = confirmed[0] if confirmed else None
        dist = round(float(near["cosine_distance"]), 4) if near else None
        scored.append({"cid": f"{date_str}-{i:02d}", "c": c, "dist": dist,
                       "near": near["source_case"] if near else None})

    scored.sort(key=lambda r: (r["dist"] is None, r["dist"] if r["dist"] is not None else 9))

    print(f"\ndetected {len(scored)} new suspected wash-trade rings, ranked by memory:\n")
    for r in scored:
        c = r["c"]
        hot = r["dist"] is not None and r["dist"] <= HIGH_PRIORITY_DISTANCE
        tag = "HIGH " if hot else "watch"
        why = (f"resembles confirmed {r['near']} (dist {r['dist']})" if hot
               else f"nearest confirmed dist {r['dist']}" if r["dist"] is not None
               else "no memory match")
        print(f"  [{tag}] {r['cid']} · {c.n_wallets} wallets · {c.n_trades} self-trades · {why}")

    # Persist the catch, tagged with this run.
    label = f"daily-{date_str}"
    detected_at = f"{date_str}T12:00:00Z"
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM collusion_cases WHERE run_label = %s", (label,))  # idempotent re-run
        for r in scored:
            c = r["c"]
            cur.execute(
                "INSERT INTO collusion_cases (case_id, n_wallets, n_rings, has_high_confidence, "
                "n_trades, total_eth, active_days, n_collections, wallets, detected_at, run_label, "
                "nearest_confirmed, nearest_distance) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (r["cid"], int(c.n_wallets), int(c.n_rings), bool(c.has_high_confidence),
                 int(c.n_trades), float(c.total_eth), float(c.active_days),
                 int(c.n_collections), c.wallets, detected_at, label, r["near"], r["dist"]),
            )
        conn.commit()
    n_hi = sum(1 for r in scored if r["dist"] is not None and r["dist"] <= HIGH_PRIORITY_DISTANCE)
    print(f"\nwrote {len(scored)} cases to CockroachDB · {n_hi} HIGH-priority (resemble confirmed cases).")


def main() -> None:
    p = argparse.ArgumentParser(description="Daily wash-trade detection run")
    p.add_argument("--date", required=True, help="YYYY-MM-DD to scan")
    run(p.parse_args().date)


if __name__ == "__main__":
    main()
