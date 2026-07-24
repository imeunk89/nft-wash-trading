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

from app import demo
from src import ask_memory, bedrock, crdb_mcp, playbook
from src.build_playbook import case_to_signal
from src.db import connect
from src.feedback_loop import confirm, reject, triage
from src.suspicion import suspicion_score

app = FastAPI(title="NFT Wash-Trading Memory Agent")
HERE = Path(__file__).parent

# Analyst-confirmed learnings + rejects carry these markers so /reset can wipe
# them without touching the 37 base collusion cases (source_case 'C0..').
LEARNED_CASE = "LEARNED"
BASELINE_RUN = "baseline-2022-01"   # the 37 patterns a reset restores to

# Must match G_MAX_NODES in app/index.html — the replay sequence is limited to the
# wallets the ring graph actually draws.
GRAPH_MAX_NODES = 36


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
    if demo.ENABLED:
        return demo.lookup("triage", a.activity) or demo.UNAVAILABLE
    r = triage(a.activity)
    explanation = explain_model = None
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
            explanation, explain_model = bedrock.explain_verbose(prompt)
        except Exception as e:  # keep the UI clean if Bedrock access isn't ready
            print(f"[explain] Bedrock error: {e}")
            explanation = explain_model = None
    return {
        "verdict": r["verdict"],
        "nearest_confirmed": _match(r["nearest_confirmed"]),
        "nearest_rejected": _match(r["nearest_rejected"]),
        "explanation": explanation,
        "explanation_model": explain_model,
        "stats": _stats(),
    }


@app.post("/api/confirm")
def api_confirm(a: Activity) -> dict:
    if demo.ENABLED:
        return demo.lookup("confirm", a.activity) or demo.UNAVAILABLE
    pid = confirm(a.activity, source_case=LEARNED_CASE)
    return {"ok": True, "pattern_id": pid, "stats": _stats()}


@app.post("/api/reject")
def api_reject(a: Activity) -> dict:
    if demo.ENABLED:
        return demo.lookup("reject", a.activity) or demo.UNAVAILABLE
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


