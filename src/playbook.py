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


def search_similar(embedding, k: int = 5) -> list[dict]:
    """Return the k playbook patterns most similar to `embedding` (cosine)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pattern_id, category, description, source_case, outcome, "
            "       embedding <=> %s::vector AS cosine_distance "
            "FROM flagged_patterns "
            "ORDER BY embedding <=> %s::vector "
            "LIMIT %s",
            (_vec(embedding), _vec(embedding), k),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
