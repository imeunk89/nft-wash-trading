"""Vercel serverless entrypoint.

Vercel's Python runtime serves any module-level ASGI `app`, so this just puts the
repo root on sys.path and re-exports the FastAPI app from app/main.py. Keeping the
adapter here (rather than moving the app) means `uvicorn app.main:app` still works
unchanged for local development.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402,F401  (re-exported for Vercel)
