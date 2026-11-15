"""Generate the newsletter's editorial illustrations via the Gemini API.

Division of labour with generate_charts.py, and the reason it exists:
image models are unreliable at rendering precise text -- Google and OpenAI both
say so in their own docs. So AI art here is *illustrative only*. Every prompt
gets NO_TEXT_RULE appended, and anything carrying a real number is rendered by
generate_charts.py from the outline's actual values instead. An AI-drawn chart
looks authoritative while being unaudited; that combination is what sinks a
sourced newsletter.

Runs on Gemini's free tier (no card required). Generated images are cached by
prompt hash, so re-running a topic or editing one section doesn't redraw
everything and burn the daily quota.

Two derivatives per image, both via Pillow (already installed with matplotlib):
  <id>.png       full resolution, embedded in the web archive
  <id>.email.jpg 600px wide, quality 82, CID-embedded in the email

Exit codes:
    0  success (including "everything was cached")
    3  images unavailable (no API key, or the API failed) -- the pipeline can
       still produce a chart-only newsletter; the workflow decides.
    1  unexpected error

CLI:
    python tools/generate_images.py --outline .tmp/outline_<slug>.json \
        --outdir .tmp/images/<slug>/ [--model gemini-2.5-flash-image] [--force]
"""

import argparse
import base64
import hashlib
import io
import json
import os
import sys

import requests
from PIL import Image

from common import get_env, retry

# The image models are served from generateContent, not the interactions
# endpoint. interactions does route, but silently maps to a preview model whose
# free-tier limit is literally 0 requests/day, producing a misleading 429.
API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.1-flash-image"

# Cloudflare Workers AI. Free allocation is 10,000 neurons/day; FLUX.1-schnell
# costs ~4.8 neurons per 512x512 tile, so a 1024x1024 image is ~19 neurons and
# a three-image issue ~58 -- comfortably inside the daily allowance.
CF_URL_TEMPLATE = ("https://api.cloudflare.com/client/v4/accounts/{account}"
                   "/ai/run/{model}")
CF_DEFAULT_MODEL = "@cf/black-forest-labs/flux-1-schnell"

EXIT_IMAGES_UNAVAILABLE = 3

MAX_IMAGES = 4
EMAIL_WIDTH_PX = 600
EMAIL_JPEG_QUALITY = 82

# Appended to every prompt. The newsletter's credibility depends on numbers
# coming only from generate_charts.py, so no generated image may contain any.
NO_TEXT_RULE = (
    # Phrased POSITIVELY on purpose. An earlier version enumerated forbidden
    # things ("no rulers, no dials, no gauges, no numbered increments") and
    # FLUX promptly drew a numbered ruler: naming a concept in a positive
    # prompt raises its likelihood, and this API exposes no negative-prompt
    # field. Describing blank surfaces works; listing what to omit does not.
    "Every surface in the image is completely blank, smooth and unmarked. "
    "No text, no numbers and no lettering anywhere."
)

# Shared art direction so the hero and section art read as one commissioned set
# rather than three unrelated stock pictures. Palette tracks templates/newsletter.css.
HOUSE_STYLE = (
    "Flat editorial vector illustration, clean geometric shapes, subtle grain texture, "
    "generous negative space, calm restrained palette of muted blue (#2a78d6), "
    "warm orange (#eb6834) and soft off-white (#fcfcfb) on a light background, "
    "confident modern magazine art direction, no photorealism, no 3D render."
)


def build_prompt(spec: dict) -> str:
    """Compose the final prompt: the outline's idea + house style + the no-text rule."""
    return f"{spec['prompt'].strip()}\n\n{HOUSE_STYLE}\n\n{NO_TEXT_RULE}"


def prompt_hash(prompt: str, model: str) -> str:
    """Cache key. Includes the model so switching models invalidates the cache."""
    return hashlib.sha256(f"{model}\n{prompt}".encode("utf-8")).hexdigest()


def _extract_image_bytes(data: dict) -> bytes:
    """Pull base64 image bytes out of the response.

    Gemini has shipped more than one response envelope, so check the documented
    Interactions path first and fall back to the generateContent-style parts
    array rather than crashing on a shape difference.
    """
    # Interactions API: output_image.data
    output_image = data.get("output_image")
    if isinstance(output_image, dict) and output_image.get("data"):
        return base64.b64decode(output_image["data"])

    # Some responses nest it under interaction/output.
    for container_key in ("interaction", "output", "response"):
        container = data.get(container_key)
        if isinstance(container, dict):
            oi = container.get("output_image")
            if isinstance(oi, dict) and oi.get("data"):
                return base64.b64decode(oi["data"])

    # generateContent style: candidates[].content.parts[].inlineData.data
    for candidate in data.get("candidates", []) or []:
        content = candidate.get("content", {}) or {}
        for part in content.get("parts", []) or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                return base64.b64decode(inline["data"])

    # Interactions steps list.
    for step in data.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        oi = step.get("output_image")
        if isinstance(oi, dict) and oi.get("data"):
            return base64.b64decode(oi["data"])

    raise RuntimeError(
        "Could not locate image bytes in the Gemini response. Top-level keys: "
        f"{sorted(data.keys())}"
    )


