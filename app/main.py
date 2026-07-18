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
