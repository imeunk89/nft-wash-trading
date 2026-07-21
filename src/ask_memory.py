"""Natural-language questions against the agent's own memory.

An analyst asks in plain English; the agent discovers the schema and runs a
read-only query through CockroachDB's **managed MCP server** — never its own SQL
connection — and returns the answer alongside the exact SQL it ran.

Two CockroachDB surfaces are in play across this project:
  * Distributed Vector Indexing  — similarity search over the playbook (see playbook.py)
  * Cloud Managed MCP Server     — this module's read-only cluster access

Why route through MCP rather than psycopg: the analyst-facing path holds no write
credentials, every call is a discrete auditable tool invocation, and the tool
allowlist in crdb_mcp.py makes "the agent modified the evidence" unrepresentable.

    python -m src.ask_memory "which wallets show up in more than one confirmed ring?"
"""
from __future__ import annotations

import json
import os
import re

from . import bedrock, config  # noqa: F401
from .crdb_mcp import MCPClient, MCPError

MAX_ROWS = 50

# A small model writes this SQL, so the prompt carries a data dictionary. Without it
# the model invented filters (e.g. `AND n_rings > 1` on a plain count) and returned a
# confidently wrong answer. The generated SQL is always shown to the analyst.
DICTIONARY = """What the tables mean:

- collusion_cases — one row per DETECTED RING (not per trade). To count rings found on a
  given day, count rows: `WHERE run_label = 'daily-YYYY-MM-DD'` (the baseline 2022 set uses
  run_label 'baseline-2022-01'). `detected_at::date` works too. Columns: n_wallets (wallets
  in the ring), n_trades (self-trades inside it), n_rings (closed loops found within that one
  case), total_eth, active_days, has_high_confidence (a same-NFT loop was proven),
  nearest_confirmed / nearest_distance (closest remembered case, lower = more alike).
- flagged_patterns — the AGENT'S MEMORY. One row per remembered pattern.
  outcome = 'confirmed' (a real ring an analyst verified) or 'rejected' (a known
  false positive). source_case links back to collusion_cases.case_id.
- nft_trades — raw trades. block_number is the chronological order (there is no timestamp).
- wallet_pair_features / wallets — per-pair and per-wallet engineered features."""

SYSTEM = """You translate a market-surveillance analyst's question into ONE read-only \
CockroachDB SELECT statement.

Rules:
- Output ONLY the SQL. No prose, no markdown fences, no commentary.
- SELECT statements only. Never INSERT/UPDATE/DELETE/DROP/ALTER/CREATE.
- If the question names how many results it wants ("top 5", "which 3"), use exactly
  that LIMIT. Otherwise add LIMIT {max_rows}. Never exceed {max_rows}.
- Use only the tables and columns in the schema below. Do not invent columns.
- Add NO filter the question did not ask for. If the analyst asks "how many X",
  count every row matching X and nothing else.
- The `embedding` column is a 1024-dim VECTOR; never SELECT it directly (it is huge).

{dictionary}

Schema:
{schema}

Question: {question}
SQL:"""

ANSWER = """You are a market-surveillance analyst. Answer the question in at most three \
sentences, in plain English, using ONLY the query result. Quote the concrete numbers. \
If the result is empty, say so plainly — do not speculate.

Question: {question}
SQL that ran: {sql}
Result rows (JSON): {rows}

Answer:"""

# Tables worth exposing — evidence and memory, not scratch/staging.
TABLES = ["flagged_patterns", "collusion_cases", "wallet_pair_features", "nft_trades", "wallets"]

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|upsert)\b", re.I
)


def database() -> str:
    m = re.search(r"/([^/?]+)\?", os.environ.get("COCKROACH_DATABASE_URL", ""))
    return m.group(1) if m else "defaultdb"


def _schema(client: MCPClient, db: str) -> str:
    """Pull CREATE TABLE text for the relevant tables, through MCP."""
    out = []
    for t in TABLES:
        try:
            raw = json.loads(client.call("get_table_schema", {"database": db, "table": t}))
            for row in raw.get("rows", []):
                stmt = row.get("create_statement", "")
                # the vector index line adds noise for the model; keep columns only
                out.append(re.sub(r"\n\tVECTOR INDEX[^\n]*", "", stmt))
        except MCPError:
            continue
    return "\n\n".join(out)


def _clean_sql(text: str) -> str:
    sql = re.sub(r"^```(?:sql)?|```$", "", text.strip(), flags=re.M).strip()
    sql = sql.split(";")[0].strip()
    if not sql.lower().startswith(("select", "with")):
        raise ValueError(f"model did not return a SELECT: {sql[:120]}")
    if _FORBIDDEN.search(sql):
        raise ValueError(f"refusing non-read-only SQL: {sql[:120]}")
    if not re.search(r"\blimit\b", sql, re.I):
        sql += f" LIMIT {MAX_ROWS}"
    return sql


def ask(question: str) -> dict:
    """Answer a question about the memory. Returns answer + the SQL actually run."""
    db = database()
    with MCPClient() as m:
        schema = _schema(m, db)
        if not schema:
            raise MCPError("could not read any table schema through the MCP server")

        sql_raw, model = bedrock.explain_verbose(
            SYSTEM.format(schema=schema, question=question, max_rows=MAX_ROWS,
                          dictionary=DICTIONARY),
            max_tokens=400,
        )
        sql = _clean_sql(sql_raw)

        # crdb_mcp's allowlist is the hard gate; select_query is read-only server-side too
        result = m.call("select_query", {"database": db, "query": sql})
        rows = json.loads(result).get("rows", [])

        answer, _ = bedrock.explain_verbose(
            ANSWER.format(question=question, sql=sql, rows=json.dumps(rows)[:4000]),
            max_tokens=400,
        )

    return {
        "question": question,
        "sql": sql,
        "rows": rows,
        "row_count": len(rows),
        "answer": answer.strip(),
        "model": model,
        "via": "CockroachDB Cloud Managed MCP Server (read-only)",
    }


if __name__ == "__main__":
    import sys

    q = " ".join(sys.argv[1:]) or "how many confirmed patterns are in memory?"
    r = ask(q)
    print(f"Q: {r['question']}\n")
    print(f"SQL ({r['via']}):\n  {r['sql']}\n")
    print(f"{r['row_count']} row(s)\n")
    print(f"A: {r['answer']}")
