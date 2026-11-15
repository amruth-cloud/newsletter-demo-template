"""Schema guard for the two JSON files the agent may hand-write.

Both the WebSearch research fallback and the outline step produce JSON by hand,
so a typo there surfaces as a confusing crash three tools downstream (or worse,
a silently chart-less newsletter). This catches it at the source with a specific
message.

Exits 0 when valid, 1 with a list of problems otherwise.

CLI:
    python tools/validate.py --research .tmp/research_<slug>.json
    python tools/validate.py --outline  .tmp/outline_<slug>.json
"""

import argparse
import json
import sys

# Must stay in sync with RENDERERS in generate_charts.py.
CHART_TYPES = {"bar", "line", "donut", "stat"}
MAX_IMAGES = 4


def _load(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_research(data: dict) -> list:
    problems = []
    if not isinstance(data, dict):
        return ["research JSON must be an object"]

    if not str(data.get("topic", "")).strip():
        problems.append("missing or empty 'topic'")
    if not str(data.get("content", "")).strip():
        problems.append("missing or empty 'content' -- nothing to write the newsletter from")

    citations = data.get("citations")
    if not isinstance(citations, list):
        problems.append("'citations' must be a list (use [] if genuinely none)")
    else:
        if not citations:
            problems.append(
                "'citations' is empty -- a newsletter with no sources isn't credible; "
                "broaden the research or flag this to the user before proceeding"
            )
        for i, c in enumerate(citations):
            if not isinstance(c, dict):
                problems.append(f"citations[{i}] must be an object with 'title' and 'url'")
                continue
            if not str(c.get("url", "")).strip():
                problems.append(f"citations[{i}] missing 'url'")
            if not str(c.get("title", "")).strip():
                problems.append(f"citations[{i}] missing 'title'")
    return problems


def validate_outline(data: dict) -> list:
    problems = []
    if not isinstance(data, dict):
        return ["outline JSON must be an object"]

    for field in ("subject", "preheader"):
        if not str(data.get(field, "")).strip():
            problems.append(f"missing or empty '{field}'")

    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        problems.append("'sections' must be a non-empty list")
        sections = []

    chart_specs = data.get("chart_specs", []) or []
    image_specs = data.get("image_specs", []) or []

    chart_ids = set()
    for i, spec in enumerate(chart_specs):
        if not isinstance(spec, dict):
            problems.append(f"chart_specs[{i}] must be an object")
            continue
        spec_id = str(spec.get("id", "")).strip()
        if not spec_id:
            problems.append(f"chart_specs[{i}] missing 'id'")
        elif spec_id in chart_ids:
            problems.append(f"chart_specs[{i}] duplicate id {spec_id!r}")
        else:
            chart_ids.add(spec_id)

        ctype = spec.get("type")
        if ctype not in CHART_TYPES:
            problems.append(
                f"chart_specs[{i}] type {ctype!r} unsupported; must be one of {sorted(CHART_TYPES)}"
            )
        # stat charts read value/caption; the plotted types need parallel arrays.
        if ctype in ("bar", "line", "donut"):
            labels = spec.get("labels")
            values = spec.get("values")
            if not isinstance(labels, list) or not labels:
                problems.append(f"chart_specs[{i}] ({ctype}) needs a non-empty 'labels' list")
            if not isinstance(values, list) or not values:
                problems.append(f"chart_specs[{i}] ({ctype}) needs a non-empty 'values' list")
            if isinstance(labels, list) and isinstance(values, list) and len(labels) != len(values):
                problems.append(
                    f"chart_specs[{i}] ({ctype}) labels/values length mismatch "
                    f"({len(labels)} vs {len(values)})"
                )

    image_ids = set()
    if len(image_specs) > MAX_IMAGES:
        problems.append(
            f"{len(image_specs)} image_specs exceeds the cap of {MAX_IMAGES} "
            f"(keeps the email from getting heavy)"
        )
    for i, spec in enumerate(image_specs):
        if not isinstance(spec, dict):
            problems.append(f"image_specs[{i}] must be an object")
            continue
        spec_id = str(spec.get("id", "")).strip()
        if not spec_id:
            problems.append(f"image_specs[{i}] missing 'id'")
        elif spec_id in image_ids:
            problems.append(f"image_specs[{i}] duplicate id {spec_id!r}")
        else:
            image_ids.add(spec_id)

        if not str(spec.get("prompt", "")).strip():
            problems.append(f"image_specs[{i}] missing 'prompt'")
        # Email clients block images by default; alt text is what most readers see.
        if not str(spec.get("alt", "")).strip():
            problems.append(f"image_specs[{i}] missing 'alt' (shown when images are blocked)")

        placement = spec.get("placement", "section")
        if placement not in ("hero", "section"):
            problems.append(
                f"image_specs[{i}] placement {placement!r} must be 'hero' or 'section'"
            )

    hero_count = sum(1 for s in image_specs
                     if isinstance(s, dict) and s.get("placement") == "hero")
    if hero_count > 1:
        problems.append(f"{hero_count} hero images declared; at most one is allowed")

    for i, section in enumerate(sections):
        if not isinstance(section, dict):
            problems.append(f"sections[{i}] must be an object")
            continue
        if not str(section.get("heading", "")).strip():
            problems.append(f"sections[{i}] missing 'heading'")
        if not str(section.get("body_html", "")).strip():
            problems.append(f"sections[{i}] missing 'body_html'")

        chart_ref = section.get("chart_ref")
        if chart_ref and chart_ref not in chart_ids:
            problems.append(
                f"sections[{i}] chart_ref {chart_ref!r} has no matching chart_specs entry"
            )
        image_ref = section.get("image_ref")
        if image_ref and image_ref not in image_ids:
            problems.append(
                f"sections[{i}] image_ref {image_ref!r} has no matching image_specs entry"
            )

    # Reverse check: a spec no section points at still gets generated and
    # CID-attached to the email, adding weight for something never displayed.
    referenced_charts = {s.get("chart_ref") for s in sections if isinstance(s, dict)}
    referenced_images = {s.get("image_ref") for s in sections if isinstance(s, dict)}
    for orphan in sorted(chart_ids - referenced_charts):
        problems.append(
            f"chart_specs {orphan!r} is never referenced by any section's chart_ref "
            f"-- it would be generated and attached but never shown"
        )
    # A hero image is placed by the template, not by a section ref, so it is
    # legitimately unreferenced.
    hero_ids = {str(s.get("id")) for s in image_specs
                if isinstance(s, dict) and s.get("placement") == "hero"}
    for orphan in sorted(image_ids - referenced_images - hero_ids):
        problems.append(
            f"image_specs {orphan!r} is never referenced by any section's image_ref "
            f"-- it would be generated and attached but never shown"
        )

    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        problems.append("'sources' must be a non-empty list -- carry the research citations through")

    return problems


def main():
    parser = argparse.ArgumentParser(description="Validate research or outline JSON.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--research")
    group.add_argument("--outline")
    args = parser.parse_args()

    path = args.research or args.outline
    kind = "research" if args.research else "outline"

    try:
        data = _load(path)
    except FileNotFoundError:
        print(f"validate.py: {path} does not exist", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"validate.py: {path} is not valid JSON -- {exc}", file=sys.stderr)
        sys.exit(1)

    problems = validate_research(data) if kind == "research" else validate_outline(data)

    if problems:
        print(f"validate.py: {len(problems)} problem(s) in {kind} file {path}:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)

    print(f"{path} is a valid {kind} file.")


if __name__ == "__main__":
    main()
