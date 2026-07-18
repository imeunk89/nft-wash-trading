-- CockroachDB schema for the NFT wash-trading memory agent.
-- Apply with:  cockroach sql --url "$COCKROACH_DATABASE_URL" -f sql/schema.sql
-- (or via the Managed MCP Server once connected).
--
-- Requires CockroachDB v25.2+ for the distributed (C-SPANN) VECTOR INDEX used by
-- the playbook table. The VECTOR type itself is v24.2+.

-- Raw trades (from Alchemy getNFTSales). ETH-denominated; USD is unreliable on
-- LooksRare 2022 (LOOKS reward inflation), so we do not store it as ground truth.
CREATE TABLE IF NOT EXISTS nft_trades (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    marketplace   STRING NOT NULL,
    nft_contract  STRING NOT NULL,
    token_id      STRING NOT NULL,
    buyer         STRING NOT NULL,
    seller        STRING NOT NULL,
    price_eth     DECIMAL,
    block_number  INT8 NOT NULL,
    tx_hash       STRING,
    INDEX (nft_contract, token_id),
    INDEX (buyer),
    INDEX (seller)
);

-- Per-wallet funding features (from the Etherscan tracer).
CREATE TABLE IF NOT EXISTS wallets (
    address              STRING PRIMARY KEY,
    first_tx_ts          INT8,
    first_funded_by      STRING,
    first_funded_ts      INT8,
    first_funded_eth     DECIMAL,
    funding_source_count INT8
);

-- Directed-pair symmetry features (the primary wash-trade signal).
CREATE TABLE IF NOT EXISTS wallet_pair_features (
    wallet_a       STRING NOT NULL,
    wallet_b       STRING NOT NULL,
    a_buys         INT8 NOT NULL,
    a_sells        INT8 NOT NULL,
    total_trades   INT8 NOT NULL,
    symmetry_ratio FLOAT8 NOT NULL,
    is_candidate   BOOL NOT NULL DEFAULT false,
    PRIMARY KEY (wallet_a, wallet_b)
);

-- Consolidated collusion cases (overlapping matched-order rings merged into cells).
CREATE TABLE IF NOT EXISTS collusion_cases (
    case_id             STRING PRIMARY KEY,
    n_wallets           INT8 NOT NULL,
    n_rings             INT8 NOT NULL,
    has_high_confidence BOOL NOT NULL,
    n_trades            INT8 NOT NULL,
    total_eth           DECIMAL,
    active_days         FLOAT8,
    n_collections       INT8,
    wallets             STRING       -- ';'-joined member addresses
);

-- THE PLAYBOOK — the agent's memory. Each row is a discovered precursor-signal
-- pattern or a confirmed-case summary, embedded for similarity search. New wallet
-- activity is scored against this via the vector index (retrospective signal mining).
CREATE TABLE IF NOT EXISTS flagged_patterns (
    pattern_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category     STRING NOT NULL,          -- e.g. 'wash_trade_ring', 'symmetric_pair'
    description  STRING NOT NULL,          -- human-readable signal text (embedded)
    embedding    VECTOR(1024),             -- Bedrock Titan v2 embedding (1024 dims)
    source_case  STRING,                   -- FK-ish to collusion_cases.case_id
    outcome      STRING,                   -- 'confirmed' | 'rejected' | 'pending'
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Distributed vector index for playbook similarity search (v25.2+).
-- NOTE: confirm exact DDL against the live cluster version at apply time.
CREATE VECTOR INDEX IF NOT EXISTS flagged_patterns_embedding_idx
    ON flagged_patterns (embedding);
