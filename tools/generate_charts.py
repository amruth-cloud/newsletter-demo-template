"""Render chart_specs from an outline JSON into email-safe PNG infographics.

Email clients don't reliably render SVG or fetch external images, so every
chart is rendered as a standalone PNG sized for the 600px email content
column (rendered at 2x for retina) and embedded later as an inline CID
image by tools/send_gmail.py.

Palette/style follow the project's dataviz skill conventions: fixed-order
categorical hues, minimal chartjunk, light chart surface, muted gridlines.
Colors below are the skill's validated default palette (references/palette.md),
light mode only -- these charts are static raster images with no dark-mode
variant.

CLI:
    python tools/generate_charts.py --outline .tmp/outline_<slug>.json \
        --outdir .tmp/charts/<slug>/
"""

import argparse
import json
import os
import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge

# dataviz skill reference palette (light mode)
CATEGORICAL = [
    "#2a78d6",  # 1 blue
    "#eb6834",  # 2 orange
    "#1baf7a",  # 3 aqua
    "#eda100",  # 4 yellow
    "#e87ba4",  # 5 magenta
    "#008300",  # 6 green
    "#4a3aa7",  # 7 violet
    "#e34948",  # 8 red
]
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"

FONT_FAMILY = ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"]

# 600px content column, rendered at 2x for retina displays
FIG_WIDTH_IN = 6.0
FIG_HEIGHT_IN = 3.6
DPI = 200


def _new_figure():
    fig, ax = plt.subplots(figsize=(FIG_WIDTH_IN, FIG_HEIGHT_IN), dpi=DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    plt.rcParams["font.family"] = FONT_FAMILY
    return fig, ax


def _style_axes(ax):
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("bottom", "left"):
        ax.spines[spine].set_color(BASELINE)
    ax.tick_params(colors=INK_MUTED, labelsize=9)
    ax.yaxis.grid(True, color=GRIDLINE, linewidth=1)
    ax.set_axisbelow(True)


def render_bar(spec: dict, outpath: str):
    fig, ax = _new_figure()
    labels = spec["labels"]
    values = spec["values"]
    # A single series (values across categories/time) is one entity, so it
    # gets one color -- distinct hues per bar would wrongly imply each bar
    # is a different series needing a legend.
    ax.bar(labels, values, color=CATEGORICAL[0], width=0.6)
    ax.xaxis.grid(False)
    _style_axes(ax)
    ax.spines["left"].set_visible(False)
    if spec.get("title"):
        ax.set_title(spec["title"], color=INK_PRIMARY, fontsize=12, loc="left", pad=12)
    fig.tight_layout()
    fig.savefig(outpath, facecolor=SURFACE)
    plt.close(fig)


def render_line(spec: dict, outpath: str):
    fig, ax = _new_figure()
    labels = spec["labels"]
    values = spec["values"]
    ax.plot(labels, values, color=CATEGORICAL[0], linewidth=2, marker="o", markersize=6)
    _style_axes(ax)
    if spec.get("title"):
        ax.set_title(spec["title"], color=INK_PRIMARY, fontsize=12, loc="left", pad=12)
    fig.tight_layout()
    fig.savefig(outpath, facecolor=SURFACE)
    plt.close(fig)


def render_donut(spec: dict, outpath: str):
    fig, ax = _new_figure()
    labels = spec["labels"]
    values = spec["values"]
    colors = [CATEGORICAL[i % len(CATEGORICAL)] for i in range(len(values))]
    wedges, _ = ax.pie(
        values,
        colors=colors,
        startangle=90,
        wedgeprops=dict(width=0.38, edgecolor=SURFACE, linewidth=2),
    )
    ax.axis("equal")
    ax.legend(
        wedges,
        labels,
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        frameon=False,
        fontsize=9,
        labelcolor=INK_SECONDARY,
    )
    if spec.get("title"):
        ax.set_title(spec["title"], color=INK_PRIMARY, fontsize=12, loc="left", pad=12)
    fig.tight_layout()
    fig.savefig(outpath, facecolor=SURFACE)
    plt.close(fig)


# Rough character budget for one line at the sizes below, across the 600px
# content column. Captions longer than this used to run off the right edge.
STAT_CAPTION_WRAP = 52
STAT_TITLE_WRAP = 62


def render_stat(spec: dict, outpath: str):
    value = spec.get("value", spec["values"][0] if spec.get("values") else "")
    caption = spec.get("caption", spec["labels"][0] if spec.get("labels") else "")
    title = spec.get("title", "")

    caption_lines = textwrap.wrap(str(caption), STAT_CAPTION_WRAP) or [""]
    title_lines = textwrap.wrap(str(title), STAT_TITLE_WRAP) if title else []

    # Grow the figure so wrapped lines have somewhere to go instead of
    # overflowing the canvas.
    extra = 0.22 * (len(caption_lines) - 1 + max(0, len(title_lines) - 1))
    fig, ax = plt.subplots(figsize=(FIG_WIDTH_IN, FIG_HEIGHT_IN * 0.6 + extra), dpi=DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    y = 0.94
    if title_lines:
        ax.text(0.02, y, "\n".join(title_lines), fontsize=11, color=INK_MUTED,
                family=FONT_FAMILY, ha="left", va="top", linespacing=1.45)
        y -= 0.13 * len(title_lines)

    ax.text(0.02, y - 0.06, str(value), fontsize=40, color=INK_PRIMARY, fontweight="bold",
            family=FONT_FAMILY, ha="left", va="top")
    y -= 0.40

    ax.text(0.02, y, "\n".join(caption_lines), fontsize=13, color=INK_SECONDARY,
            family=FONT_FAMILY, ha="left", va="top", linespacing=1.5)

    fig.savefig(outpath, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.28)
    plt.close(fig)


RENDERERS = {
    "bar": render_bar,
    "line": render_line,
    "donut": render_donut,
    "stat": render_stat,
}


def generate_chart(spec: dict, outpath: str) -> str:
    chart_type = spec.get("type")
    renderer = RENDERERS.get(chart_type)
    if renderer is None:
        raise ValueError(f"Unsupported chart type {chart_type!r}; supported: {list(RENDERERS)}")
    renderer(spec, outpath)
    return outpath


def main():
    parser = argparse.ArgumentParser(description="Generate chart PNGs from an outline's chart_specs.")
    parser.add_argument("--outline", required=True)
    parser.add_argument("--outdir", required=True)
    args = parser.parse_args()

    with open(args.outline, "r", encoding="utf-8") as f:
        outline = json.load(f)

    chart_specs = outline.get("chart_specs", [])
    os.makedirs(args.outdir, exist_ok=True)

    written = []
    for spec in chart_specs:
        outpath = os.path.join(args.outdir, f"{spec['id']}.png")
        generate_chart(spec, outpath)
        written.append(outpath)
        print(f"Wrote {outpath}")

    if not written:
        print("No chart_specs in outline -- nothing to generate.")


if __name__ == "__main__":
    main()
