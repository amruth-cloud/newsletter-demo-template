"""Send the rendered newsletter via the Gmail API (OAuth, user's own Gmail).

Three modes, matching the workflow's human-in-the-loop send gates:
  --dry-run       (default) Build the MIME message and write it to
                  .tmp/dryrun_<slug>.eml. No API call, no OAuth required.
                  Open the .eml in Mail.app (or similar) to sanity-check it.
  --send-to-self  Real Gmail API call, but the recipient is forcibly
                  overridden to the authenticated account's own address --
                  validates real-inbox rendering with zero risk to the list.
  --confirm       The real send to the full --recipients-file list. Requires
                  this explicit flag as a second guard beyond whatever
                  confirmation the workflow already gathered from the user.

Recipients go in Bcc (a single To: yourself + Bcc: everyone), so one API
call handles the whole list without exposing addresses to each other.
Refuses (rather than partial-sending) above a conservative safety threshold,
comfortably under Gmail's ~500/day cap for a regular account.

Only --confirm sends are appended to sent/log.jsonl and snapshotted into
sent/<slug>_<timestamp>/ -- that directory is the persistent record of real
sends to real people (see workflows/newsletter.md for why it's not in .tmp/).

CLI:
    python tools/send_gmail.py --html .tmp/final_<slug>.html \
        --charts-dir .tmp/charts/<slug>/ --subject "..." \
        [--dry-run | --send-to-self | --confirm --recipients-file config/recipients.txt]
"""

import argparse
import base64
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from common import REPO_ROOT, load_recipients, slugify

# gmail.send lets us send and nothing else -- it cannot read the mailbox.
# openid + userinfo.email add ONLY the account's own address, so the tool can
# state which account it is sending as instead of leaving it to inference.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

# Google frequently echoes scopes back in a different order or adds its own,
# which oauthlib otherwise treats as a scope-change error mid-flow.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
CREDENTIALS_PATH = os.path.join(REPO_ROOT, "credentials.json")
TOKEN_PATH = os.path.join(REPO_ROOT, "token.json")
SENT_DIR = os.path.join(REPO_ROOT, "sent")
MAX_RECIPIENTS = 400


def get_credentials() -> Credentials:
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            return creds
        except Exception as exc:
            raise RuntimeError(
                "Gmail token refresh failed (often invalid_grant). "
                "Delete token.json and re-run to re-authenticate via browser."
            ) from exc

    if not os.path.exists(CREDENTIALS_PATH):
        raise RuntimeError(
            "credentials.json not found at repo root. Create a Google Cloud OAuth "
            "Desktop client and download it there first -- see workflows/newsletter.md "
            "for the setup steps."
        )
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    return creds


def resolve_identity(creds) -> str:
    """Ask Google which account this token actually belongs to.

    Uses the oauth2 userinfo endpoint, which the openid/userinfo.email scopes
    grant. Deliberately NOT users.getProfile(), which needs a Gmail *read*
    scope -- reading an entire mailbox to learn one address is a bad trade.
    """
    try:
        info = build("oauth2", "v2", credentials=creds).userinfo().get().execute()
        email = (info.get("email") or "").strip()
        if email:
            return email
    except Exception as exc:
        raise RuntimeError(
            "Could not read the authenticated account address. The token may predate "
            "the openid/userinfo.email scopes -- delete token.json and re-run to "
            "re-consent."
        ) from exc
    raise RuntimeError("Google returned no email for this token.")


def assert_expected_sender(identity: str) -> None:
    """Refuse to send if the token belongs to an account we did not expect.

    Guards the failure this tool already hit once: silently authenticating as
    the wrong Google account and mailing from the wrong domain. Set
    EXPECTED_SENDER in .env to arm it; unset means no check.
    """
    expected = os.environ.get("EXPECTED_SENDER", "").strip()
    if not expected:
        print("WARNING: EXPECTED_SENDER is not set in .env -- sending as "
              f"{identity} without verification.", file=sys.stderr)
        return
    if identity.lower() != expected.lower():
        raise RuntimeError(
            f"REFUSING TO SEND.\n"
            f"  token authenticates : {identity}\n"
            f"  .env expects        : {expected}\n"
            f"Delete token.json and re-consent with the correct Google account, "
            f"or update EXPECTED_SENDER if the change is intentional."
        )


