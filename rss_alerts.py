#!/usr/bin/env python3
import os
import json
import smtplib
import feedparser
import requests
import datetime as dt
from email.mime.text import MIMEText
from email.utils import formatdate, parsedate_to_datetime
from html import escape

# --- Config from env / secrets ---
def env_str(name, default=None):
    val = os.getenv(name)
    if val is None:
        return default
    val = val.strip()
    return val or default


def env_int(name, default):
    val = env_str(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError as exc:
        raise SystemExit(f"Invalid integer for {name}: {val}") from exc


SUBREDDIT = env_str("SUBREDDIT", "")
GMAIL_USER = env_str("GMAIL_USER", "")
GMAIL_APP_PASSWORD = env_str("GMAIL_APP_PASSWORD", "")
TO_EMAIL = env_str("TO_EMAIL", "")
SENDER_NAME = env_str("SENDER_NAME", "EquipCore Alerts")

SMTP_HOST = env_str("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = env_int("SMTP_PORT", 587)

GIST_TOKEN = env_str("GIST_TOKEN", "")
GIST_ID = env_str("GIST_ID", "")

POLL_LOOKBACK_MINUTES = env_int("POLL_LOOKBACK_MINUTES", 60)
FEED_USER_AGENT = env_str(
    "FEED_USER_AGENT",
    "github.com/jarmeli1/reddit-rss-alerts (RSS Gmail Alerts)",
)

STATE_FILENAME = "seen.json"  # stored inside the Gist

INCLUDE_KEYWORDS = [k.strip().lower() for k in os.getenv("INCLUDE_KEYWORDS", "").split(",") if k.strip()]
EXCLUDE_KEYWORDS = [k.strip().lower() for k in os.getenv("EXCLUDE_KEYWORDS", "").split(",") if k.strip()]

# --- Validation ---
def require(name, val):
    if not val:
        raise SystemExit(f"Missing required env/secret: {name}")

for name, val in [
    ("SUBREDDIT", SUBREDDIT),
    ("GMAIL_USER", GMAIL_USER),
    ("GMAIL_APP_PASSWORD", GMAIL_APP_PASSWORD),
    ("TO_EMAIL", TO_EMAIL),
    ("GIST_TOKEN", GIST_TOKEN),
    ("GIST_ID", GIST_ID),
]:
    require(name, val)

# --- Helpers: Time ---
def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def parse_rfc822_or_none(val):
    if not val:
        return None
    try:
        dt_obj = parsedate_to_datetime(val)
    except (TypeError, ValueError, IndexError):
        return None
    if dt_obj is None:
        return None
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=dt.timezone.utc)
    return dt_obj.astimezone(dt.timezone.utc)


# --- Gist IO ---
def gist_headers():
    return {
        "Authorization": f"Bearer {GIST_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def gist_get_state():
    url = f"https://api.github.com/gists/{GIST_ID}"
    r = requests.get(url, headers=gist_headers(), timeout=20)
    r.raise_for_status()
    data = r.json()
    files = data.get("files", {})
    if STATE_FILENAME in files and files[STATE_FILENAME].get("content") is not None:
        try:
            entries = json.loads(files[STATE_FILENAME]["content"])
            if isinstance(entries, list):
                return set(entries)
        except Exception:
            return set()
    return set()


def gist_save_state(seen_ids):
    url = f"https://api.github.com/gists/{GIST_ID}"
    payload = {
        "files": {
            STATE_FILENAME: {
                "content": json.dumps(sorted(seen_ids))
            }
        }
    }
    r = requests.patch(url, headers=gist_headers(), json=payload, timeout=20)
    r.raise_for_status()


# --- RSS ---
def fetch_reddit_feed(url):
    headers = {
        "User-Agent": FEED_USER_AGENT,
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    }
    response = requests.get(url, headers=headers, timeout=20)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        snippet = response.text[:200].replace("\n", " ") if response.text else ""
        raise SystemExit(
            f"RSS HTTP error for {url}: {exc}; first 200 chars: {snippet!r}"
        ) from exc

    feed = feedparser.parse(response.content)
    if feed.bozo:
        detail = getattr(feed, "bozo_exception", "")
        snippet = response.text[:200].replace("\n", " ") if response.text else ""
        raise SystemExit(
            f"RSS parse error for {url}: {detail}; first 200 chars: {snippet!r}"
        )
    return feed


# --- Email ---
def send_email(subject, html_body):
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = f"{SENDER_NAME} <{GMAIL_USER}>"
    msg["To"] = TO_EMAIL
    msg["Date"] = formatdate(localtime=True)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.sendmail(GMAIL_USER, [TO_EMAIL], msg.as_string())


def extract_media_url(entry):
    media_content = entry.get("media_content") or []
    for media in media_content:
        url = media.get("url")
        if url:
            return url
    for link in entry.get("links", []):
        if link.get("rel") in {"enclosure", "alternate", None}:
            href = link.get("href")
            if href:
                return href
    return ""


def passes_keyword_filters(entry):
    if not INCLUDE_KEYWORDS and not EXCLUDE_KEYWORDS:
        return True

    text_parts = [
        entry.get("title", ""),
        entry.get("summary", ""),
        entry.get("author", ""),
    ]
    body = " \n ".join(part for part in text_parts if part).lower()

    if INCLUDE_KEYWORDS and not any(keyword in body for keyword in INCLUDE_KEYWORDS):
        return False
    if EXCLUDE_KEYWORDS and any(keyword in body for keyword in EXCLUDE_KEYWORDS):
        return False
    return True


# --- Render ---
def render_email(subreddit, entry):
    title = entry.get("title", "(no title)")
    link = entry.get("link", "")
    author = entry.get("author", "")
    published = entry.get("published", "")
    summary = entry.get("summary", "")
    media_url = extract_media_url(entry)

    safe_title = escape(title)
    safe_author = escape(author) if author else "unknown"
    safe_published = escape(published or "")
    safe_link = escape(link or "")
    media_block = ""
    if media_url:
        safe_media = escape(media_url)
        media_block = f"<p style=\"margin:0 0 10px 0\"><b>Media Link:</b> <a href=\"{safe_media}\">{safe_media}</a></p>"

    html = f"""
    <div style="font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; line-height:1.5">
      <h2 style="margin:0 0 6px 0">[r/{escape(subreddit)}] {safe_title}</h2>
      <p style="margin:0 0 10px 0">
        <b>Author:</b> {safe_author}
        {' | <b>Published:</b> ' + safe_published if safe_published else ''}
      </p>
      <p style="margin:0 0 10px 0">
        <b>Reddit Link:</b> <a href="{safe_link}">{safe_link}</a>
      </p>
      {media_block}
      <hr style="border:none;border-top:1px solid #ddd;margin:10px 0"/>
      <div>{summary}</div>
    </div>
    """
    return html


def recent_enough(entry, cutoff):
    published_dt = None
    if entry.get("published_parsed"):
        published_dt = dt.datetime(*entry["published_parsed"][:6], tzinfo=dt.timezone.utc)
    elif entry.get("updated_parsed"):
        published_dt = dt.datetime(*entry["updated_parsed"][:6], tzinfo=dt.timezone.utc)
    else:
        published_dt = parse_rfc822_or_none(entry.get("published") or entry.get("updated"))

    if published_dt is None:
        return True
    return published_dt >= cutoff


# --- Main ---
def main():
    rss_url = f"https://www.reddit.com/r/{SUBREDDIT}/new/.rss"
    feed = fetch_reddit_feed(rss_url)

    seen_ids = gist_get_state()
    now = utcnow()
    lookback_cutoff = now - dt.timedelta(minutes=POLL_LOOKBACK_MINUTES)

    new_seen = False
    sent_count = 0

    for entry in feed.entries:
        entry_id = entry.get("id") or entry.get("link")
        if not entry_id:
            continue

        if entry_id in seen_ids:
            continue

        if not recent_enough(entry, lookback_cutoff):
            seen_ids.add(entry_id)
            new_seen = True
            continue

        if not passes_keyword_filters(entry):
            seen_ids.add(entry_id)
            new_seen = True
            continue

        subject = f"[r/{SUBREDDIT}] {entry.get('title','(no title)')[:180]}"
        html = render_email(SUBREDDIT, entry)
        send_email(subject, html)

        seen_ids.add(entry_id)
        new_seen = True
        sent_count += 1

    if new_seen:
        gist_save_state(seen_ids)

    print(f"Done. Emails sent this run: {sent_count}")


if __name__ == "__main__":
    main()
