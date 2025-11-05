#!/usr/bin/env python3
"""Convert Gmail replies to RSS alerts into Reddit comments."""
import imaplib
import os
import re
import sys
from email import message_from_bytes
from email.header import decode_header, make_header

import praw

# --- Helpers ---------------------------------------------------------------


def env_str(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name)
    if val is None:
        return default
    val = val.strip()
    return val or default


def require(name: str, value: str | None) -> str:
    if not value:
        raise SystemExit(f"Missing required env/secret: {name}")
    return value


def parse_header(raw_value: str | None) -> str:
    if not raw_value:
        return ""
    try:
        return str(make_header(decode_header(raw_value)))
    except Exception:
        return raw_value


def extract_plain_text(email_message) -> str:
    if email_message.is_multipart():
        for part in email_message.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace")
                except LookupError:
                    return payload.decode("utf-8", errors="replace")
    else:
        if email_message.get_content_type() == "text/plain":
            payload = email_message.get_payload(decode=True)
            if payload is not None:
                charset = email_message.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace")
                except LookupError:
                    return payload.decode("utf-8", errors="replace")
    return ""


def trim_reply_body(body: str) -> str:
    """Remove quoted original messages (best effort)."""
    lines = body.splitlines()
    trimmed: list[str] = []
    for line in lines:
        if line.startswith(">"):
            break
        if line.startswith("On ") and line.rstrip().endswith("wrote"):
            break
        if line.startswith("On ") and " wrote:" in line:
            break
        if line.startswith("From: "):
            break
        trimmed.append(line)
    return "\n".join(trimmed).strip()


REDDIT_LINK_RE = re.compile(
    r"https?://www\.reddit\.com/r/[A-Za-z0-9_]+/comments/[0-9a-z]+/[0-9a-z_%-]+",
    re.IGNORECASE,
)

COMMENT_MAX_LEN = 9_900  # Reddit comments cap at 10k

# --- Config ----------------------------------------------------------------

IMAP_HOST = env_str("GMAIL_IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(env_str("GMAIL_IMAP_PORT", "993"))
IMAP_MAILBOX = env_str("GMAIL_IMAP_MAILBOX", "INBOX")

GMAIL_USER = env_str("GMAIL_IMAP_USER", env_str("GMAIL_USER"))
GMAIL_PASSWORD = env_str("GMAIL_IMAP_PASSWORD", env_str("GMAIL_APP_PASSWORD"))

REDDIT_CLIENT_ID = env_str("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = env_str("REDDIT_CLIENT_SECRET")
REDDIT_USERNAME = env_str("REDDIT_USERNAME")
REDDIT_PASSWORD = env_str("REDDIT_PASSWORD")
REDDIT_USER_AGENT = env_str(
    "REDDIT_USER_AGENT",
    "github.com/jarmeli1/reddit-rss-alerts (Email replies to Reddit)",
)

REPLY_SUBJECT_PREFIX = env_str("EMAIL_REPLY_SUBJECT_PREFIX", "Re: [r/")
POST_SUBJECT_PREFIX = env_str("EMAIL_SUBJECT_PREFIX", "[Reddit]")

for name, value in [
    ("GMAIL_IMAP_USER", GMAIL_USER),
    ("GMAIL_IMAP_PASSWORD", GMAIL_PASSWORD),
    ("REDDIT_CLIENT_ID", REDDIT_CLIENT_ID),
    ("REDDIT_CLIENT_SECRET", REDDIT_CLIENT_SECRET),
    ("REDDIT_USERNAME", REDDIT_USERNAME),
    ("REDDIT_PASSWORD", REDDIT_PASSWORD),
]:
    require(name, value)


def reddit_client() -> praw.Reddit:
    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        username=REDDIT_USERNAME,
        password=REDDIT_PASSWORD,
        user_agent=REDDIT_USER_AGENT,
    )


def connect_imap() -> imaplib.IMAP4_SSL:
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(GMAIL_USER, GMAIL_PASSWORD)
        mail.select(IMAP_MAILBOX)
        return mail
    except imaplib.IMAP4.error as exc:
        raise SystemExit(f"IMAP login/select failed: {exc}") from exc


def main() -> None:
    mail = connect_imap()
    reddit = reddit_client()

    status, data = mail.search(None, "UNSEEN")
    if status != "OK":
        raise SystemExit(f"IMAP search failed: {status}")

    message_ids = data[0].split()
    if not message_ids:
        print("No unread replies to process.")
        mail.close()
        mail.logout()
        return

    commented = 0
    skipped = 0
    deferred_to_posts = 0

    for msg_id in message_ids:
        status, parts = mail.fetch(msg_id, "(BODY.PEEK[])")
        if status != "OK":
            print(f"Failed to fetch message {msg_id!r}: {status}")
            continue

        # BODY.PEEK[] lets us inspect without flipping the \Seen flag prematurely
        raw_email = parts[0][1]
        email_msg = message_from_bytes(raw_email)

        subject = parse_header(email_msg.get("Subject"))
        sender = parse_header(email_msg.get("From"))

        if REPLY_SUBJECT_PREFIX and not subject.startswith(REPLY_SUBJECT_PREFIX):
            if POST_SUBJECT_PREFIX and subject.startswith(POST_SUBJECT_PREFIX):
                print(
                    "Skipping email for reply workflow because subject matches new post "
                    "prefix; leaving unread for post handler."
                )
                deferred_to_posts += 1
                continue
            print(f"Skipping email from {sender!r} – subject missing reply prefix {REPLY_SUBJECT_PREFIX!r}.")
            mail.store(msg_id, "+FLAGS", "(\\Seen)")
            skipped += 1
            continue

        body = extract_plain_text(email_msg)
        body = trim_reply_body(body)
        if not body:
            print(f"Skipping reply '{subject}' – no comment body detected.")
            mail.store(msg_id, "+FLAGS", "(\\Seen)")
            skipped += 1
            continue

        match = REDDIT_LINK_RE.search(email_msg.as_string())
        if not match:
            match = REDDIT_LINK_RE.search(body)
        if not match:
            print(f"Skipping reply '{subject}' – no Reddit permalink found.")
            mail.store(msg_id, "+FLAGS", "(\\Seen)")
            skipped += 1
            continue

        permalink = match.group(0)
        comment_text = body[:COMMENT_MAX_LEN]

        try:
            submission = reddit.submission(url=permalink)
            submission.reply(comment_text)
            print(f"Commented on {permalink} with {len(comment_text)} chars.")
            commented += 1
            mail.store(msg_id, "+FLAGS", "(\\Seen)")
        except Exception as exc:
            print(f"Failed to comment on {permalink}: {exc}", file=sys.stderr)
            # leave unread for manual retry
            continue

    mail.close()
    mail.logout()
    summary = f"Done. Comments posted: {commented}; Skipped: {skipped}"
    if deferred_to_posts:
        summary += f"; Deferred to posts: {deferred_to_posts}"
    print(summary)


if __name__ == "__main__":
    main()
