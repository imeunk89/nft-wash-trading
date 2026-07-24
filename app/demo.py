"""Read-only public-demo mode.

The deployed demo connects with a read-only CockroachDB user and ships NO AWS
credentials, so it cannot spend money and cannot write. Everything that would call
AWS Bedrock — or write a row — is replayed from app/demo_data.json instead.

Those values are real: `python -m src.make_demo_snapshot` captured them from one
genuine run against the live cluster and live Bedrock. Read-only endpoints (cases,
daily catch, case detail, stats) are NOT replayed — they still query the cluster,
so the evidence a judge inspects is live data.

Mode is implicit: no AWS key => demo mode. Force with DEMO_MODE=1 / DEMO_MODE=0.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).parent

_forced = os.environ.get("DEMO_MODE")
ENABLED = (_forced == "1") or (_forced != "0" and not os.environ.get("AWS_ACCESS_KEY_ID"))

_DATA: dict | None = None

UNAVAILABLE = {
    "error": "Not part of the public read-only demo — this one needs a live AWS Bedrock "
             "call. Try a preset, or run the project locally (see the README)."
}


def data() -> dict:
    global _DATA
    if _DATA is None:
        path = HERE / "demo_data.json"
        _DATA = json.loads(path.read_text()) if path.exists() else {}
    return _DATA


def lookup(kind: str, key: str) -> dict | None:
    return data().get(kind, {}).get(key)


def stats() -> dict:
    """Baseline memory counts, as captured after a reset."""
    return (data().get("reset") or {}).get("stats", {"confirmed": 37})


def verdict_response(case_id: str, verdict: str) -> dict:
    """Scripted reply for ruling on a real ring while writes are disabled."""
    n = dict(stats())
    key = "confirmed" if verdict == "confirmed" else "rejected"
    n[key] = n.get(key, 0) + 1
    return {"ok": True, "verdict": verdict, "case_id": case_id, "demo": True,
            "stats": n,
            "note": "Read-only public demo — the verdict is shown but not persisted."}
