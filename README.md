# Reddit RSS Gmail Alerts

## Overview
This repository delivers near-real-time Reddit post alerts to Gmail and can optionally turn emails into Reddit submissions – all via GitHub Actions. Two scheduled workflows ship with the project:

1. **RSS → Gmail Alerts** (every 15 minutes) runs `rss_alerts.py`, which:

1. Polls `https://www.reddit.com/r/<SUBREDDIT>/new/.rss` with `feedparser`.
2. Loads previously-seen post IDs from a private GitHub Gist.
3. Emails new entries over Gmail SMTP and records the IDs back to the Gist for deduplication.

2. **Email → Reddit Poster** (every 15 minutes) runs `email_to_reddit.py`, polling a Gmail inbox for specially tagged emails and submitting them to a subreddit with the Reddit API.

```
GitHub Actions (cron) ──▶ rss_alerts.py ───▶ Gmail alerts
                    └──▶ email_to_reddit.py ───▶ Reddit posts
```

This design keeps costs at $0 (GitHub free tier + Gmail personal limits), avoids Reddit OAuth, and persists state across ephemeral runners.

## Prerequisites
- Gmail account with 2-Step Verification enabled and a Gmail App Password for SMTP access ([how-to](https://support.google.com/accounts/answer/185833?hl=en)).
- Private GitHub Gist containing a file named `seen.json` seeded with `[]` ([create a gist](https://gist.github.com/)).
- GitHub fine-grained personal access token granting `gist:write` only ([token docs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token)).
- Repository where this workflow will run (fork or push this repo).

## Configuration & Secrets
### RSS → Gmail Alerts
Set the following repository secrets under **Settings → Secrets and variables → Actions** before enabling the workflow:

| Secret | Required | Description |
| --- | --- | --- |
| `SUBREDDIT` | ✅ | Subreddit name (e.g., `physicaltherapy`). |
| `GMAIL_USER` | ✅ | Gmail address that owns the App Password. |
| `GMAIL_APP_PASSWORD` | ✅ | 16-character Gmail App Password (no spaces needed; keep formatting consistent). |
| `TO_EMAIL` | ✅ | Recipient Gmail address (can match `GMAIL_USER`). |
| `SENDER_NAME` | ✅ | Display name (e.g., `EquipCore Alerts`). |
| `SMTP_HOST` | ❌ | Defaults to `smtp.gmail.com`; override if using another SMTP server. |
| `SMTP_PORT` | ❌ | Defaults to `587`; set to `465` for SSL-only SMTP. |
| `POLL_LOOKBACK_MINUTES` | ❌ | Defaults to `60`; keep it ≥ the cron interval (15) to cover slight delays. |
| `GIST_TOKEN` | ✅ | Fine-grained PAT with `gist:write`. |
| `GIST_ID` | ✅ | The ID (hash) of your private Gist. |
| `INCLUDE_KEYWORDS` | ❌ | Comma-separated list; only posts containing at least one term will alert. |
| `EXCLUDE_KEYWORDS` | ❌ | Comma-separated list; posts containing any term will be skipped but marked seen. |

> Tip: confirm the Gist is private and contains `seen.json` with `[]` before the first workflow execution.

### Local Environment File
`.env.example` mirrors the secrets for local testing with `python-dotenv` or manual exports.

### Email → Reddit Poster (optional)
Add these additional secrets if you want GitHub Actions to convert tagged emails into Reddit posts:

| Secret | Required | Description |
| --- | --- | --- |
| `POST_SUBREDDIT` | ✅ | Subreddit that should receive the posts (e.g., `kneereplacement`). |
| `REDDIT_CLIENT_ID` | ✅ | Client ID from your Reddit script app. |
| `REDDIT_CLIENT_SECRET` | ✅ | Client secret from the same app. |
| `REDDIT_USERNAME` | ✅ | Reddit account that owns the script app. |
| `REDDIT_PASSWORD` | ✅ | Reddit password for that account. |
| `REDDIT_USER_AGENT` | ❌ | Custom user agent; defaults to `github.com/jarmeli1/reddit-rss-alerts (Email to Reddit)`. |
| `GMAIL_IMAP_USER` | ❌ | Defaults to `GMAIL_USER`; override if you poll a different mailbox. |
| `GMAIL_IMAP_PASSWORD` | ❌ | Defaults to `GMAIL_APP_PASSWORD`; use a separate Gmail App Password if preferred. |
| `GMAIL_IMAP_HOST` | ❌ | Defaults to `imap.gmail.com`. |
| `GMAIL_IMAP_PORT` | ❌ | Defaults to `993`. |
| `GMAIL_IMAP_MAILBOX` | ❌ | Defaults to `INBOX`; set to a Gmail label if you filter posts there. |
| `EMAIL_SUBJECT_PREFIX` | ❌ | Defaults to `[Reddit]`; only emails whose subject begins with this string are posted. Set blank to post every unread email (not recommended). |

Workflow behaviour:

- The script checks the mailbox for unread messages. Only those whose subject begins with `EMAIL_SUBJECT_PREFIX` are considered. After a successful post, the email is marked read.
- Email subject (prefix stripped) becomes the Reddit title (300 char max). The plain-text body becomes the self-post body. Emails without a text/plain part are skipped and marked read.
- Leave unwanted emails unread; they will be retried each run until the issue is resolved. Skipped messages (e.g., missing prefix or body) are marked read to avoid loops; adjust the prefix to tighten or loosen the rules.

### Deployment
1. Fork or clone this repository.
2. Commit any adjustments (e.g., README tweaks) and push to GitHub.
3. Add the secrets described above in the target repo settings.
4. Ensure the private Gist exists with `seen.json` seeded as `[]`.
5. GitHub Actions will automatically schedule every 15 minutes; you can also trigger manually via **Actions → RSS → Gmail Alerts → Run workflow** or **Actions → Email → Reddit Poster → Run workflow**.

## Acceptance Tests
Run these checks after configuring secrets:

1. **First Run Backlog Guard** – Manual dispatch should log `Emails sent this run: 0` when the feed only contains posts older than `POLL_LOOKBACK_MINUTES`, and the Gist state grows to include them.
2. **New Post Alert** – After a new subreddit post appears, the next scheduled run sends a single email with subject `[r/<subreddit>] <title>` and HTML body listing author, published time (if present), permalink, media link (if available), and summary.
3. **Deduplication** – Subsequent workflow runs do not resend emails for previously-seen post IDs (confirm by checking logs and avoiding duplicate messages).
4. **Keyword Filters (optional)** – If `INCLUDE_KEYWORDS`/`EXCLUDE_KEYWORDS` are set, verify that matching and non-matching posts behave as expected while still updating the Gist state.

### Email → Reddit Poster
1. Send yourself an email whose subject starts with `[Reddit]` (or your configured prefix); keep the body plain text. Trigger **Email → Reddit Poster → Run workflow** and confirm the run logs `Posted email '...' to r/<subreddit>`.
2. Visit the subreddit to ensure the submission appears under the Reddit account tied to your script app.
3. Send an email without the prefix and confirm the workflow logs a skip and marks the message read.

## Manual Test Procedure
1. Configure all required secrets and optional filters.
2. Create/verify the private Gist with `seen.json` containing `[]`.
3. Trigger the workflow via **Actions → Run workflow**; expect `Emails sent this run: 0` on first execution unless new posts exist.
4. Publish or observe a new post in the target subreddit.
5. Wait for the next scheduled run (≤15 minutes) and confirm the Gmail inbox receives the formatted alert.
6. Observe additional runs; they should not resend the same post.

## Operational Notes
- **Latency**: Bounded by the 15-minute cron plus Reddit RSS propagation (~2–3 minutes typical).
- **Throughput & Limits**: Personal Gmail sends ~500 emails/day ([limits reference](https://support.google.com/a/answer/166852)). Typical subreddits remain far below this; adjust filters if you approach the limit.
- **Failure Modes**: RSS parse errors, SMTP authentication failures, or Gist permission issues cause the script to exit non-zero so the workflow run shows as failing.
- **Logs**: Review run logs in GitHub Actions; no secrets are printed. Gist and SMTP errors bubble up with context for easier troubleshooting.
- **Scalability**: Designed for one subreddit/recipient. Extend by adding another workflow or parameterizing lists.

## Troubleshooting
- `Missing required env/secret`: confirm the GitHub secret exists and is referenced correctly.
- `RSS parse error`: Reddit may throttle or respond with HTML; retry shortly or inspect the subreddit RSS URL.
- `smtp.SMTPAuthenticationError`: Regenerate the Gmail App Password or verify 2FA is enabled. Ensure SMTP host/port values match your Gmail account type.
- Gist updates failing (`403 Forbidden`): Confirm the PAT has `gist:write` scope and the Gist belongs to the token owner.

## Safety & Compliance
- Emails include sanitized titles and summaries; media is provided as a link, not embedded.
- Uses Gmail App Passwords (not raw credentials) and a minimally scoped PAT.
- Respects Reddit’s RSS interface and terms of service; avoid excessive polling beyond the 15-minute schedule.