def get_verified_service():
    """Build the Gmail service and prove who it sends as, before any send."""
    creds = get_credentials()
    identity = resolve_identity(creds)
    assert_expected_sender(identity)
    print(f"Authenticated as: {identity}", file=sys.stderr)
    return build("gmail", "v1", credentials=creds), identity


def html_to_plaintext(html: str) -> str:
    text = re.sub(r"(?is)<head[^>]*>.*?</head>", "", html)
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    cleaned = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank:
                cleaned.append("")
            prev_blank = True
        else:
            cleaned.append(line)
            prev_blank = False
    return "\n".join(cleaned).strip()


def _attach_inline(msg: MIMEMultipart, directory: str, suffix: str) -> int:
    """CID-embed every file in `directory` ending with `suffix`.

    The Content-ID is the filename with the suffix stripped, which is what makes
    it line up with the cid: references render_email.py emits -- so an image
    written as img_hero.email.jpg is referenced as cid:img_hero.
    """
    if not directory or not os.path.isdir(directory):
        return 0
    count = 0
    for fname in sorted(os.listdir(directory)):
        if not fname.lower().endswith(suffix):
            continue
        asset_id = fname[: -len(suffix)]
        with open(os.path.join(directory, fname), "rb") as img_f:
            img = MIMEImage(img_f.read())
        img.add_header("Content-ID", f"<{asset_id}>")
        img.add_header("Content-Disposition", "inline", filename=fname)
        msg.attach(img)
        count += 1
    return count


