"""CockroachDB Cloud Managed MCP Server client.

The agent reaches the cluster through CockroachDB's *managed* MCP endpoint rather
than opening its own SQL connection. That matters for a surveillance tool: the
endpoint is read-only by default, every call is scoped to one cluster by API-key
RBAC, and the tool calls are auditable — an analyst-facing agent should not hold
raw write credentials.

    https://cockroachlabs.cloud/mcp   (header: mcp-cluster-id: <cluster uuid>)

Auth is a CockroachDB Cloud **service account API key** (Bearer). The console also
offers OAuth 2.1 for interactive use; a server-side agent wants the key.

Transport is MCP streamable HTTP: JSON-RPC over POST, where the server may answer
with either application/json or an SSE stream. Both are handled here.

    from .crdb_mcp import MCPClient
    with MCPClient() as m:
        print(m.list_tools())
        print(m.call("run_query", {"statement": "SELECT count(*) FROM collusion_cases"}))
"""
from __future__ import annotations

import json
import os
from typing import Any

import requests

from . import config  # noqa: F401  (imported for its .env side effect)

DEFAULT_URL = "https://cockroachlabs.cloud/mcp"
PROTOCOL_VERSION = "2025-06-18"

# The managed server exposes write tools (insert_rows, create_table, create_database)
# and a Cluster Admin API key really can call them — verified against the live
# endpoint. A surveillance agent answering analyst questions has no business
# mutating evidence, so the client refuses anything outside this allowlist. Defence
# in depth: narrow the service account's role too, don't rely on this alone.
READ_ONLY_TOOLS = frozenset({
    "list_clusters", "get_cluster", "list_databases", "list_tables",
    "get_table_schema", "select_query", "explain_query", "show_statement",
    "show_running_queries",
})


class MCPError(RuntimeError):
    """A JSON-RPC error, or a transport failure, from the managed MCP server."""


def _parse(resp: requests.Response) -> dict[str, Any]:
    """Read one JSON-RPC result from a JSON or SSE response."""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        # SSE frames: lines of "data: {...}"; take the first data payload that
        # carries a JSON-RPC envelope.
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                if "result" in payload or "error" in payload:
                    return payload
        raise MCPError(f"no JSON-RPC frame in SSE response: {resp.text[:300]}")
    return resp.json()


class MCPClient:
    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        cluster_id: str | None = None,
        timeout: int = 45,
    ):
        self.url = url or os.environ.get("COCKROACH_MCP_URL", DEFAULT_URL)
        self.api_key = api_key or os.environ.get("COCKROACH_API_KEY", "")
        self.cluster_id = cluster_id or os.environ.get("COCKROACH_MCP_CLUSTER_ID", "")
        self.timeout = timeout
        self._id = 0
        self._session_id: str | None = None
        self._session = requests.Session()

    # -- plumbing -------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self.cluster_id:
            h["mcp-cluster-id"] = self.cluster_id
        if self._session_id:
            h["mcp-session-id"] = self._session_id
        return h

    def _rpc(self, method: str, params: dict | None = None, notify: bool = False):
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            self._id += 1
            body["id"] = self._id

        r = self._session.post(
            self.url, headers=self._headers(), json=body, timeout=self.timeout
        )
        # the server hands back a session id on initialize; reuse it afterwards
        if sid := r.headers.get("mcp-session-id"):
            self._session_id = sid

        if r.status_code == 401:
            raise MCPError(
                "MCP server rejected the credentials (401). Set COCKROACH_API_KEY to a "
                "CockroachDB Cloud service account API key with access to this cluster."
            )
        if notify:
            return None
        if not r.ok:
            raise MCPError(f"{method} -> HTTP {r.status_code}: {r.text[:300]}")

        payload = _parse(r)
        if "error" in payload:
            raise MCPError(f"{method} -> {payload['error']}")
        return payload.get("result")

    # -- lifecycle ------------------------------------------------------------
    def connect(self) -> dict[str, Any]:
        if not self.api_key:
            raise MCPError(
                "COCKROACH_API_KEY is not set. Create a service account API key in the "
                "CockroachDB Cloud console (Access Management -> Service Accounts) and "
                "put it in .env."
            )
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "nft-wash-trading-agent", "version": "1.0"},
            },
        )
        self._rpc("notifications/initialized", {}, notify=True)
        return result or {}

    def __enter__(self) -> MCPClient:
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self._session.close()

    # -- tools ----------------------------------------------------------------
    def list_tools(self) -> list[dict[str, Any]]:
        return (self._rpc("tools/list") or {}).get("tools", [])

    def call(self, name: str, arguments: dict[str, Any], allow_writes: bool = False) -> str:
        """Invoke a tool and flatten its content blocks to text.

        Refuses non-read-only tools unless explicitly opted into, so an LLM that
        hallucinates `insert_rows` cannot touch the evidence tables.
        """
        if not allow_writes and name not in READ_ONLY_TOOLS:
            raise MCPError(
                f"tool {name!r} is not in the read-only allowlist; refusing to call it"
            )
        result = self._rpc("tools/call", {"name": name, "arguments": arguments}) or {}
        if result.get("isError"):
            raise MCPError(f"tool {name} failed: {result.get('content')}")
        parts = [
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p).strip()


def available() -> bool:
    """True when the MCP path is configured — lets callers fall back to SQL."""
    return bool(os.environ.get("COCKROACH_API_KEY"))


if __name__ == "__main__":  # python -m src.crdb_mcp  -> smoke test
    with MCPClient() as m:
        tools = m.list_tools()
        print(f"connected · {len(tools)} tools exposed by the managed MCP server\n")
        for t in tools:
            print(f"  {t['name']:<28} {(t.get('description') or '').splitlines()[0][:80]}")