def call_gemini(prompt: str, model: str, api_key: str) -> bytes:
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    url = API_URL_TEMPLATE.format(model=model)

    def call():
        resp = requests.post(url, headers=headers, json=payload, timeout=180)
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"Gemini API returned {resp.status_code} -- check GEMINI_API_KEY in .env"
            )
        if resp.status_code == 404:
            raise RuntimeError(
                f"Model {model!r} is not available to this key. List what is available with:\n"
                f"  curl -H 'x-goog-api-key: $GEMINI_API_KEY' "
                f"https://generativelanguage.googleapis.com/v1beta/models"
            )
        if resp.status_code == 429:
            # Verified 2026-09-19: image generation is NOT on the Gemini free
            # tier -- every image model 429s on a free key, and the preview
            # model states "limit: 0 requests per day on Free Tier". This is a
            # billing state, not a transient quota, so retrying will not help.
            raise RuntimeError(
                "Gemini refused the image request (429). Image generation is not "
                "included in the Gemini free tier -- every image model returns this "
                "on a free key, regardless of usage. Enable billing at "
                "https://ai.dev/rate-limit, or switch to a free provider "
                "(see workflows/newsletter.md 'Illustrations')."
            )
        resp.raise_for_status()
        return resp.json()

    data = retry(call, attempts=3, backoff=2.0, retry_on=(requests.ConnectionError,))
    return _extract_image_bytes(data)


def center_crop(image: Image.Image, ratio: float) -> Image.Image:
    """Center-crop to a target width/height ratio.

    Done in Pillow rather than CSS because email clients broadly ignore
    object-fit/aspect-ratio: a square 1024x1024 generation would otherwise
    render as a 536px-tall slab in a 600px-wide email.
    """
    target_h = image.width / ratio
    if target_h <= image.height:
        new_w, new_h = image.width, round(target_h)
    else:
        new_w, new_h = round(image.height * ratio), image.height
    left = (image.width - new_w) // 2
    top = (image.height - new_h) // 2
    return image.crop((left, top, left + new_w, top + new_h))


def call_cloudflare(prompt: str, model: str, account: str, token: str) -> bytes:
    """Generate one image via Cloudflare Workers AI (FLUX.1-schnell).

    Returns base64 JPEG bytes in result.image. Unlike Gemini, the free
    allocation genuinely covers image generation -- see workflows Learnings.
    """
    url = CF_URL_TEMPLATE.format(account=account, model=model)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def call():
        resp = requests.post(url, headers=headers,
                             json={"prompt": prompt, "steps": 4}, timeout=180)
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"Cloudflare returned {resp.status_code} -- check CLOUDFLARE_API_TOKEN "
                f"has the 'Workers AI' permission."
            )
        if resp.status_code == 404:
            raise RuntimeError(
                f"Cloudflare could not route to the model. Check CLOUDFLARE_ACCOUNT_ID "
                f"and that {model!r} is a valid Workers AI model id."
            )
        if resp.status_code == 429:
            raise RuntimeError(
                "Cloudflare daily neuron allowance exhausted (429). It resets at "
                "00:00 UTC. On the Workers Free plan this fails rather than billing you."
            )
        resp.raise_for_status()
        return resp.json()

    data = retry(call, attempts=3, backoff=2.0, retry_on=(requests.ConnectionError,))
    if not data.get("success"):
        raise RuntimeError(f"Cloudflare reported failure: {data.get('errors')}")
    image_b64 = (data.get("result") or {}).get("image")
    if not image_b64:
        raise RuntimeError(f"No image in Cloudflare response; keys: {list(data.get('result', {}))}")
    return base64.b64decode(image_b64)


