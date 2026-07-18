"""Central config: loads secrets from .env and defines project constants.

Import `get_dune_key()` / `get_etherscan_key()` rather than reading os.environ
directly, so key-missing / still-a-placeholder mistakes fail loudly and early.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project paths -------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
SQL_DIR = ROOT / "sql"

# Load .env once at import time.
load_dotenv(ROOT / ".env")

_PLACEHOLDERS = {
    "your_etherscan_api_key_here",
    "your_dune_api_key_here",
    "your_alchemy_api_key_here",
    "your_cockroach_connection_string_here",
    "",
    None,
}


def _require(name: str) -> str:
    val = os.environ.get(name)
    if val in _PLACEHOLDERS:
        raise RuntimeError(
            f"{name} is missing or still a placeholder. "
            f"Copy .env.example to .env and fill in your real key."
        )
    return val


def get_dune_key() -> str:
    return _require("DUNE_API_KEY")


def get_etherscan_key() -> str:
    return _require("ETHERSCAN_API_KEY")


def get_alchemy_key() -> str:
    return _require("ALCHEMY_API_KEY")


def get_cockroach_url() -> str:
    return _require("COCKROACH_DATABASE_URL")


def alchemy_nft_url(endpoint: str) -> str:
    """Alchemy NFT API v3 puts the key in the URL path."""
    return f"https://eth-mainnet.g.alchemy.com/nft/v3/{get_alchemy_key()}/{endpoint}"


# Domain constants ----------------------------------------------------------
BLOCKCHAIN = "ethereum"
MARKETPLACE = "looksrare"
YEAR = 2022

# Etherscan v2 unified endpoint (v1 mainnet endpoints are being deprecated).
ETHERSCAN_BASE = "https://api.etherscan.io/v2/api"
ETH_CHAIN_ID = 1

DUNE_BASE = "https://api.dune.com/api/v1"


def ensure_data_dirs() -> None:
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
