# Newsletter Automation

## Objective

Given a topic, research it, write it up, illustrate it, style it as an HTML
email, publish a web archive copy, and deliver it -- with a human review gate
before anything is sent, since email is not reversible.

## Required inputs

- **Topic** (from the user's request).
- Optional: tone/length guidance, research recency window (`day`/`week`/
  `month`/`year`), art direction notes.

**Example delivery mode is send-to-self (or dry-run).** The recipient-list machinery
(`config/recipients.txt`, `send_gmail.py --confirm`) still exists and works, but
this workflow does not route to it. Reinstating a real list send means
populating `config/recipients.txt` first and re-reading the compliance gaps in
"Known gaps" below.

## Preflight checks

1. `GEMINI_API_KEY` in `.env`. **Image generation requires billing enabled --
   it is NOT in the Gemini free tier** (verified 2026-09-19; see Learnings). A
   free key yields 429 on every image model. Without image access the pipeline
   still produces a complete chart-only issue.
2. `PERPLEXITY_API_KEY` in `.env` -- missing key means the WebSearch fallback
   (step 2b) instead of the API. Not a blocker.
3. `credentials.json` at the repo root -- only needed at the send step. Preview
   and archive work without it.
4. `EXPECTED_SENDER` in `.env` -- the Google account this project must send
   as (for example `you@your-domain.example`). Sends refuse if the token
   authenticates anyone else.
5. If `token.json` is missing, the first real `send_gmail.py` call opens a
   browser consent window. Because the OAuth app sits in Testing mode, Google
   shows an "unverified app" warning -- Advanced -> Go to <project> (unsafe) ->
   Allow. Expected: Testing mode is what skips Google's verification review.

## Manual setup (one-time, done by the user)

- **Gemini**: aistudio.google.com -> Get API key -> paste into `.env` as
  `GEMINI_API_KEY`. The key is free, but **image generation needs billing
  enabled** at https://ai.dev/rate-limit -- a free-tier key returns 429 on every
  image model. Alternatives if you want illustrations at zero cost: Cloudflare
  Workers AI (FLUX.1-schnell, 10k neurons/day free), or generate images by hand
  and drop them into `.tmp/images/<slug>/` as `<id>.png` + `<id>.email.jpg`.
- **Perplexity**: perplexity.ai -> Account -> API -> `.env` as
  `PERPLEXITY_API_KEY`. Check Perplexity's current pricing before use; without
  a key the workflow falls back to manual research (step 2b).
- **Gmail API**: Google Cloud Console -> new project -> enable Gmail API ->
  OAuth consent screen as External + Testing (add the user's own Gmail as a test
  user, which avoids Google's app-verification process for a single-user tool)
  -> create an OAuth Client ID of type Desktop app -> download as
  `credentials.json` at the repo root.
- `pip install -r requirements.txt`.

## The division of labour that keeps this credible

**AI-generated images never carry data. Charts never come from an image model.**

`generate_images.py` appends a hard "no text, no numbers, no charts" rule to
every prompt, because image models are unreliable at rendering precise text --
Google and OpenAI both say so in their own docs. An AI-drawn chart looks
authoritative while being completely unaudited, and one wrong figure in a
sourced newsletter costs more than the illustration is worth.

Every number the reader sees comes from `generate_charts.py`, plotted from
`chart_specs` values that trace back to the research. Keep it that way.

## Tool sequence

Run tools from the repo root with the venv python (`.venv/bin/python`).

**1. Slug the run.** `slug = slugify(topic)` from `tools/common.py`, so repeated
runs on the same topic never clobber each other.

**2a. Research (primary).**
```
.venv/bin/python tools/research_perplexity.py --topic "<topic>" \
    --output .tmp/research_<slug>.json [--preset medium] [--recency month]
```

**2b. Research (fallback).** If step 2a exits with **code 3**
(`RESEARCH_UNAVAILABLE` -- no key, or the API failed), do the research yourself
with WebSearch/WebFetch and hand-write `.tmp/research_<slug>.json` in the
identical schema:
```json
{"topic": "...", "source": "agent_websearch", "queried_at": "<iso8601>",
 "content": "<thorough factual synthesis>",
 "citations": [{"title": "...", "url": "...", "snippet": "..."}]}
```
Everything downstream reads only this file, so both paths are interchangeable.
Any other non-zero exit is a real bug -- fix the tool, don't paper over it.

**3. Validate the research.**
```
.venv/bin/python tools/validate.py --research .tmp/research_<slug>.json
```
If it reports thin content or zero citations, surface that to the user and ask
whether to broaden, retry, or proceed -- don't silently ship a thin issue.

**4. Write the outline** (this is the reasoning step -- no tool call). Write
`.tmp/outline_<slug>.json`:
```json
{
  "topic": "...", "subject": "...", "preheader": "...", "intro": "...",
  "sections": [
    {"heading": "...", "body_html": "...", "chart_ref": "chart_1", "image_ref": "img_1"}
  ],
  "chart_specs": [
    {"id": "chart_1", "type": "bar", "title": "...", "labels": [...], "values": [...]}
  ],
  "image_specs": [
    {"id": "img_hero", "placement": "hero", "prompt": "...", "alt": "..."},
    {"id": "img_1", "placement": "section", "prompt": "...", "alt": "..."}
  ],
  "sources": [{"title": "...", "url": "..."}]
}
```
- `chart_specs[].type` is one of `bar`, `line`, `donut`, `stat`.
- Only include a chart where the research genuinely supports one. A forced chart
  is worse than no chart.
- `image_specs`: at most 4, at most one `hero`. Write prompts describing a
  *scene or concept*, never a diagram -- the tool will refuse text anyway, and
  asking for a chart just wastes a generation.
- `alt` is required on every image: most email clients block images by default,
  so alt text is what a large share of readers actually get.
- Carry the research citations through into `sources`. Easy to drop during
  synthesis, and it's what makes the issue credible.

**5. Validate the outline.**
```
.venv/bin/python tools/validate.py --outline .tmp/outline_<slug>.json
```

**6. Charts and illustrations.**
```
.venv/bin/python tools/generate_charts.py --outline .tmp/outline_<slug>.json \
    --outdir .tmp/charts/<slug>/
.venv/bin/python tools/generate_images.py --outline .tmp/outline_<slug>.json \
    --outdir .tmp/images/<slug>/
```
`generate_images.py` caches by prompt hash, so re-running after editing one
section won't redraw everything. Exit code 3 means images are unavailable --
tell the user and offer to continue chart-only rather than stopping the run.

**7. Build the archive page.**
```
.venv/bin/python tools/build_archive.py --outline .tmp/outline_<slug>.json \
    --charts-dir .tmp/charts/<slug>/ --images-dir .tmp/images/<slug>/ \
    --out .tmp/archive_<slug>.html
```

**8. Publish the archive (optional, host-specific).** `build_archive.py` writes a
self-contained HTML file. The original author published it with the Claude
Artifact tool; if you host it elsewhere (static hosting, S3, etc.), skip the
Artifact wording below and record the URL with `archive_index.py --set`.
Check for an existing URL first:
```
.venv/bin/python tools/archive_index.py --get <slug>
```
- **If a URL comes back**: publish with the Artifact tool passing that `url`, so
  the issue updates in place. Re-read it first, as the Artifact tool requires.
- **If nothing comes back**: publish fresh with a favicon, then record it:
```
.venv/bin/python tools/archive_index.py --set <slug> --url "<url>" --subject "<subject>"
```
Never let a republished issue land on a new URL -- the old link is already
sitting in a sent email.

**9. Render the email preview.**
```
.venv/bin/python tools/render_email.py --outline .tmp/outline_<slug>.json \
    --charts-dir .tmp/charts/<slug>/ --images-dir .tmp/images/<slug>/ \
    --mode preview --archive-url "<url>" --out .tmp/preview_<slug>.html
```
Give the user the path; they can `open .tmp/preview_<slug>.html`.

**10. Human review gate** -- see below. No send without a fresh go-ahead.

**11. Render final + send to self.**
```
.venv/bin/python tools/render_email.py ... --mode final --out .tmp/final_<slug>.html
.venv/bin/python tools/send_gmail.py --html .tmp/final_<slug>.html \
    --charts-dir .tmp/charts/<slug>/ --images-dir .tmp/images/<slug>/ \
    --subject "<subject>" --slug <slug> --send-to-self
```
Ask the user to check their inbox: a browser preview can't catch what Gmail's
renderer does differently.

**12. Report back**: Gmail message id, the archive URL, and anything that fell
back (WebSearch research, missing illustrations).

## The human review gate (step 10)

Present:
- The subject line and preheader.
- Which sections carry charts, and which carry illustrations.
- The archive URL and the local preview path.
- Anything that degraded: fallback research, skipped images, thin citations.

Then let them choose: send the self-test, request edits (loop back to step 4),
or abort.

`send_gmail.py --confirm` (the real list send) requires an affirmative
instruction given fresh **in that session**. A standing "you can send
newsletters" from an earlier conversation is not sufficient.

## Decision points / edge cases

- **Research API exits 3**: use the WebSearch fallback (step 2b). Mention it in
  the final report -- the user should know the issue wasn't Perplexity-grounded.
- **Image API exits 3**: quota (free tier resets midnight Pacific), bad key, or
  network. Offer a chart-only issue rather than blocking.
- **Gemini returns an unexpected response shape**: `_extract_image_bytes` walks
  four known envelopes and raises with the top-level keys if none match. Add the
  new shape to that function and record it in Learnings.
- **Chart image missing**: templates skip a chart whose PNG is absent, and every
  `<img>` carries alt text.
- **Re-running the same topic**: charts and images regenerate into the same
  slug directories (images short-circuit on the prompt hash); the archive
  updates in place via `archive_index.py`.
- **OAuth refresh fails** (`invalid_grant`): delete `token.json` and re-run to
  retrigger browser consent.
- **Recipient list over the threshold** (400, `MAX_RECIPIENTS` in
  `send_gmail.py`): refuses rather than partial-sending. Not reachable in
  send-to-self mode.

## Known gaps (acceptable for personal use, not fixed here)

- No unsubscribe / CAN-SPAM / GDPR handling. Fine while this only goes to the
  author; **required work before it reaches anyone who hasn't opted in.**
- No deliverability tooling, bounce handling, or engagement tracking. Personal
  Gmail is not a substitute for an ESP at volume.
- The email has no dark-mode treatment: Gmail and Apple Mail may invert the
  light card while chart PNGs stay light. Deferred deliberately.
- No fact-check gate tying every charted number back to a specific citation --
  the AI-art/chart split limits the blast radius, but doesn't verify the figures
  the agent extracted.
- The plaintext MIME part is a basic HTML-strip fallback, not hand-tuned.

## Development notes (from the original author; not re-verified for this template)

- **2026-09-10 -- Sonar chat-completions is being retired (2026-09-27).**
  `research_perplexity.py` was calling `api.perplexity.ai/chat/completions` with
  `model: sonar-pro`. Migrated to the Agent API at `/v1/agent`:
  `model` -> `preset`, `messages[]` -> `input` string, system message ->
  top-level `instructions`. The response is an `output[]` array of *typed*
  items, so text now comes from walking for `type == "message"` rather than
  indexing `choices[0]`.
- **2026-09-10 -- `search_recency_filter` doesn't exist on the Agent API.**
  Recency is steered through the prompt instead; `--recency` appends a sentence
  to the input. Not yet confirmed against a live call -- verify and update this
  line once a real request has been made.
- **2026-09-10 -- sources used to render as bare URLs.** Sonar's `citations`
  array was plain strings, so every source title was just its URL. The Agent
  API's `search_results[]` carries real titles; `_extract_sources` prefers those
  and only falls back to the URL.
- **2026-09-10 -- email clients ignore `object-fit`/`aspect-ratio`.** Gemini
  returns square images, which became a 536px-tall slab in a 600px email.
  `generate_images.py` now center-crops the *email* derivative in Pillow (16:9
  hero, 3:2 section) while the archive keeps the uncropped original and crops
  via CSS.
- **2026-09-10 -- Artifacts cannot load external images.** The archive page must
  embed every chart and illustration as a base64 `data:` URI or it publishes
  with broken images. `build_archive.py` does this; don't "optimize" it into
  file references.
- **2026-09-19 -- Gemini image generation is NOT on the free tier.** This was
  planned on a secondary blog's claim of "~500 images/day free". That is wrong.
  Verified against a real free-tier key: all six image models
  (`gemini-2.5-flash-image`, `gemini-3.1-flash-image`, `-lite-image`,
  `gemini-3-pro-image`, and the previews) return 429 "exceeded your current
  quota" on the first call, and the preview model states the limit outright:
  *"limit: 0 requests per day on Free Tier."* The key itself is fine -- Gemma
  text models return 200 on the same key. **A 429 here is a billing state, not
  a transient quota; retrying never helps.** Don't trust aggregator blogs for
  free-tier claims -- probe `/v1beta/models` and make one real call.
- **2026-09-19 -- the image endpoint was wrong.** `generate_images.py` was
  calling `/v1beta/interactions`, which routes but silently maps to
  `gemini-2.5-flash-preview-image`. The image models are served from
  `/v1beta/models/<model>:generateContent` with a `contents[].parts[]` payload.
  Fixed. Also note Gemini text models are now gated: `gemini-2.5-flash` and
  `-pro` return 404 "no longer available to new users" on a new key.
- **2026-09-19 -- `stat` charts clipped long captions.** `render_stat` drew the
  caption as a single unwrapped line, so anything past ~52 characters ran off
  the right edge of the PNG (caught visually, not by any assertion -- the tool
  exited 0 and wrote a valid file). It now wraps title and caption with
  `textwrap`, grows the figure to fit the extra lines, and saves with
  `bbox_inches="tight"`. Worth remembering that chart tools fail silently this
  way: always look at a rendered `stat` chart before shipping it.
- **Gmail sending identity (design note).** `send_gmail.py` requests the
  `gmail.send` scope plus `openid` and `userinfo.email`. That combination can
  send mail and learn which account the token belongs to, without any mailbox
  read access (`users.getProfile()` would need a read scope, so it is not
  used). `assert_expected_sender()` refuses to send unless the authenticated
  account matches `EXPECTED_SENDER`. Changing scopes invalidates an existing
  `token.json`: delete it and re-consent. `--send-to ADDRESS` sends to one
  explicit address and omits the `From` header so Gmail stamps the real sender.
- **2026-09-19 -- Cloudflare Workers AI works where Gemini did not.**
  `generate_images.py` now takes `--provider auto|cloudflare|gemini` (auto picks
  Cloudflare when `CLOUDFLARE_API_TOKEN` is set). Endpoint:
  `POST /client/v4/accounts/<id>/ai/run/@cf/black-forest-labs/flux-1-schnell`,
  base64 JPEG returned at `result.image`. Free allocation is 10,000 neurons/day
  and a 1024x1024 image costs ~19, so a 3-image issue is ~58 -- about 170
  issues/day of headroom. On the Workers Free plan, exceeding it errors rather
  than billing.
- **2026-09-19 -- do NOT enumerate forbidden things in an image prompt.**
  After a measuring-cylinder prompt produced garbled graduation numerals
  ("1I3", "8D"), NO_TEXT_RULE was expanded to forbid "rulers, dials, gauges,
  numbered increments" -- and FLUX immediately drew a numbered ruler across the
  hero. Naming a concept in a positive prompt RAISES its likelihood, and this
  API exposes no negative-prompt field. The rule is now short and positive
  ("every surface is completely blank, smooth and unmarked"), which works.
  The durable lesson: **steer by describing what you want, and pick subjects
  that have no affordance for text at all.** Measuring vessels, rulers, signage
  and dashboards all invite lettering; plain columns and curtains do not.
- **2026-09-19 -- check generated art for unintended connotation, not just
  text.** A "marionette control cross" prompt produced three hanging shapes
  that read unmistakably as religious crosses. Nothing automated catches this;
  look at every hero before shipping.
