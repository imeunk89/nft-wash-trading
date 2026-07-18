"""Apply sql/schema.sql to the CockroachDB cluster (idempotent).

Runs the whole file in one statement so the real SQL parser handles comments and
semicolons correctly (naive ';'-splitting breaks on semicolons inside comments).

    python -m src.crdb_init
"""
from __future__ import annotations

from . import config
from .db import connect


def main() -> None:
    sql = (config.SQL_DIR / "schema.sql").read_text()
    with connect() as conn:
        conn.autocommit = True
        conn.execute(sql)
    print("schema applied.")

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
        tables = [r[0] for r in cur.fetchall()]
    print("tables:", tables)


if __name__ == "__main__":
    main()
