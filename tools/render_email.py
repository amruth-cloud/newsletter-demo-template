"""Render an outline JSON into a styled, email-safe HTML file.

Renders templates/newsletter_base.html.j2 with the outline's content, then
runs the result through premailer to inline all CSS into style="" attributes
(Gmail/Outlook strip <style> blocks) and strips the now-redundant <style> tag.

Two modes, because cid: image references don't render in a browser:
  --mode preview  chart images point at local file paths, so a human can
                  open the HTML file directly in a browser to review it.
  --mode final    chart images point at cid:<chart_id> references, for MIME
                  embedding by tools/send_gmail.py.

CLI:
    python tools/render_email.py --outline .tmp/outline_<slug>.json \
        --charts-dir .tmp/charts/<slug>/ --mode preview|final \
        --out .tmp/preview_<slug>.html
"""

import argparse
import json
import os

from jinja2 import Environment, FileSystemLoader
from premailer import transform

from common import REPO_ROOT

TEMPLATES_DIR = os.path.join(REPO_ROOT, "templates")


def render(outline: dict, charts_dir: str, mode: str, images_dir: str = None,
           archive_url: str = None) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template("newsletter_base.html.j2")

    with open(os.path.join(TEMPLATES_DIR, "newsletter.css"), "r", encoding="utf-8") as f:
        css = f.read()

    chart_srcs = {}
    for spec in outline.get("chart_specs", []):
        chart_id = spec["id"]
        png_path = os.path.join(charts_dir, f"{chart_id}.png")
        if not os.path.exists(png_path):
            continue
        if mode == "final":
            chart_srcs[chart_id] = f"cid:{chart_id}"
        else:
            chart_srcs[chart_id] = os.path.abspath(png_path)

    # Email uses the downscaled JPEG derivative, not the archive's full-res PNG.
    image_srcs, hero_src, hero_alt = {}, None, ""
    for spec in outline.get("image_specs", []) or []:
        image_id = spec["id"]
        jpg_path = os.path.join(images_dir or "", f"{image_id}.email.jpg")
        if not os.path.exists(jpg_path):
            continue
        src = f"cid:{image_id}" if mode == "final" else os.path.abspath(jpg_path)
        if spec.get("placement") == "hero":
            hero_src, hero_alt = src, spec.get("alt", "")
        else:
            image_srcs[image_id] = {"src": src, "alt": spec.get("alt", "")}

    html = template.render(
        subject=outline.get("subject", outline.get("topic", "")),
        preheader=outline.get("preheader", ""),
        intro=outline.get("intro", ""),
        sections=outline.get("sections", []),
        sources=outline.get("sources", []),
        chart_srcs=chart_srcs,
        image_srcs=image_srcs,
        hero_src=hero_src,
        hero_alt=hero_alt,
        archive_url=archive_url,
        css=css,
    )

    inlined = transform(
        html,
        keep_style_tags=False,
        remove_classes=False,
        strip_important=False,
        cssutils_logging_level="CRITICAL",
    )
    return inlined


def main():
    parser = argparse.ArgumentParser(description="Render an outline into email-safe HTML.")
    parser.add_argument("--outline", required=True)
    parser.add_argument("--charts-dir", default=None)
    parser.add_argument("--images-dir", default=None)
    parser.add_argument("--mode", required=True, choices=["preview", "final"])
    parser.add_argument("--archive-url", default=None,
                        help="Published archive URL; adds the 'view in browser' link.")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    with open(args.outline, "r", encoding="utf-8") as f:
        outline = json.load(f)

    charts_dir = args.charts_dir or ""
    html = render(outline, charts_dir, args.mode,
                  images_dir=args.images_dir, archive_url=args.archive_url)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Wrote {args.mode} HTML to {args.out}")


if __name__ == "__main__":
    main()
