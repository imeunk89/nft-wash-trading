"""Score new/unseen NFT activity against the playbook memory.

Embeds the activity (Bedrock), finds the closest confirmed patterns (CockroachDB
distributed vector search), and asks Bedrock Claude to explain the match for an
analyst — the agent's flag-with-rationale step.

    python -m src.score_activity "three wallets bounced the same NFT 200 times in a week"
"""
from __future__ import annotations

import argparse

from . import bedrock
from .playbook import search_similar


def flag(activity_text: str, k: int = 3) -> dict:
    matches = search_similar(bedrock.embed(activity_text), k=k)
    top = matches[0]
    prompt = (
        "You are a market-surveillance assistant reviewing flagged on-chain NFT activity. "
        f'New activity: "{activity_text}". '
        f"Closest confirmed precedent (cosine distance {top['cosine_distance']:.2f}, "
        f"case {top['source_case']}): \"{top['description']}\". "
        "In 2-3 sentences, explain to an analyst why the new activity is suspicious and "
        "how it resembles the precedent."
    )
    return {"matches": matches, "explanation": bedrock.explain(prompt)}


def main() -> None:
    p = argparse.ArgumentParser(description="Score new activity against the playbook")
    p.add_argument("activity", help="Free-text description of the new activity")
    p.add_argument("-k", type=int, default=3)
    args = p.parse_args()

    result = flag(args.activity, args.k)
    print("Nearest playbook patterns (cosine distance):")
    for m in result["matches"]:
        print(f"  {m['cosine_distance']:.4f}  {m['source_case']}  {m['description'][:80]}")
    print("\nExplanation:")
    print(result["explanation"])


if __name__ == "__main__":
    main()
