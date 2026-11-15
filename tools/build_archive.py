"""Build the standalone web-archive page for a newsletter issue.

Renders templates/archive.html.j2 with every asset embedded as a base64 data:
URI. The embedding is not an optimization -- published Artifacts cannot load
images from any external host, so a page that references chart PNGs by path
renders with broken images. Self-contained is the only shape that works.

Output is artifact-ready page content (no doctype/head/body wrapper); the agent
publishes it with the Artifact tool and feeds the resulting URL back into
render_email.py --archive-url.

CLI:
    python tools/build_archive.py --outline .tmp/outline_<slug>.json \
        --charts-dir .tmp/charts/<slug>/ --images-dir .tmp/images/<slug>/ \
        --out .tmp/archive_<slug>.html
"""

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from common import REPO_ROOT

TEMPLATES_DIR = os.path.join(REPO_ROOT, "templates")

MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

WORDS_PER_MINUTE = 220


def data_uri(path: str) -> str:
    """Inline a local image as a base64 data: URI."""
    ext = os.path.splitext(path)[1].lower()
    mime = MIME_BY_EXT.get(ext)
    if mime is None:
        raise ValueError(f"Unsupported image type {ext!r} for {path}")
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


def reading_time(outline: dict) -> int:
    words = len(strip_tags(outline.get("intro", "")).split())
    for section in outline.get("sections", []) or []:
        words += len(strip_tags(section.get("heading", "")).split())
        words += len(strip_tags(section.get("body_html", "")).split())
    return max(1, round(words / WORDS_PER_MINUTE))


def as_html(text: str) -> str:
    """Intro is authored as plain prose; wrap paragraphs so the drop cap and
    paragraph spacing have real elements to attach to."""
    if not text:
        return ""
    if "<p" in text.lower():
        return text
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "".join(f"<p>{p}</p>" for p in paragraphs)


def host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def build(outline: dict, charts_dir: str, images_dir: str) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("archive.html.j2")

    # Index the specs so sections can resolve their refs.
    charts = {}
    for spec in outline.get("chart_specs", []) or []:
        path = os.path.join(charts_dir or "", f"{spec['id']}.png")
        if os.path.exists(path):
            charts[spec["id"]] = {
                "src": data_uri(path),
                "alt": spec.get("title") or "Chart",
                "title": spec.get("title", ""),
            }

    images, hero = {}, None
    for spec in outline.get("image_specs", []) or []:
        # Archive uses the full-resolution PNG; the email JPEG stays for email.
        path = os.path.join(images_dir or "", f"{spec['id']}.png")
        if not os.path.exists(path):
            continue
        entry = {"src": data_uri(path), "alt": spec.get("alt", "")}
        if spec.get("placement") == "hero":
            hero = entry
        else:
            images[spec["id"]] = entry

    sections = []
    for section in outline.get("sections", []) or []:
        sections.append({
            "heading": section.get("heading", ""),
            "body_html": section.get("body_html", ""),
            "chart": charts.get(section.get("chart_ref")),
            "image": images.get(section.get("image_ref")),
        })

    sources = []
    for source in outline.get("sources", []) or []:
        url = source.get("url", "")
        sources.append({
            "title": source.get("title") or url,
            "url": url,
            "host": host_of(url),
        })

    return template.render(
        subject=outline.get("subject", outline.get("topic", "Newsletter")),
        preheader=outline.get("preheader", ""),
        intro_html=as_html(outline.get("intro", "")),
        intro=outline.get("intro", ""),
        sections=sections,
        sources=sources,
        hero=hero,
        source_count=len(sources),
        reading_time=reading_time(outline),
        dateline=datetime.now().strftime("%d %B %Y").lstrip("0"),
    )


def main():
    parser = argparse.ArgumentParser(description="Build the standalone web-archive page.")
    parser.add_argument("--outline", required=True)
    parser.add_argument("--charts-dir", default=None)
    parser.add_argument("--images-dir", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.outline, "r", encoding="utf-8") as f:
        outline = json.load(f)

    try:
        html = build(outline, args.charts_dir, args.images_dir)
    except Exception as exc:
        print(f"build_archive.py failed: {exc}", file=sys.stderr)
        sys.exit(1)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = os.path.getsize(args.out) / 1024
    print(f"Wrote archive page to {args.out} ({size_kb:.0f}KB, assets embedded)")
    if size_kb > 16 * 1024:
        print("WARNING: page exceeds the 16MB Artifact limit.", file=sys.stderr)


if __name__ == "__main__":
    main()
