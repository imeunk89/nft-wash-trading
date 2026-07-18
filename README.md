# NFT Wash-Trading Memory Agent

A memory-driven agent that detects NFT wash trading and **learns to catch it earlier over time**. When a wash-trading wallet pair is confirmed, the agent doesn't just log the pattern — it traces back through the wallet's full history to mine the *earliest precursor signals* that preceded the obvious round-tripping, and stores them as a growing **playbook** in CockroachDB. Each confirmed case makes the next detection earlier and sharper.

Built for the **CockroachDB × AWS "Build with Agentic Memory" hackathon**.

> **Data & scope:** Public on-chain data only (Dune Analytics `nft.trades` + Etherscan). Target: LooksRare / Ethereum / 2022, where independent research (Niu et al., 2024) estimated ~94.5% of volume was wash trading. No proprietary or exchange-internal data or methodology is used.

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
- **Distributed Vector Indexing** — _(planned)_ stores + similarity-searches playbook signal embeddings.
- **Managed MCP Server** — _(planned)_ connects the agent to the cluster (read-only + audit logging).
- **ccloud CLI** — _(stretch)_ cluster provisioning/backup shown in the demo.

## AWS services used
- **SageMaker** — _(planned)_ trains the precursor-signal model, extracts feature importance.
- **Bedrock** — _(planned)_ generates playbook embeddings + flag explanations.
- **Lambda / S3** — _(planned)_ scheduled Dune ingestion; raw-response / audit archival.

_(Sections marked planned are filled in as milestones land.)_

---

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env       # then fill in your real DUNE_API_KEY and ETHERSCAN_API_KEY
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
