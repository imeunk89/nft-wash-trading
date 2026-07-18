"""Demo web app for the NFT wash-trading memory agent.

Wraps the triage / confirm / reject loop in a UI a judge can click through and
watch the memory get smarter live. Reuses src/ modules; talks to the same
CockroachDB playbook + AWS Bedrock the CLI does.

    uvicorn app.main:app --port 8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src import bedrock
from src.db import connect
from src.feedback_loop import confirm, reject, triage

app = FastAPI(title="NFT Wash-Trading Memory Agent")
HERE = Path(__file__).parent

# Analyst-confirmed learnings + rejects carry these markers so /reset can wipe
# them without touching the 37 base collusion cases (source_case 'C0..').
LEARNED_CASE = "LEARNED"


class Activity(BaseModel):
    activity: str


def _match(m) -> dict | None:
    if not m:
        return None
    return {
        "distance": round(float(m["cosine_distance"]), 4),
        "source_case": m.get("source_case"),
        "outcome": m.get("outcome"),
        "description": m["description"],
    }


def _stats() -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT outcome, count(*) FROM flagged_patterns GROUP BY outcome")
        return {k: int(v) for k, v in cur.fetchall()}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (HERE / "index.html").read_text()


@app.post("/api/triage")
def api_triage(a: Activity) -> dict:
    r = triage(a.activity)
    explanation = None
    if r["verdict"] == "flag" and r["nearest_confirmed"]:
        top = r["nearest_confirmed"]
        prompt = (
            "You are a market-surveillance assistant reviewing flagged on-chain NFT "
            f'activity. New activity: "{a.activity}". Closest confirmed precedent '
            f"(cosine {top['cosine_distance']:.2f}, case {top['source_case']}): "
            f"\"{top['description']}\". In 2-3 sentences, explain why the new activity "
            "is suspicious and how it resembles the precedent."
        )
        try:
            explanation = bedrock.explain(prompt)
        except Exception as e:  # keep the UI clean if Claude access isn't ready
            print(f"[explain] Bedrock error: {e}")
            explanation = None
    return {
        "verdict": r["verdict"],
        "nearest_confirmed": _match(r["nearest_confirmed"]),
        "nearest_rejected": _match(r["nearest_rejected"]),
        "explanation": explanation,
        "stats": _stats(),
    }


@app.post("/api/confirm")
def api_confirm(a: Activity) -> dict:
    pid = confirm(a.activity, source_case=LEARNED_CASE)
    return {"ok": True, "pattern_id": pid, "stats": _stats()}


@app.post("/api/reject")
def api_reject(a: Activity) -> dict:
    pid = reject(a.activity)
    return {"ok": True, "pattern_id": pid, "stats": _stats()}


@app.get("/api/stats")
def api_stats() -> dict:
    return _stats()


@app.get("/api/cases")
def api_cases() -> list[dict]:
    """Real detected collusion cases with their headline evidence numbers."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT case_id, n_wallets, n_rings, has_high_confidence, n_trades, "
            "total_eth, active_days, n_collections "
            "FROM collusion_cases ORDER BY n_trades DESC LIMIT 20"
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["total_eth"] = float(r["total_eth"]) if r["total_eth"] is not None else None
        r["active_days"] = float(r["active_days"]) if r["active_days"] is not None else None
        r["n_trades"] = int(r["n_trades"])
    return rows


@app.get("/api/case/{case_id}")
def api_case(case_id: str) -> dict:
    """Full evidence for one case: the ring graph edges, the same-NFT loops that
    recirculated (the decisive high-confidence signal), and the member wallets."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT wallets, n_wallets, n_trades, total_eth, active_days, "
            "has_high_confidence, n_collections FROM collusion_cases WHERE case_id = %s",
            (case_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"error": "case not found"}
        wallets = row[0].split(";")
        summary = {
            "case_id": case_id, "n_wallets": row[1], "n_trades": int(row[2]),
            "total_eth": float(row[3]) if row[3] is not None else None,
            "active_days": float(row[4]) if row[4] is not None else None,
            "has_high_confidence": row[5], "n_collections": row[6],
        }
        # Directed trade edges among the case's wallets (who sold to whom, how often).
        cur.execute(
            "SELECT seller, buyer, count(*) AS n, coalesce(sum(price_eth), 0) AS eth "
            "FROM nft_trades WHERE seller = ANY(%s) AND buyer = ANY(%s) "
            "GROUP BY seller, buyer ORDER BY n DESC",
            (wallets, wallets),
        )
        edges = [{"source": s, "target": b, "count": int(n), "eth": round(float(e), 2)}
                 for s, b, n, e in cur.fetchall()]
        # Same NFT recirculated among these wallets = the smoking gun.
        cur.execute(
            "SELECT nft_contract, token_id, count(*) AS n FROM nft_trades "
            "WHERE seller = ANY(%s) AND buyer = ANY(%s) "
            "GROUP BY nft_contract, token_id HAVING count(*) >= 3 ORDER BY n DESC LIMIT 8",
            (wallets, wallets),
        )
        tokens = [{"contract": c, "token_id": str(t), "times": int(n)}
                  for c, t, n in cur.fetchall()]
    return {"summary": summary, "wallets": wallets, "edges": edges,
            "recirculated_tokens": tokens}


@app.post("/api/reset")
def api_reset() -> dict:
    """Wipe analyst-learned + rejected patterns; keep the 37 base cases."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM flagged_patterns "
            "WHERE source_case = %s OR (outcome = 'rejected' AND source_case IS NULL)",
            (LEARNED_CASE,),
        )
        conn.commit()
    return {"ok": True, "stats": _stats()}
