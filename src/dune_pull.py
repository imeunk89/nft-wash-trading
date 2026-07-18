"""Pull data from Dune Analytics via the REST API (no SDK — just `requests`).

Two ways to run a query:

  1. Execute an existing saved query by ID (works on every plan, cheapest):
         python -m src.dune_pull --query-id 1234567

  2. Create a query from a local .sql file, then execute it (convenient, but the
     Query-CRUD endpoints require a plan that permits API query creation):
         python -m src.dune_pull --sql sql/top_pairs.sql --name "top pairs"

Results are written to data/raw/<out>.csv and <out>.json.

Docs: https://docs.dune.com/api-reference/
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd
import requests

from . import config

POLL_SECONDS = 3
POLL_TIMEOUT_SECONDS = 600  # queries over a full year can take a while


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"X-Dune-API-Key": config.get_dune_key()})
    return s


def create_query_from_sql(sql: str, name: str, session: requests.Session) -> int:
    """Create a private saved query from raw SQL. Returns its query_id.

    Note: requires a Dune plan that allows programmatic query creation. On plans
    that don't, this returns a 403 — fall back to --query-id with a query you
    made in the Dune UI.
    """
    resp = session.post(
        f"{config.DUNE_BASE}/query",
        json={"name": name, "query_sql": sql, "is_private": True},
        timeout=30,
    )
    if resp.status_code in (401, 402, 403):
        raise RuntimeError(
            f"Dune rejected programmatic query creation (HTTP {resp.status_code}) — "
            "creating queries via the API requires a paid Dune plan. On the free "
            "plan instead: paste sql/top_pairs.sql into a New Query in the Dune UI, "
            "Save it, copy the query id from the URL, and rerun with "
            "`--query-id <id>` (executing a saved query works on the free plan)."
        )
    resp.raise_for_status()
    query_id = resp.json()["query_id"]
    print(f"  created Dune query_id={query_id}")
    return query_id


def execute_query(query_id: int, session: requests.Session) -> str:
    resp = session.post(
        f"{config.DUNE_BASE}/query/{query_id}/execute",
        json={"performance": "medium"},
        timeout=30,
    )
    resp.raise_for_status()
    execution_id = resp.json()["execution_id"]
    print(f"  execution_id={execution_id} — polling...")
    return execution_id


def wait_for_results(execution_id: str, session: requests.Session) -> list[dict]:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while True:
        status = session.get(
            f"{config.DUNE_BASE}/execution/{execution_id}/status", timeout=30
        ).json()
        state = status.get("state")
        if state == "QUERY_STATE_COMPLETED":
            break
        if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED", "QUERY_STATE_EXPIRED"):
            raise RuntimeError(f"Dune execution ended in state {state}: {status}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Dune execution {execution_id} timed out (still {state}).")
        time.sleep(POLL_SECONDS)

    results = session.get(
        f"{config.DUNE_BASE}/execution/{execution_id}/results", timeout=60
    ).json()
    rows = results.get("result", {}).get("rows", [])
    print(f"  got {len(rows)} rows")
    return rows


def run(query_id: int | None, sql_path: Path | None, name: str, out: str) -> pd.DataFrame:
    config.ensure_data_dirs()
    session = _session()

    if query_id is None:
        if sql_path is None:
            raise ValueError("Provide either --query-id or --sql.")
        sql = Path(sql_path).read_text()
        query_id = create_query_from_sql(sql, name, session)

    execution_id = execute_query(query_id, session)
    rows = wait_for_results(execution_id, session)

    df = pd.DataFrame(rows)
    csv_path = config.DATA_RAW / f"{out}.csv"
    json_path = config.DATA_RAW / f"{out}.json"
    df.to_csv(csv_path, index=False)
    df.to_json(json_path, orient="records", indent=2)
    print(f"  saved -> {csv_path}")
    return df


def main() -> None:
    p = argparse.ArgumentParser(description="Pull a Dune query into data/raw/")
    p.add_argument("--query-id", type=int, default=None, help="Existing saved query id")
    p.add_argument("--sql", type=Path, default=None, help="Path to a .sql file to create+run")
    p.add_argument("--name", default="coin_trade query", help="Name for a created query")
    p.add_argument("--out", default="top_pairs", help="Output basename in data/raw/")
    args = p.parse_args()
    try:
        run(args.query_id, args.sql, args.name, args.out)
    except RuntimeError as e:
        raise SystemExit(f"\n[dune_pull] {e}")


if __name__ == "__main__":
    main()
