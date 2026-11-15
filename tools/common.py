"""Shared helpers reused across the newsletter tools/ scripts."""

import hashlib
import os
import re
import time

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_env(key: str, required: bool = True) -> str:
    value = os.environ.get(key, "")
    if required and not value:
        raise RuntimeError(
            f"Missing required environment variable {key!r}. "
            f"Add it to .env at the repo root."
        )
    return value


def slugify(text: str, max_len: int = 40) -> str:
    """Filesystem-safe slug for a topic string, with a short hash suffix
    to avoid collisions when truncating long/non-ASCII topics."""
    ascii_text = text.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    if not slug:
        slug = "topic"
    if len(slug) > max_len:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        slug = f"{slug[:max_len].rstrip('-')}-{digest}"
    return slug


def retry(fn, attempts: int = 3, backoff: float = 2.0, retry_on=(Exception,)):
    """Call fn() with exponential backoff. Re-raises the last error if all
    attempts fail."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(backoff ** (attempt - 1))
    raise last_error


def load_recipients(path: str) -> list:
    """Read a plain-text recipient list: one email per line, blank lines
    and '#'-prefixed comment lines ignored."""
    if not os.path.exists(path):
        return []
    recipients = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            recipients.append(line)
    return recipients
