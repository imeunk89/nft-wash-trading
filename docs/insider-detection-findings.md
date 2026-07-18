# Insider / Rarity-Sniping Detection — Findings & Honest Limits

This document records what we learned attempting to detect **insider trading and
rarity sniping** from public on-chain data, and why the MVP concentrates on
wash-trade detection. The negative result here is a deliberate, credibility-
building part of the project: it empirically validates the two-tier product
thesis in [manipulation-taxonomy.md](manipulation-taxonomy.md).

## What we set out to detect

Trading on non-public **rarity** information around a collection's metadata
**reveal**: before reveal, which token is rare is unknown; someone who knew the
rarity mapping early could accumulate soon-to-be-rare tokens cheaply.

## What we proved works

**Reveal timing is recoverable, and materiality is real.** On Azuki
(`0xED5AF388…`), we located the reveal transaction (block 14044496, 2022-01-21)
by scanning the contract owner's `setBaseURI`/reveal calls and verifying via
`eth_call tokenURI` at each epoch (the collection called `setBaseURI` **three**
times — placeholder, real reveal, and a post-reveal re-pin — so verification, not
a naive "find setBaseURI", was essential).

Measured price dispersion by rarity, before vs. after that block:

| | Spearman(rarity, price) | rarest-10% vs common | Spirit type |
|---|---|---|---|
| **Pre-reveal** | +0.017 (≈0) | ×0.99 (no premium) | 2.30 Ξ |
| **Post-reveal** | +0.282 | ×2.14 | **50.07 Ξ** |

So the reveal is a genuinely **material** event, and pre-reveal rarity was
genuinely **non-public** (price ignored it). The preconditions for insider
trading are detectable from public data.

## What does NOT work from public data

Naming the **actual insider/sniper actors**. We tried three methods:

1. **Statistical rarity-skew** of pre-reveal buyers (Azuki) — null. Azuki used a
   sequential mint + atomic random reveal, so pre-reveal rarity targeting was
   structurally impossible. The detector correctly returns *no signal* — a
   specificity check (no false positives).
2. **Extreme-tail / rare-type concentration** on Meebits (`0x7Bd29408…`, a
   *documented* rarity-sniping exploit collection) — null after multiple-comparison
   correction. Wallets holding the most ultra-rare types are just high-volume
   minters (e.g. 5 rares out of 281 mints → expected 2.1, p=0.065).
3. **Tx-forensics** on reverted mint attempts — no clean actor. The documented
   Meebits exploit reverted **internal** contract calls, which don't appear in
   normal transaction lists; catching it needs transaction *tracing*
   (`debug_traceTransaction`), i.e. archive+trace tooling (paid tier).

## Why this is the expected result — and the core lesson

Wash trading leaves a **high-volume statistical footprint** (hundreds of
round-trips), so it is cleanly detectable from public data. Insider trading and
sniping are **low-volume and surgical** (often a single well-timed acquisition),
so they are statistically camouflaged and require either transaction-level
tracing or off-chain data.

This is exactly the **two-tier split**:

- **Tier 1 (this public-data MVP):** robustly detects wash trading; detects the
  *preconditions* for insider trading (materiality + non-publicness); cannot
  name individual insiders.
- **Tier 2 (internal surveillance deployment):** order-book, account-identity,
  and trace-level data make the insider actors identifiable.

The negative result validates the original scoping instinct — wash trading was
chosen as the core precisely because it is the manipulation type that public
on-chain data can detect with confidence.