@app.get("/api/catch")
def api_catch() -> dict:
    """Daily-run detections ('today's catch'), newest first, ranked within each day
    by how closely they resemble a confirmed case in the playbook memory."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT case_id, n_wallets, n_trades, has_high_confidence, "
            "detected_at, nearest_confirmed, nearest_distance "
            "FROM collusion_cases WHERE run_label LIKE 'daily-%' "
            "ORDER BY detected_at DESC, nearest_distance ASC NULLS LAST"
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["date"] = str(r["detected_at"])[:10]
        del r["detected_at"]
        r["n_trades"] = int(r["n_trades"])
        r["nearest_distance"] = (round(float(r["nearest_distance"]), 4)
                                 if r["nearest_distance"] is not None else None)
        r["suspicion"] = suspicion_score(
            r["nearest_distance"], r["has_high_confidence"], r["n_trades"])
    return {"catches": rows}


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
        # Chronological trade sequence so the UI can replay how the ring formed.
        # nft_trades has no timestamp column, but block_number is a strict
        # chronological ordering on-chain, so it *is* the sequence.
        #
        # Restrict it to the same wallets the UI actually draws (the busiest
        # GRAPH_MAX_NODES, ranked by trade count exactly as app/index.html does).
        # Otherwise most of the sequence references wallets that aren't on screen
        # and the replay counts up while nothing appears.
        deg: dict[str, int] = {}
        for e in edges:
            deg[e["source"]] = deg.get(e["source"], 0) + e["count"]
            deg[e["target"]] = deg.get(e["target"], 0) + e["count"]
        drawn = sorted(wallets, key=lambda w: -deg.get(w, 0))[:GRAPH_MAX_NODES]
        cur.execute(
            "SELECT seller, buyer, token_id, block_number, price_eth FROM nft_trades "
            "WHERE seller = ANY(%s) AND buyer = ANY(%s) AND seller <> buyer "
            "ORDER BY block_number ASC LIMIT 150",
            (drawn, drawn),
        )
        sequence = [{"source": s, "target": b, "token_id": str(t),
                     "block": int(bn), "eth": round(float(p or 0), 3)}
                    for s, b, t, bn, p in cur.fetchall()]
        # How many trades exist among those same drawn wallets — the honest
        # denominator for "showing the first N of M".
        cur.execute(
            "SELECT count(*) FROM nft_trades WHERE seller = ANY(%s) AND buyer = ANY(%s) "
            "AND seller <> buyer",
            (drawn, drawn),
        )
        sequence_total = int(cur.fetchone()[0])
        # Has an analyst already ruled on this ring? The 37 baseline cases are
        # pre-confirmed, so the UI must show that instead of offering the buttons.
        cur.execute(
            "SELECT fp.outcome, cc.run_label = %s AS is_baseline "
            "FROM flagged_patterns fp LEFT JOIN collusion_cases cc "
            "  ON cc.case_id = fp.source_case "
            "WHERE fp.source_case = %s",
            (BASELINE_RUN, case_id),
        )
        ruled = cur.fetchone()
    summary["ruling"] = (
        {"outcome": ruled[0], "baseline": bool(ruled[1])} if ruled else None
    )
    return {"summary": summary, "wallets": wallets, "edges": edges,
            "recirculated_tokens": tokens, "sequence": sequence,
            "sequence_total": sequence_total, "sequence_wallets": len(drawn)}


class Question(BaseModel):
    question: str


class Verdict(BaseModel):
    case_id: str
    verdict: str          # "confirmed" | "rejected"
    note: str | None = None


@app.get("/api/case_basis/{case_id}")
def api_case_basis(case_id: str) -> dict:
    """The data an analyst rules on — never a bare button.

    Two grounded inputs, one per CockroachDB feature:
      1. A checklist computed from THIS ring's own trades (objective, pass/fail).
      2. The nearest already-decided cases (vector index) and how they were ruled,
         so the verdict is consistent with precedent instead of a lone judgment call.
    """
    if demo.ENABLED:                       # precedent lookup needs a Bedrock embedding
        return demo.lookup("case_basis", case_id) or demo.UNAVAILABLE
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT case_id, wallets, n_wallets, n_rings, has_high_confidence, "
            "       n_trades, total_eth, active_days, n_collections "
            "FROM collusion_cases WHERE case_id = %s",
            (case_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"error": "case not found"}
        cols = [d[0] for d in cur.description]
        case = dict(zip(cols, row))
        wl = case["wallets"].split(";")

        # --- checklist: derived from the ring's actual trades ---
        cur.execute(
            "SELECT count(*) FROM nft_trades "
            "WHERE seller = ANY(%s) AND buyer = ANY(%s) AND seller <> buyer", (wl, wl))
        inside = int(cur.fetchone()[0])
        cur.execute(
            "SELECT count(*) FROM nft_trades "
            "WHERE (seller = ANY(%s) OR buyer = ANY(%s)) AND seller <> buyer", (wl, wl))
        touched = int(cur.fetchone()[0]) or 1
        cur.execute(
            "SELECT token_id, count(*) n FROM nft_trades "
            "WHERE seller = ANY(%s) AND buyer = ANY(%s) "
            "GROUP BY token_id HAVING count(*) >= 3 ORDER BY n DESC LIMIT 1", (wl, wl))
        recirc = cur.fetchone()
        cur.execute(
            "SELECT stddev(price_eth) / nullif(avg(price_eth), 0) FROM nft_trades "
            "WHERE seller = ANY(%s) AND buyer = ANY(%s) AND price_eth > 0", (wl, wl))
        cv_row = cur.fetchone()[0]
        cv = float(cv_row) if cv_row is not None else None

    self_deal = round(inside / touched * 100)
    checklist = [
        {"label": "Closed-loop ring among ≥3 wallets",
         "detail": f"{case['n_wallets']} wallets, {case['n_rings']} closed loop(s)",
         "pass": case["n_wallets"] >= 3 and case["n_rings"] >= 1},
        {"label": "Same NFT returned to a prior holder (matched-order signature)",
         "detail": (f"token #{str(recirc[0])[:10]}… came back {int(recirc[1])}×"
                    if recirc else "no token recirculated 3+ times"),
         "pass": bool(case["has_high_confidence"])},
        {"label": "Trading stays inside the group (self-dealing)",
         "detail": f"{self_deal}% of members' trades are with each other ({inside}/{touched})",
         "pass": self_deal >= 80},
        {"label": "Round-trips repriced near-identically",
         "detail": (f"price variation (CV) {cv:.2f} — {'flat' if cv <= 0.35 else 'not flat'}"
                    if cv is not None else "no priced trades to test"),
         "pass": cv is not None and cv <= 0.35},
    ]

    # --- precedent: nearest decided cases via the vector index ---
    precedents = []
    try:
        emb = bedrock.embed(case_to_signal(case))
        hits = playbook.search_similar(emb, k=8, outcomes=None)
        with connect() as conn, conn.cursor() as cur:
            for h in hits:
                sc = h.get("source_case")
                if not sc or sc == case_id:
                    continue
                cur.execute(
                    "SELECT n_wallets, n_trades, has_high_confidence "
                    "FROM collusion_cases WHERE case_id = %s", (sc,))
                cr = cur.fetchone()
                precedents.append({
                    "case_id": sc, "outcome": h["outcome"],
                    "distance": round(float(h["cosine_distance"]), 3),
                    "n_wallets": int(cr[0]) if cr else None,
                    "n_trades": int(cr[1]) if cr else None,
                    "same_nft_loop": bool(cr[2]) if cr else None,
                })
                if len(precedents) >= 5:
                    break
    except Exception as e:  # noqa: BLE001 — precedent is best-effort, checklist still stands
        print(f"[case_basis] precedent lookup failed: {e}")

    n_conf = sum(1 for p in precedents if p["outcome"] == "confirmed")
    consistency = None
    if precedents:
        consistency = {
            "confirmed": n_conf, "rejected": len(precedents) - n_conf,
            "total": len(precedents),
            "agrees_with": ("confirmed" if n_conf > len(precedents) / 2
                            else "rejected" if n_conf < len(precedents) / 2 else "split"),
        }
    passed = sum(1 for c in checklist if c["pass"])
    return {"case_id": case_id, "checklist": checklist,
            "checklist_passed": passed, "checklist_total": len(checklist),
            "precedents": precedents, "consistency": consistency}


@app.post("/api/case_verdict")
def api_case_verdict(v: Verdict) -> dict:
    """Analyst rules on a REAL detected ring; the ring itself enters memory.

    This is the loop the whole project is about — without it the memory only ever
    learns from the scripted walkthrough. The embedded text is the same
    case_to_signal() description build_playbook uses for the baseline cases, so a
    ring confirmed here is indistinguishable from one learned offline.
    """
    if v.verdict not in ("confirmed", "rejected"):
        return {"error": "verdict must be 'confirmed' or 'rejected'"}
    if demo.ENABLED:                        # show the ruling; the demo cannot write
        return demo.verdict_response(v.case_id, v.verdict)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT case_id, n_wallets, n_rings, has_high_confidence, n_trades, "
            "       total_eth, active_days, n_collections "
            "FROM collusion_cases WHERE case_id = %s",
            (v.case_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"error": f"no such case: {v.case_id}"}
        cols = [d[0] for d in cur.description]
        case = dict(zip(cols, row))
        # already ruled on? don't write a duplicate row
        cur.execute("SELECT outcome FROM flagged_patterns WHERE source_case = %s", (v.case_id,))
        prior = cur.fetchone()
    if prior:
        return {"ok": True, "already": prior[0], "case_id": v.case_id, "stats": _stats()}

    text = case_to_signal(case)
    if v.note:
        text += f" Analyst note: {v.note}"
    pid = (confirm if v.verdict == "confirmed" else reject)(text, source_case=v.case_id)
    return {"ok": True, "verdict": v.verdict, "case_id": v.case_id,
            "pattern_id": pid, "signal": text, "stats": _stats()}


@app.get("/api/mcp_status")
def api_mcp_status() -> dict:
    """Whether the managed-MCP path is wired up, for the UI to show or hide the panel.

    The public demo ships no MCP key (a Cloud service-account key is write-capable),
    so it reports configured from the snapshot instead — the panel still demonstrates
    the feature, replaying answers and the SQL the model actually generated.
    """
    if demo.ENABLED:
        return {"configured": bool(demo.data().get("ask")), "demo": True}
    return {"configured": crdb_mcp.available()}


@app.get("/api/mode")
def api_mode() -> dict:
    """Lets the UI label a read-only deployment honestly."""
    return {"demo": demo.ENABLED}


@app.post("/api/ask")
def api_ask(q: Question) -> dict:
    """Answer an analyst question by querying the cluster through the managed MCP server."""
    question = q.question.strip()
    if not question:
        return {"error": "Ask a question first."}
    if demo.ENABLED:                        # the SQL is written by a Bedrock model
        return demo.lookup("ask", question) or demo.UNAVAILABLE
    try:
        return ask_memory.ask(question)
    except crdb_mcp.MCPError as e:
        return {"error": f"MCP server: {e}"}
    except ValueError as e:                       # model returned something non-read-only
        return {"error": str(e)}
    except Exception as e:                        # noqa: BLE001 — surface it, don't 500
        return {"error": f"{type(e).__name__}: {e}"}


@app.post("/api/reset")
def api_reset() -> dict:
    """Wipe analyst-learned + rejected patterns; keep the 37 base cases."""
    if demo.ENABLED:                        # nothing was written, so nothing to wipe
        return {"ok": True, "stats": demo.stats()}
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            # The baseline is defined positively: the 37 patterns mined from the
            # 2022 cases. Everything else — the scripted story, free-text triage,
            # analyst verdicts on live rings, CLI writes (source_case NULL) — was
            # learned at runtime and goes. Defining it this way means a new write
            # path can't quietly survive a reset the way the CLI once did.
            "DELETE FROM flagged_patterns WHERE source_case IS NULL OR source_case NOT IN "
            "(SELECT case_id FROM collusion_cases WHERE run_label = %s)",
            (BASELINE_RUN,),
        )
        conn.commit()
    return {"ok": True, "stats": _stats()}
