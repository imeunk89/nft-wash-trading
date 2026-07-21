"""The playbook — the agent's memory, backed by CockroachDB's vector index.

add_pattern() stores a discovered signal (text + embedding); search_similar()
finds the closest known patterns to new activity via distributed vector search.
Embeddings will come from AWS Bedrock (Milestone 3); the interface here is
embedding-source agnostic so the vector mechanism can be validated independently.

CockroachDB exposes pgvector-compatible distance operators:
  <-> L2   |  <=> cosine  |  <#> inner product.
We use cosine (<=>) — standard for semantic text embeddings.
"""
from __future__ import annotations

from .db import connect


def _vec(embedding) -> str:
    return "[" + ",".join(str(float(x)) for x in embedding) + "]"


def add_pattern(category: str, description: str, embedding,
                source_case: str | None = None, outcome: str = "pending") -> str:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO flagged_patterns (category, description, embedding, source_case, outcome) "
            "VALUES (%s, %s, %s::vector, %s, %s) RETURNING pattern_id",
            (category, description, _vec(embedding), source_case, outcome),
        )
        pid = cur.fetchone()[0]
        conn.commit()
        return str(pid)


OVERFETCH = 8        # candidates pulled per requested hit before filtering
MIN_OVERFETCH = 40   # floor, so a small k still sees a useful window


def search_similar(embedding, k: int = 5,
                   outcomes: tuple[str, ...] | None = ("confirmed",)) -> list[dict]:
    """Return the k playbook patterns most similar to `embedding` (cosine).

    By default only 'confirmed' patterns are searched — the trusted memory.
    Pass outcomes=("rejected",) to check whether activity resembles a known
    false positive (used to suppress repeat mistakes), or None to search all.
    """
    # A WHERE clause on the KNN query defeats the vector index — CockroachDB falls
    # back to a full scan (verified with EXPLAIN). So the ORDER BY ... LIMIT runs
    # unfiltered, which the index can serve, and the outcome filter is applied to
    # the candidates afterwards. Over-fetch so enough survive the filter.
    vec = _vec(embedding)
    limit = k if outcomes is None else max(k * OVERFETCH, MIN_OVERFETCH)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pattern_id, category, description, source_case, outcome, "
            "       embedding <=> %s::vector AS cosine_distance "
            "FROM flagged_patterns "
            "ORDER BY embedding <=> %s::vector "
            "LIMIT %s",
            (vec, vec, limit),
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    if outcomes is None:
        return rows[:k]
    hits = [r for r in rows if r["outcome"] in outcomes]
    if len(hits) >= k:
        return hits[:k]
    # Over-fetch missed some (e.g. a rare outcome buried far down the ranking):
    # fall back to the filtered scan so correctness never depends on the window.
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pattern_id, category, description, source_case, outcome, "
            "       embedding <=> %s::vector AS cosine_distance "
            "FROM flagged_patterns WHERE outcome = ANY(%s) "
            "ORDER BY embedding <=> %s::vector LIMIT %s",
            (vec, list(outcomes), vec, k),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
