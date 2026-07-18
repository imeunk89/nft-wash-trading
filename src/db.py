"""CockroachDB connection helper.

Reads COCKROACH_DATABASE_URL from .env. CockroachDB Cloud certificates chain to a
public CA, so we append `sslrootcert=system` (use the OS trust store) when the URL
uses sslmode=verify-full without an explicit cert — this avoids needing to download
a cluster-specific CA file.
"""
from __future__ import annotations

import certifi
import psycopg

from . import config


def connection_url() -> str:
    url = config.get_cockroach_url()
    # CockroachDB Cloud certs chain to a public CA. Point sslrootcert at certifi's
    # bundle (works cross-platform; macOS's system store isn't a libpq-readable file).
    if "sslrootcert" not in url:
        url += ("&" if "?" in url else "?") + f"sslrootcert={certifi.where()}"
    return url


def connect() -> psycopg.Connection:
    return psycopg.connect(connection_url())
