"""Research a topic via the Perplexity Agent API.

Calls Perplexity's Agent API with a research-oriented prompt and writes the
synthesized answer + sources to a JSON file. Does NOT attempt to parse the
answer into sections -- that synthesis step belongs to the agent, not this tool.

Migrated from the Sonar chat-completions endpoint, which Perplexity retires on
2026-09-27. See workflows/newsletter.md "Learnings" for the field mapping.

Exit codes:
    0  success
    3  research unavailable (no API key, or the API failed after retries).
       The agent falls back to its own WebSearch and hand-writes the same JSON
       shape -- see workflows/newsletter.md. Anything downstream reads only the
       output file, so both paths are interchangeable.
    1  unexpected error

CLI:
    python tools/research_perplexity.py --topic "..." --output .tmp/research_<slug>.json \
        [--preset medium] [--recency month]
"""

import argparse
import json
import sys
from datetime import datetime, timezone

import requests

from common import get_env, retry

API_URL = "https://api.perplexity.ai/v1/agent"

EXIT_RESEARCH_UNAVAILABLE = 3

INSTRUCTIONS = (
    "You are a research assistant preparing source material for a "
    "newsletter. Give a thorough, well-organized, factual summary "
    "of the topic with concrete details, numbers, and recent "
    "developments where relevant. Prefer credible sources."
)

# The Agent API has no search_recency_filter equivalent -- recency is steered
# through the prompt and the preset instead.
RECENCY_PHRASING = {
    "day": "Focus on developments from the last 24 hours.",
    "week": "Focus on developments from the last week.",
    "month": "Focus on developments from the last month.",
    "year": "Focus on developments from the last year.",
}


class _NonRetryable(Exception):
    """Wrapper so retry() doesn't burn attempts on errors that can't succeed."""

    def __init__(self, original):
        super().__init__(str(original))
        self.original = original


def _extract_text(output: list) -> str:
    """Walk the heterogeneous output[] array and concatenate assistant text.

    Items are typed (message / search_results / tool calls), so index-based
    access like output[0] is not safe.
    """
    chunks = []
    for item in output:
        if item.get("type") != "message":
            continue
        for part in item.get("content", []) or []:
            if part.get("type") in ("output_text", "text") and part.get("text"):
                chunks.append(part["text"])
    return "\n\n".join(chunks).strip()


def _extract_sources(output: list) -> list:
    """Collect web sources, de-duplicated by URL, preserving first-seen order.

    Sonar's old `citations` array was bare URL strings, which made every source
    title render as a naked URL. search_results carries real titles.
    """
    sources = []
    seen = set()
    for item in output:
        if item.get("type") != "search_results":
            continue
        for result in item.get("results", []) or []:
            url = (result.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append({
                "title": (result.get("title") or "").strip() or url,
                "url": url,
                "snippet": (result.get("snippet") or "").strip(),
            })
    return sources


def research(topic: str, preset: str = "medium", recency: str = None) -> dict:
    api_key = get_env("PERPLEXITY_API_KEY")

    prompt = topic
    if recency:
        prompt = f"{topic}\n\n{RECENCY_PHRASING[recency]}"

    payload = {
        "preset": preset,
        "input": prompt,
        "instructions": INSTRUCTIONS,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    def call():
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
        if resp.status_code == 401:
            raise RuntimeError(
                "Perplexity API returned 401 Unauthorized -- check PERPLEXITY_API_KEY in .env"
            )
        resp.raise_for_status()
        return resp.json()

    def is_retryable(exc):
        # Only rate limits and server-side faults are worth retrying; a 400
        # means the request itself is wrong and will fail identically 3x.
        if isinstance(exc, requests.ConnectionError):
            return True
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return exc.response.status_code == 429 or exc.response.status_code >= 500
        return False

    def call_if_retryable():
        try:
            return call()
        except (requests.HTTPError, requests.ConnectionError) as exc:
            if is_retryable(exc):
                raise
            raise _NonRetryable(exc) from exc

    try:
        data = retry(
            call_if_retryable,
            attempts=3,
            backoff=2.0,
            retry_on=(requests.HTTPError, requests.ConnectionError),
        )
    except _NonRetryable as wrapper:
        raise wrapper.original
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 429:
            raise RuntimeError("Perplexity API rate limit exceeded after retries (429).") from exc
        raise

    output = data.get("output", []) or []
    content = _extract_text(output)
    if not content:
        raise RuntimeError(
            "Perplexity returned no assistant text. Response keys: "
            f"{sorted(data.keys())}"
        )

    return {
        "topic": topic,
        "source": "perplexity_agent_api",
        "preset": preset,
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "content": content,
        "citations": _extract_sources(output),
    }


def main():
    parser = argparse.ArgumentParser(description="Research a topic via the Perplexity Agent API.")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--preset", default="medium",
                        choices=["low", "medium", "high", "xhigh"])
    parser.add_argument("--recency", default=None, choices=list(RECENCY_PHRASING))
    args = parser.parse_args()

    try:
        result = research(args.topic, preset=args.preset, recency=args.recency)
    except Exception as exc:
        print(f"research_perplexity.py failed: {exc}", file=sys.stderr)
        print(
            "RESEARCH_UNAVAILABLE: fall back to agent WebSearch and hand-write "
            f"{args.output} in the same schema (see workflows/newsletter.md).",
            file=sys.stderr,
        )
        sys.exit(EXIT_RESEARCH_UNAVAILABLE)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Wrote research to {args.output} ({len(result['citations'])} sources)")


if __name__ == "__main__":
    main()