def write_derivatives(raw: bytes, outdir: str, spec_id: str, placement: str = "section") -> tuple:
    """Write the full-res PNG (archive) and the cropped, downscaled JPEG (email).

    The archive keeps the uncropped original -- it can afford the height and
    crops via CSS instead.
    """
    png_path = os.path.join(outdir, f"{spec_id}.png")
    jpg_path = os.path.join(outdir, f"{spec_id}.email.jpg")

    image = Image.open(io.BytesIO(raw))
    image.save(png_path, format="PNG")

    # Matches the archive template's .hero (16/9) and .section-art (3/2) crops.
    email_image = center_crop(image.convert("RGB"), 16 / 9 if placement == "hero" else 3 / 2)
    if email_image.width > EMAIL_WIDTH_PX:
        ratio = EMAIL_WIDTH_PX / email_image.width
        new_size = (EMAIL_WIDTH_PX, max(1, round(email_image.height * ratio)))
        email_image = email_image.resize(new_size, Image.LANCZOS)
    email_image.save(jpg_path, format="JPEG", quality=EMAIL_JPEG_QUALITY, optimize=True)

    return png_path, jpg_path


def main():
    parser = argparse.ArgumentParser(description="Generate editorial illustrations via Gemini.")
    parser.add_argument("--outline", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--provider", default="auto",
                        choices=["auto", "cloudflare", "gemini"],
                        help="auto prefers Cloudflare when its credentials are set.")
    parser.add_argument("--model", default=None,
                        help="Override the provider's default model.")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate even when the cached prompt hash matches.")
    args = parser.parse_args()

    with open(args.outline, "r", encoding="utf-8") as f:
        outline = json.load(f)

    image_specs = outline.get("image_specs", []) or []
    if not image_specs:
        print("No image_specs in outline -- nothing to generate.")
        return

    if len(image_specs) > MAX_IMAGES:
        print(
            f"generate_images.py: {len(image_specs)} image_specs exceeds the cap of "
            f"{MAX_IMAGES}; trim the outline.",
            file=sys.stderr,
        )
        sys.exit(1)

    os.makedirs(args.outdir, exist_ok=True)

    # Resolve credentials once, before any work, so a missing key fails fast
    # and cleanly rather than after some images have already cost quota.
    provider = args.provider
    if provider == "auto":
        provider = "cloudflare" if os.environ.get("CLOUDFLARE_API_TOKEN") else "gemini"

    try:
        if provider == "cloudflare":
            cf_account = get_env("CLOUDFLARE_ACCOUNT_ID")
            cf_token = get_env("CLOUDFLARE_API_TOKEN")
            api_key = None
            model = args.model or CF_DEFAULT_MODEL
        else:
            api_key = get_env("GEMINI_API_KEY")
            cf_account = cf_token = None
            model = args.model or DEFAULT_MODEL
    except RuntimeError as exc:
        print(f"generate_images.py failed: {exc}", file=sys.stderr)
        print(
            "IMAGES_UNAVAILABLE: set Cloudflare Workers AI credentials, or proceed "
            "with a chart-only newsletter (see workflows/newsletter.md).",
            file=sys.stderr,
        )
        sys.exit(EXIT_IMAGES_UNAVAILABLE)

    print(f"Provider: {provider} ({model})")

    generated, cached = 0, 0
    for spec in image_specs:
        spec_id = spec["id"]
        prompt = build_prompt(spec)
        digest = prompt_hash(prompt, model)

        hash_path = os.path.join(args.outdir, f"{spec_id}.prompt.sha256")
        png_path = os.path.join(args.outdir, f"{spec_id}.png")

        if not args.force and os.path.exists(png_path) and os.path.exists(hash_path):
            with open(hash_path, "r", encoding="utf-8") as f:
                if f.read().strip() == digest:
                    print(f"Cached  {png_path} (prompt unchanged)")
                    cached += 1
                    continue

        try:
            if provider == "cloudflare":
                raw = call_cloudflare(prompt, model, cf_account, cf_token)
            else:
                raw = call_gemini(prompt, model, api_key)
        except Exception as exc:
            print(f"generate_images.py failed on {spec_id}: {exc}", file=sys.stderr)
            print(
                "IMAGES_UNAVAILABLE: retry later, or proceed with a chart-only "
                "newsletter (see workflows/newsletter.md).",
                file=sys.stderr,
            )
            sys.exit(EXIT_IMAGES_UNAVAILABLE)

        png_path, jpg_path = write_derivatives(
            raw, args.outdir, spec_id, spec.get("placement", "section")
        )
        with open(hash_path, "w", encoding="utf-8") as f:
            f.write(digest)
        print(f"Wrote   {png_path} + {os.path.basename(jpg_path)}")
        generated += 1

    print(f"Done: {generated} generated, {cached} cached.")


if __name__ == "__main__":
    main()
