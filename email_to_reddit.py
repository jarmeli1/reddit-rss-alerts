#!/usr/bin/env python3
"""Polls a Gmail inbox and posts qualified emails to Reddit."""
import imaplib
import os
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


def parse_subject(raw_subject: str | None) -> str:
    if not raw_subject:
        return ""
    try:
        header = make_header(decode_header(raw_subject))
        return str(header)
    except Exception:
        return raw_subject


def extract_plain_text(email_message) -> str:
    """Best-effort extract of the text/plain body."""
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


# --- Configuration --------------------------------------------------------

IMAP_HOST = env_str("GMAIL_IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(env_str("GMAIL_IMAP_PORT", "993"))
IMAP_MAILBOX = env_str("GMAIL_IMAP_MAILBOX", "INBOX")
SUBJECT_PREFIX = env_str("EMAIL_SUBJECT_PREFIX", "[Reddit]")
REPLY_SUBJECT_PREFIX = env_str("EMAIL_REPLY_SUBJECT_PREFIX", "Re: [r/")

GMAIL_USER = env_str("GMAIL_IMAP_USER", env_str("GMAIL_USER"))
GMAIL_PASSWORD = env_str("GMAIL_IMAP_PASSWORD", env_str("GMAIL_APP_PASSWORD"))

REDDIT_CLIENT_ID = env_str("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = env_str("REDDIT_CLIENT_SECRET")
REDDIT_USERNAME = env_str("REDDIT_USERNAME")
REDDIT_PASSWORD = env_str("REDDIT_PASSWORD")
REDDIT_USER_AGENT = env_str(
    "REDDIT_USER_AGENT",
    "github.com/jarmeli1/reddit-rss-alerts (Email to Reddit)",
)
POST_SUBREDDIT = env_str("POST_SUBREDDIT")

# Validate required inputs
for name, value in [
    ("GMAIL_IMAP_USER", GMAIL_USER),
    ("GMAIL_IMAP_PASSWORD", GMAIL_PASSWORD),
    ("REDDIT_CLIENT_ID", REDDIT_CLIENT_ID),
    ("REDDIT_CLIENT_SECRET", REDDIT_CLIENT_SECRET),
    ("REDDIT_USERNAME", REDDIT_USERNAME),
    ("REDDIT_PASSWORD", REDDIT_PASSWORD),
    ("POST_SUBREDDIT", POST_SUBREDDIT),
]:
    require(name, value)

SUBJECT_PREFIX = SUBJECT_PREFIX or ""
MAX_TITLE_LEN = 300  # Reddit limit


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
        print("No unread emails to process.")
        mail.close()
        mail.logout()
        return

    posted = 0
    skipped = 0
    deferred_to_reply = 0

    for msg_id in message_ids:
        status, parts = mail.fetch(msg_id, "(BODY.PEEK[])")
        if status != "OK":
            print(f"Failed to fetch message {msg_id!r}: {status}")
            continue

        # Using BODY.PEEK[] keeps the \Seen flag untouched until we decide
        raw_email = parts[0][1]
        email_msg = message_from_bytes(raw_email)

        subject = parse_subject(email_msg.get("Subject"))
        sender = parse_subject(email_msg.get("From"))

        if SUBJECT_PREFIX and not subject.startswith(SUBJECT_PREFIX):
            if REPLY_SUBJECT_PREFIX and subject.startswith(REPLY_SUBJECT_PREFIX):
                print(
                    "Skipping email for post workflow because subject matches reply prefix; "
                    "leaving unread for reply handler."
                )
                deferred_to_reply += 1
                continue
            print(f"Skipping email from {sender!r} – subject missing prefix {SUBJECT_PREFIX!r}.")
            mail.store(msg_id, "+FLAGS", "(\\Seen)")
            skipped += 1
            continue

        title = subject[len(SUBJECT_PREFIX):].strip() if SUBJECT_PREFIX else subject.strip()
        if not title:
            title = "(untitled email)"

        body = extract_plain_text(email_msg).strip()
        if not body:
            print(f"Skipping email {subject!r}: no text/plain body found.")
            mail.store(msg_id, "+FLAGS", "(\\Seen)")
            skipped += 1
            continue

        try:
            subreddit = reddit.subreddit(POST_SUBREDDIT)
            subreddit.submit(title=title[:MAX_TITLE_LEN], selftext=body)
            print(f"Posted email '{title}' to r/{POST_SUBREDDIT}.")
            posted += 1
        except Exception as exc:  # Reddit can raise many errors; surfacing message is helpful
            print(f"Failed to submit post for email '{title}': {exc}", file=sys.stderr)
            # Leave email unread so it can be retried manually
            continue

        # Mark as read only on success
        mail.store(msg_id, "+FLAGS", "(\\Seen)")

    mail.close()
    mail.logout()
    summary = f"Done. Posted: {posted}; Skipped: {skipped}"
    if deferred_to_reply:
        summary += f"; Deferred to replies: {deferred_to_reply}"
    print(summary)


if __name__ == "__main__":
    main()
