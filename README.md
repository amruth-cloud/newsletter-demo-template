# Newsletter Automation Template

A set of small Python tools and a written workflow that turn a topic into a sourced, chart-backed HTML newsletter. It also builds a standalone web-archive page and a MIME email you can dry-run locally. Sending is gated behind explicit flags.

Use it as a starting point: fork it, edit the templates, and swap in your own research and delivery steps.

## What it does

1. Researches a topic (Perplexity API, or notes you write by hand).
2. Validates the research and a hand-written (or AI-assisted) outline JSON.
3. Draws charts locally from the outline's numbers (matplotlib).
4. Optionally generates illustrations (Cloudflare Workers AI or Gemini).
5. Renders an email-safe HTML issue and a self-contained archive page.
6. Builds the email as a dry-run `.eml` file, or sends it through the Gmail API.

Design rule: **AI-generated images never carry data.** Every number a reader sees is plotted from the outline by `generate_charts.py`.

## Pipeline

```
 topic
   |
   v
[research]  tools/research_perplexity.py  ->  .tmp/research_<slug>.json
   |                (exit 3 -> write the same JSON by hand)
   v
[validate]  tools/validate.py --research
   |
   v
[outline]   write .tmp/outline_<slug>.json  (see examples/sample_outline.json)
   |
   v
[validate]  tools/validate.py --outline
   |
   +--> [charts]   tools/generate_charts.py  -> .tmp/charts/<slug>/*.png
   +--> [images]   tools/generate_images.py  -> .tmp/images/<slug>/   (optional)
   |
   v
[archive page]   tools/build_archive.py   -> .tmp/archive_<slug>.html
[email preview]  tools/render_email.py --mode preview
[email final]    tools/render_email.py --mode final
   |
   v
[human review]  you read the preview
   |
   v
[dry-run email] tools/send_gmail.py --dry-run  -> .tmp/dryrun_<slug>.eml
                (--send-to-self / --send-to / --confirm exist; see Limits)
```

The full step-by-step is in [workflows/newsletter.md](workflows/newsletter.md). It was written as instructions for an AI coding agent, but a person can follow it too.

## Tools used

| Tool | Purpose | Needs |
|---|---|---|
| Python 3 | all scripts | `pip install -r requirements.txt` |
| matplotlib | charts | none |
| Jinja2 + premailer | email and archive HTML | none |
| Perplexity Agent API | research (configured for) | `PERPLEXITY_API_KEY` |
| Cloudflare Workers AI (FLUX.1-schnell) | illustrations (configured for) | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` |
| Gemini API | illustrations, alternative (configured for) | `GEMINI_API_KEY` |
| Gmail API (OAuth desktop client) | sending (configured for) | `credentials.json`, `EXPECTED_SENDER` |

Check each provider's current pricing and free-tier terms yourself before running anything live.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in only what you need
```

Try the whole local pipeline with the fake sample data. No keys and no network are needed:

```bash
PY=.venv/bin/python
$PY tools/validate.py --research examples/sample_research.json
$PY tools/validate.py --outline  examples/sample_outline.json
$PY tools/generate_charts.py --outline examples/sample_outline.json --outdir .tmp/charts/sample/
$PY tools/build_archive.py  --outline examples/sample_outline.json \
    --charts-dir .tmp/charts/sample/ --images-dir .tmp/images/sample/ --out .tmp/archive_sample.html
$PY tools/render_email.py --outline examples/sample_outline.json \
    --charts-dir .tmp/charts/sample/ --images-dir .tmp/images/sample/ --mode preview --out .tmp/preview_sample.html
$PY tools/render_email.py --outline examples/sample_outline.json \
    --charts-dir .tmp/charts/sample/ --images-dir .tmp/images/sample/ --mode final --out .tmp/final_sample.html
$PY tools/send_gmail.py --html .tmp/final_sample.html --charts-dir .tmp/charts/sample/ \
    --images-dir .tmp/images/sample/ --subject "Sample Issue" --slug sample --dry-run
```

Open `.tmp/preview_sample.html` in a browser. The dry-run writes `.tmp/dryrun_sample.eml`.

The sample data is invented: every company, source and number in `examples/` is fictional.

## Customising

- **Look and feel:** `templates/newsletter.css`, `templates/newsletter_base.html.j2`, `templates/archive.html.j2`.
- **Footer text:** the footer in `newsletter_base.html.j2` is a placeholder sentence. Replace it with your own wording and a real unsubscribe mechanism before mailing anyone who has not opted in.
- **Research instructions:** the `INSTRUCTIONS` string in `tools/research_perplexity.py` is a generic example prompt. Edit it for your topic area.
- **Recipients:** copy `config/recipients.example.txt` to `config/recipients.txt` (git-ignored).

## Limits

Only facts verified while preparing this template are listed.

- **Verified offline, on fake data:** syntax check of every script, research and outline validation, chart rendering (donut, stat, line, bar), archive-page build, email preview and final render, and a dry-run `.eml` build.
- **Not exercised for this template:** live Perplexity calls, live image generation, Gmail OAuth, and any real send. These are "configured for", and live use of each is unconfirmed here.
- **Model IDs** (`gemini-3.1-flash-image`, `@cf/black-forest-labs/flux-1-schnell`) are defaults in the code and were not re-checked against the providers. Override with `--model` if they change. The code does not call any Claude model.
- **Dry-run emails only:** this repo contains no evidence of a delivered email. The `--send-to-self`, `--send-to` and `--confirm` modes exist in code but are untested here.
- **No full-list sends:** nothing here shows a send to a recipient list.
- **No production runs:** nothing here shows scheduled or recurring operation.
- **No measured time saved:** no timing data was collected.
- **No compliance features:** there is no unsubscribe link, bounce handling or engagement tracking. The recipient cap in the code is 400.
- **No fact-check gate:** the tools do not verify that a charted number matches a cited source. Review every figure yourself.
- **Archive publishing is host-specific:** `build_archive.py` makes a standalone HTML file. The workflow's step 8 describes publishing it with the Claude Artifact tool, which only applies if you use that environment.
- **The outline step has no script.** In the workflow an AI agent (or you) writes the outline JSON by hand and `validate.py` checks its shape.
- **No license is included.** Add one before reuse.
