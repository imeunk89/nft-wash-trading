# NFT Wash-Trading Memory Agent

A memory-driven agent that detects NFT wash trading and **learns to catch it earlier over time**. When a wash-trading wallet pair is confirmed, the agent doesn't just log the pattern — it traces back through the wallet's full history to mine the *earliest precursor signals* that preceded the obvious round-tripping, and stores them as a growing **playbook** in CockroachDB. Each confirmed case makes the next detection earlier and sharper.

Built for the **CockroachDB × AWS "Build with Agentic Memory" hackathon**.

> **Data & scope:** Public on-chain data only (Dune Analytics `nft.trades` + Etherscan). Target: LooksRare / Ethereum / 2022, where independent research (Niu et al., 2024) estimated ~94.5% of volume was wash trading. No proprietary or exchange-internal data or methodology is used.

---

## The demo

```bash
uvicorn app.main:app --port 8100     # then open http://localhost:8100
```

### 1. Daily monitor — the analyst's morning worklist

A scheduled run scans each day's new trades, detects fresh rings, and ranks them by how closely
they match **cases already confirmed in memory**. Every one of the latest run's 8 rings resembles
a confirmed case, so the analyst opens a worklist that is already triaged.

![Daily monitor: detections-by-day chart and KPI tiles](docs/img/daily-monitor.png)

### 2. The memory getting sharper — the whole point

An analyst confirms a scheme; the agent writes it to CockroachDB. Days later the **same crooks
return, worded completely differently** — a keyword filter would miss it. The distance meter shows
what memory bought: the same scheme moved from **0.5051 → 0.3741** cosine distance. Caught, and
caught *sooner*.

![How the memory learns: distance meter showing 0.5051 to 0.3741 after learning](docs/img/memory-learns.png)

### 3. Evidence per case — why this is not a guess

Case C001: 5 wallets, 351 trades among *themselves*, and token **#689 came back to the group 114
times**. A genuine sale moves an NFT to a new owner; these loops never do.

![Case evidence: ring graph and the same-NFT recirculation table](docs/img/case-evidence.png)

---

## What's actually running

Against a live CockroachDB Cloud cluster, on real 2022 LooksRare data:

| | |
|---|---|
| Trades ingested (`nft_trades`) | **48,322** |
| Collusion cases detected (`collusion_cases`) | **57** (37 base + 20 from daily runs) |
| Playbook patterns in vector memory (`flagged_patterns`) | **37** |
| Distributed vector index | `flagged_patterns_embedding_idx` — **live** |
| Embeddings | AWS Bedrock Titan Text Embeddings V2, 1024-dim — **live** |
| Flag rationales | AWS Bedrock chat model — **live** |

---

## How it works (pipeline)

1. **Ingest** LooksRare 2022 trades from Dune → CockroachDB.
2. **Confirm** wash-trading pairs via a symmetric round-trip heuristic (pairs trade back-and-forth near-identical counts in each direction) + funding-relationship checks on Etherscan.
3. **Mine precursors**: for each confirmed pair, pull full wallet history and engineer early-signal features (time from first funding → first trade, funding-source diversity, counterparty concentration).
4. **Learn** which precursors best predict eventual confirmation (AWS SageMaker), turn the top signals into playbook entries.
5. **Remember**: embed playbook entries (AWS Bedrock) into CockroachDB's vector index.
6. **Detect** new activity via vector similarity + rule thresholds; the agent flags with a human-readable explanation citing the closest known precedent.
7. **Close the loop**: human approve/reject is written back to CockroachDB — the memory that makes the agent better over time.

## CockroachDB tools used
- **Distributed Vector Indexing** — **live.** `VECTOR(1024)` column + C-SPANN index on
  `flagged_patterns`; every triage is a similarity search against it. See [`sql/schema.sql`](sql/schema.sql).
- **Durable agent memory** — **live.** `confirm()` / `reject()` verdicts are rows, not session
  state, so the improvement survives restarts and is shared by every agent instance.
- **Managed MCP Server** — _(planned)_ read-only agent access to the cluster + audit logging.
- **ccloud CLI** — _(stretch)_ cluster provisioning/backup shown in the demo.

## AWS services used
- **Bedrock — Titan Text Embeddings V2** — **live.** Embeds every playbook entry and every piece of
  incoming activity into the 1024-dim space the vector index searches.
- **Bedrock — chat model** — **live.** Writes the plain-language rationale citing the closest
  precedent. Defaults to Claude Haiku 4.5; see *Bedrock model access* below.
- **Lambda** — _(planned)_ the daily timer that drives `src/daily_run.py` in production.
- **SageMaker** — _(planned)_ trains the precursor-signal model, extracts feature importance.
- **S3** — _(planned)_ raw-response / audit archival.

### Bedrock model access

Claude on Bedrock requires a one-time **"Anthropic use case details"** form per AWS account
(Bedrock console → *Model access*). Until it's approved the account gets
`ResourceNotFoundException`, so `src/bedrock.py` automatically falls back to another Bedrock model
and the UI labels the rationale with **whichever model actually produced it** — the demo never
shows an empty box. Override either model with `BEDROCK_CHAT_MODEL` /
`BEDROCK_FALLBACK_CHAT_MODEL`.

---

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env       # then fill in your real DUNE_API_KEY and ETHERSCAN_API_KEY
```

Then set up the cluster and load data:

```bash
python -m src.crdb_init          # create schema + the distributed vector index
python -m src.crdb_load          # load trades into CockroachDB
python -m src.ring_detect        # find closed-loop trading rings -> collusion_cases
python -m src.build_playbook     # embed confirmed cases via Bedrock -> vector memory
python -m src.daily_run --date 2023-02-16   # one "today's catch" run
```

## Run (Milestone 1 — local signal pipeline)

```bash
# 1. Pull the top candidate pairs from Dune (LooksRare 2022).
#    If you created the query in the Dune UI from sql/top_pairs.sql:
python -m src.dune_pull --query-id <your_query_id>
#    Or attempt programmatic creation from the .sql file:
python -m src.dune_pull --sql sql/top_pairs.sql --name "top pairs"

# 2. Score pairs by round-trip symmetry and flag wash-trade candidates.
python -m src.pair_heuristic

# 3. Trace each candidate wallet's funding history via Etherscan.
python -m src.etherscan_trace
```

Outputs land in `data/raw/` (raw pulls) and `data/processed/` (scored pairs, wallet features).

## Architecture

_(diagram to be added)_

## License

MIT — see [LICENSE](LICENSE).
