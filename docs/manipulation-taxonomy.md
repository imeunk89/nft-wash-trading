# NFT Market-Manipulation Taxonomy & Detection Scope

This document explains **why this project detects what it detects**. It maps the full
taxonomy of NFT market-manipulation types against what is detectable from **public
on-chain data alone** (the constraint an outside analyst operates under) versus what
requires the privileged data a real market-surveillance operation holds internally.

It also states the MVP scope and the reasoning behind it. It doubles as the demo's
"why this scope" narrative.

---

## 1. The load-bearing insight: order data vs. on-chain data

Traditional market surveillance runs on **order-level data** — the order book: every
submission, modification, and cancellation, with resting depth and precise timestamps.
Public blockchains expose something structurally different:

| We have (on-chain, public) | We do **not** have (off-chain) |
|---|---|
| Executed trades (buyer, seller, price, time, tx) | Marketplace order book (listings, bids, cancels) |
| Ownership transfers (ERC-721 `Transfer`) | Unfilled / modified / cancelled orders |
| Wallet funding history (who funded whom, when) | Order timestamps, resting depth |
| Mint events (primary-market issuance) | Pre-execution order intent (mempool aside) |

**Consequence:** NFT marketplaces (LooksRare, OpenSea, Blur) keep their order books
**off-chain** and settle only *executed* trades on-chain. So manipulation that lives in
**order-book deception** (spoofing, layering, quote stuffing) is essentially invisible
to on-chain analysis. Conversely, manipulation that leaves a **trade / ownership /
funding** footprint is *more* transparent on-chain than in traditional markets, because
every account is a public wallet with a fully traceable history.

That line — footprint in executed trades vs. footprint only in the order book — is what
divides Tier 1 from Tier 2 below.

---

## 2. Full taxonomy × detectability (two tiers)

- **Tier 1 — Public on-chain MVP (this project):** detectable by an outside analyst with
  only public data (Alchemy `getNFTSales` + Etherscan wallet history + mint events).
- **Tier 2 — Full surveillance deployment:** the *same* memory-playbook engine deployed
  **inside** an exchange or regulator's surveillance function, where internal order-book
  data, account identity (KYC), and cross-venue feeds are available. At that tier the
  **entire** taxonomy becomes addressable — including the order-book-deception types that
  are structurally invisible from outside.

| Manipulation type | Securities-law analog | On-chain footprint | Tier 1 (public on-chain) | Tier 2 (internal surveillance data) |
|---|---|---|---|---|
| **Wash trading — round-trip pair** (IRS "1-1", ≤7-day repurchase) | 가장·통정매매 | Symmetric back-and-forth settlement | ✅ **built** (symmetry heuristic) | ✅ + order-timing precision |
| **Wash trading — matched-order ring** (3+ wallets, cyclic) | 통정매매 | Settlement cycle A→B→C→A | ✅ **MVP scope** (graph cycle detection) | ✅ + order-timing precision |
| **Wash trading — identity** (buyer = seller) | 가장매매 | Same wallet both sides | ✅ (already excluded/flagged) | ✅ |
| **Insider trading** (free-mint / allowlist wallets buying ahead of reveals & news) | 미공개중요정보 이용 | Primary-market free receipt → pre-move purchases | ✅ **MVP scope** (needs mint data) | ✅ + account identity, comms records |
| **Pump-and-dump** (accumulate → ramp → distribute) | 현실거래 시세조종 | Coordinated accumulation, price/volume spike, coordinated exit | △ **stretch** (price + coordinated wallets) | ✅ |
| **Spoofing / layering** (fake orders to move price) | 허수주문 시세조종 | *(none — off-chain order book)* | ❌ not possible | ✅ full detection from order data |
| **Marking the close** (moving a reference/floor price at a snapshot) | 종가관여 | Trades just before a valuation snapshot | △ partial (if reference time defined) | ✅ |
| **Rug pull** (creator drains proceeds / abandons) | 부정거래 | Creator-wallet withdrawals, project halt | △ (needs contract + creator-flow data) | ✅ + KYC on creator |
| **Sleepminting / forged provenance** | 부정거래 | Anomalous mint / `Transfer` provenance | △ (contract-level) | ✅ |
| **Front-running / MEV / sandwich** | (no clean analog) | Mempool + intra-block tx ordering | ❌ / △ (needs mempool) | ✅ with sequencer/mempool access |