def build_message(html_path: str, charts_dir: str, subject: str, from_display: str,
                   to_addr: str, bcc_addrs: list, images_dir: str = None) -> MIMEMultipart:
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    # Gmail rewrites From to the authenticated account anyway -- setting an
    # address you don't own is at best ignored. Omit it and let Gmail fill in.
    if from_display:
        msg["From"] = from_display
    msg["To"] = to_addr
    if bcc_addrs:
        msg["Bcc"] = ", ".join(bcc_addrs)

    alt = MIMEMultipart("alternative")
    msg.attach(alt)
    alt.attach(MIMEText(html_to_plaintext(html), "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))

    _attach_inline(msg, charts_dir, ".png")
    # Only the downscaled derivative ships; the full-res PNG belongs to the
    # archive page, and attaching both would double the message weight.
    _attach_inline(msg, images_dir, ".email.jpg")

    return msg


def _send_raw(service, msg: MIMEMultipart) -> dict:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()


def dry_run(html_path, charts_dir, subject, recipients, sender_name, slug, images_dir=None) -> dict:
    from_display = f"{sender_name} <your-authenticated-gmail-address>" if sender_name else "<your-authenticated-gmail-address>"
    to_addr = "<your-authenticated-gmail-address>"
    msg = build_message(html_path, charts_dir, subject, from_display, to_addr, recipients,
                        images_dir=images_dir)

    outpath = os.path.join(REPO_ROOT, ".tmp", f"dryrun_{slug}.eml")
    with open(outpath, "wb") as f:
        f.write(msg.as_bytes())

    return {
        "mode": "dry_run",
        "eml_path": outpath,
        "recipient_count": len(recipients),
        "note": "No API call made. Open the .eml file to inspect the MIME structure.",
    }


def send_to_self(html_path, charts_dir, subject, sender_name, images_dir=None) -> dict:
    service, own_email = get_verified_service()
    from_display = f"{sender_name} <{own_email}>" if sender_name else own_email
    msg = build_message(html_path, charts_dir, subject, from_display, own_email, [],
                        images_dir=images_dir)
    result = _send_raw(service, msg)
    return {
        "mode": "send_to_self",
        "sent_from": own_email,
        "sent_to": own_email,
        "gmail_message_id": result.get("id"),
    }


def send_to_address(html_path, charts_dir, subject, sender_name, to_addr, images_dir=None) -> dict:
    """Send to one explicitly named recipient.

    Separate from --send-to-self because the recipient is NOT the authenticated
    account: From is left unset so Gmail stamps the real sending account rather
    than an address the token does not own.
    """
    service, identity = get_verified_service()
    msg = build_message(html_path, charts_dir, subject, None, to_addr, [],
                        images_dir=images_dir)
    result = _send_raw(service, msg)
    return {
        "mode": "send_to_address",
        "sent_from": identity,
        "sent_to": to_addr,
        "gmail_message_id": result.get("id"),
    }


def send_confirm(html_path, charts_dir, subject, recipients, sender_name, slug, images_dir=None) -> dict:
    if not recipients:
        raise RuntimeError("No recipients configured. Populate config/recipients.txt before a live send.")
    if len(recipients) > MAX_RECIPIENTS:
        raise RuntimeError(
            f"{len(recipients)} recipients exceeds the safety threshold of {MAX_RECIPIENTS}. "
            f"Refusing to send rather than partial-send."
        )

    service, own_email = get_verified_service()
    from_display = f"{sender_name} <{own_email}>" if sender_name else own_email
    msg = build_message(html_path, charts_dir, subject, from_display, own_email, recipients,
                        images_dir=images_dir)
    result = _send_raw(service, msg)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_entry = {
        "slug": slug,
        "timestamp": timestamp,
        "subject": subject,
        "recipient_count": len(recipients),
        "gmail_message_id": result.get("id"),
    }
    os.makedirs(SENT_DIR, exist_ok=True)
    with open(os.path.join(SENT_DIR, "log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

    snapshot_dir = os.path.join(SENT_DIR, f"{slug}_{timestamp}")
    os.makedirs(snapshot_dir, exist_ok=True)
    shutil.copy2(html_path, os.path.join(snapshot_dir, "final.html"))
    if charts_dir and os.path.isdir(charts_dir):
        shutil.copytree(charts_dir, os.path.join(snapshot_dir, "charts"), dirs_exist_ok=True)
    if images_dir and os.path.isdir(images_dir):
        shutil.copytree(images_dir, os.path.join(snapshot_dir, "images"), dirs_exist_ok=True)

    return {
        "mode": "confirm",
        "recipient_count": len(recipients),
        "gmail_message_id": result.get("id"),
        "snapshot_dir": snapshot_dir,
    }


def main():
    parser = argparse.ArgumentParser(description="Send the rendered newsletter via Gmail API.")
    parser.add_argument("--html", required=True)
    parser.add_argument("--charts-dir", default=None)
    parser.add_argument("--images-dir", default=None)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--recipients-file", default=None)
    parser.add_argument("--slug", default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--send-to-self", action="store_true")
    mode.add_argument("--send-to", metavar="ADDRESS",
                      help="Send to one explicit address (not the authenticated account).")
    mode.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    slug = args.slug or slugify(args.subject)
    sender_name = os.environ.get("GMAIL_SENDER_NAME", "")

    try:
        if args.confirm:
            if not args.recipients_file:
                raise RuntimeError("--confirm requires --recipients-file")
            recipients = load_recipients(args.recipients_file)
            result = send_confirm(args.html, args.charts_dir, args.subject, recipients, sender_name, slug,
                                  images_dir=args.images_dir)
        elif args.send_to:
            result = send_to_address(args.html, args.charts_dir, args.subject, sender_name,
                                     args.send_to, images_dir=args.images_dir)
        elif args.send_to_self:
            result = send_to_self(args.html, args.charts_dir, args.subject, sender_name,
                                  images_dir=args.images_dir)
        else:
            recipients = load_recipients(args.recipients_file) if args.recipients_file else []
            result = dry_run(args.html, args.charts_dir, args.subject, recipients, sender_name, slug,
                             images_dir=args.images_dir)
    except Exception as exc:
        print(f"send_gmail.py failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
