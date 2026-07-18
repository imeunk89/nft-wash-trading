"""Suspicion score — a transparent 0-100 indicator of how much current activity
resembles known manipulation. It is a RISK signal, not a price prediction and not
trading advice: it says "this volume looks manufactured," never "buy" or "sell".

The score is a weighted blend of:
  * memory resemblance — cosine similarity to the nearest CONFIRMED case (dominant)
  * same-NFT loop      — the near-irrefutable structural red flag
  * self-trade volume  — how much back-and-forth among the same small group

A truly *calibrated probability* needs ground-truth labels; our feedback loop
accumulates those (analyst confirm/reject) over time, so this heuristic can be
recalibrated into a real probability later.
"""
from __future__ import annotations

# distance beyond this to the nearest confirmed case contributes ~no resemblance
MAX_DISTANCE = 0.60
W_MEMORY, W_LOOP, W_VOLUME = 0.60, 0.25, 0.15


def suspicion_score(memory_distance: float | None, has_high_confidence: bool,
                    n_trades: int) -> dict:
    dist = MAX_DISTANCE if memory_distance is None else min(memory_distance, MAX_DISTANCE)
    mem = 1.0 - dist / MAX_DISTANCE           # 0 dist -> 1.0 ; >=MAX -> 0
    loop = 1.0 if has_high_confidence else 0.0
    vol = min(1.0, n_trades / 50.0)

    score = round(100 * (W_MEMORY * mem + W_LOOP * loop + W_VOLUME * vol))
    factors = []
    if mem > 0.5:
        factors.append(f"{round(mem * 100)}% match to a confirmed case")
    if loop:
        factors.append("same-NFT loop")
    if vol > 0.4:
        factors.append(f"{n_trades} self-trades")

    band = "high" if score >= 70 else "elevated" if score >= 40 else "low"
    return {"score": score, "band": band, "factors": factors}