---

## 3. MVP scope (what we build now) and why

1. **Wash-trade ring detection** — matched orders across 3+ wallets, found via **graph
   cycle detection**. This closes the real blind spot in the current pairwise detector:
   a ring A→B→C→A looks *asymmetric* on any single pair and slips through symmetry
   filtering, yet 3+-wallet rings are a more common and more deliberate disguise for
   collusive trading than simple 2-party round-trips. **Same data, no new source.**
2. **Insider trading** — wallets that received **free items from the creator in the
   primary market**, whose subsequent *purchases* predict price increases. Academically
   validated on 557 Ethereum collections (Oh 2024): insiders are 4.9% of primary-market
   wallets and their buying strongly predicts future returns. This is a natural extension
   of our existing wallet funding-tracer; it needs mint / primary-market data (obtainable
   via Alchemy `getAssetTransfers`, `from = 0x0` = mint).

*(Stretch)* **Pump-and-dump** — accumulate → ramp → distribute — combines with the two
above to reconstruct the documented collusion pattern.

**Why exactly this scope:** it reuses our existing data and tooling, it is academically
credible, and together the two detectors reconstruct the documented
**wash-trader × insider collusion → pump-and-dump** pattern (insiders sell during/shortly
after wash trading, creating a pump-and-dump that transfers losses to buyers — *Journal
of Banking & Finance*, 2025). That cross-type story is the ideal showcase for a memory
engine that gets smarter as it links cases.

---

## 4. How this maps to the agentic-memory core

Each manipulation type is a **playbook category** in CockroachDB. Confirming a case of one
type triggers retrospective precursor mining whose signals feed *all* types. The collusion
finding is a cross-type memory story: a confirmed wash-trade case surfaces the insider
precursor, and a confirmed insider case surfaces the wash-trade scaffolding around it. The
playbook visibly gets smarter as these links accumulate — the demo's climax.

---

## 5. Product vision: public-data MVP → full surveillance suite

The on-chain MVP deliberately covers only the publicly-detectable subset of the taxonomy.
But the **methodology** — retrospective signal mining into a growing memory playbook — is
**data-source agnostic**. Deployed inside a market-surveillance operation that already
holds complete order-book, account-identity, and cross-venue data, the same engine
addresses the **entire** taxonomy above, including the order-book-deception types
(spoofing, layering) that are structurally invisible from outside.

**The MVP is a public-data proof of a method that generalizes to a full internal
surveillance product.** Tier 1 is what we can show today with public data; Tier 2 is the
commercial product a surveillance desk would run on its own feeds.

---

## 6. Explicitly out of scope for the MVP (stated plainly)

- **Spoofing / layering / quote stuffing** — no public order-book data exists to detect them.
- **Front-running / MEV / sandwich** — requires mempool capture and intra-block sequencing.

Naming these limits is itself a credibility signal: it shows the detector's claims are
bounded by data availability, not overstated.

---

## Sources

- Sebeom Oh, *Market Manipulation in Non-Fungible Token Markets* (2024) — 557 Ethereum
  collections; IRS 3-type wash-trade definition (identity / 1-1 / matched orders); insiders
  = free-item recipients, 4.9% of primary-market wallets, buying predicts returns.
- *Wash trading and insider sales in NFT markets*, Journal of Banking & Finance (2025) —
  wash-trader × insider collusion; insiders sell during/after wash trading → pump-and-dump.
- TRM Labs, *Common Market Manipulation Typologies in Crypto*.
- Chainalysis, *Crypto Market Manipulation 2025: Wash Trading, Pump and Dump*.
