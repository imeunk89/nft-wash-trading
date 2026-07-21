"""AWS Bedrock wrapper — embeddings (Titan Text Embeddings V2) and explanations.

embed(text)   -> 1024-dim vector (matches the flagged_patterns VECTOR(1024) schema)
explain(text) -> human-readable rationale via a Claude model on Bedrock

Credentials + region come from the environment (.env, loaded by config).
"""
from __future__ import annotations

import json
import os

import boto3
from botocore.exceptions import ClientError

from . import config  # noqa: F401  (imported for its .env side effect)

EMBED_MODEL = "amazon.titan-embed-text-v2:0"
# Claude on Bedrock for explanations. Overridable via BEDROCK_CHAT_MODEL if the
# default id isn't enabled in the account/region.
# Latest active Claude on Bedrock is served via cross-region inference profiles
# (raw legacy model ids are blocked). Haiku 4.5 = fast + cheap, ideal for short
# surveillance rationales. Override with BEDROCK_CHAT_MODEL if needed.
CHAT_MODEL = os.environ.get(
    "BEDROCK_CHAT_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)
# Claude on Bedrock needs a one-time "Anthropic use case details" form per AWS
# account; until it's approved the account gets ResourceNotFoundException. Fall
# back to another Bedrock model so the rationale is never blank in a live demo —
# callers get the model id back and label the output with what actually ran.
FALLBACK_CHAT_MODEL = os.environ.get("BEDROCK_FALLBACK_CHAT_MODEL", "us.amazon.nova-lite-v1:0")
_UNAVAILABLE = ("ResourceNotFoundException", "AccessDeniedException", "ValidationException")

_client = None


def client():
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
    return _client


def embed(text: str, dimensions: int = 1024) -> list[float]:
    resp = client().invoke_model(
        modelId=EMBED_MODEL,
        body=json.dumps({"inputText": text, "dimensions": dimensions, "normalize": True}),
    )
    return json.loads(resp["body"].read())["embedding"]


def _converse(model_id: str, prompt: str, max_tokens: int) -> str:
    resp = client().converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0.2},
    )
    return resp["output"]["message"]["content"][0]["text"]


def explain_verbose(prompt: str, max_tokens: int = 400) -> tuple[str, str]:
    """Rationale + the model id that actually produced it."""
    try:
        return _converse(CHAT_MODEL, prompt, max_tokens), CHAT_MODEL
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code not in _UNAVAILABLE or FALLBACK_CHAT_MODEL in (None, "", CHAT_MODEL):
            raise
        print(f"[bedrock] {CHAT_MODEL} unavailable ({code}) — "
              f"falling back to {FALLBACK_CHAT_MODEL}")
        return _converse(FALLBACK_CHAT_MODEL, prompt, max_tokens), FALLBACK_CHAT_MODEL


def explain(prompt: str, max_tokens: int = 400) -> str:
    return explain_verbose(prompt, max_tokens)[0]
