"""Track which published Artifact URL belongs to which newsletter slug.

Republishing an issue must land on its existing URL -- a new URL would orphan
the "view in browser" link already sitting in a sent email. The agent looks the
slug up here before publishing, and records the URL after.

Lives in archive/ rather than .tmp/ because .tmp/ is disposable by design and
losing this file means losing the link between an issue and its published page.

CLI:
    python tools/archive_index.py --get <slug>
    python tools/archive_index.py --set <slug> --url <url> [--subject "..."]
    python tools/archive_index.py --list
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from common import REPO_ROOT

INDEX_PATH = os.path.join(REPO_ROOT, "archive", "index.jsonl")


def _entries() -> list:
    if not os.path.exists(INDEX_PATH):
        return []
    entries = []
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def get_url(slug: str):
    """Latest URL recorded for a slug, or None."""
    for entry in reversed(_entries()):
        if entry.get("slug") == slug:
            return entry.get("url")
    return None


def set_url(slug: str, url: str, subject: str = "") -> dict:
    entry = {
        "slug": slug,
        "url": url,
        "subject": subject,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    with open(INDEX_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def main():
    parser = argparse.ArgumentParser(description="Map newsletter slugs to published archive URLs.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--get", metavar="SLUG")
    group.add_argument("--set", metavar="SLUG")
    group.add_argument("--list", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--subject", default="")
    args = parser.parse_args()

    if args.list:
        entries = _entries()
        if not entries:
            print("No archive entries recorded yet.")
            return
        for entry in entries:
            print(f"{entry['slug']}\t{entry['url']}\t{entry.get('subject','')}")
        return

    if args.get:
        url = get_url(args.get)
        # Print nothing on a miss so the caller can test for empty output.
        if url:
            print(url)
        return

    if not args.url:
        print("--set requires --url", file=sys.stderr)
        sys.exit(1)
    entry = set_url(args.set, args.url, args.subject)
    print(f"Recorded {entry['slug']} -> {entry['url']}")


if __name__ == "__main__":
    main()
