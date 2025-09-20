# Reddit RSS Gmail Alerts

## Overview
This repository delivers near-real-time Reddit post alerts to Gmail using only the subreddit RSS feed and GitHub Actions. Every five minutes a scheduled workflow runs `rss_alerts.py`, which:

1. Polls `https://www.reddit.com/r/<SUBREDDIT>/new/.rss` with `feedparser`.
2. Loads previously-seen post IDs from a private GitHub Gist.
3. Emails new entries over Gmail SMTP and records the IDs back to the Gist for deduplication.

```
GitHub Actions (cron) ──▶ rss_alerts.py
        │                       │
        ├──▶ Reddit RSS feed ───┘
        ├──▶ Gmail SMTP (email delivery)
        └──▶ GitHub Gist (seen.json state)
```

This design keeps costs at $0 (GitHub free tier + Gmail personal limits), avoids Reddit OAuth, and persists state across ephemeral runners.

## Prerequisites
- Gmail account with 2-Step Verification enabled and a Gmail App Password for SMTP access ([how-to](https://support.google.com/accounts/answer/185833?hl=en)).
- Private GitHub Gist containing a file named `seen.json` seeded with `[]` ([create a gist](https://gist.github.com/)).
- GitHub fine-grained personal access token granting `gist:write` only ([token docs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token)).
- Repository where this workflow will run (fork or push this repo).

## Configuration & Secrets
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
| `POLL_LOOKBACK_MINUTES` | ❌ | Defaults to `60` to prevent backlog blasts on first run. |
| `GIST_TOKEN` | ✅ | Fine-grained PAT with `gist:write`. |
| `GIST_ID` | ✅ | The ID (hash) of your private Gist. |
| `INCLUDE_KEYWORDS` | ❌ | Comma-separated list; only posts containing at least one term will alert. |
| `EXCLUDE_KEYWORDS` | ❌ | Comma-separated list; posts containing any term will be skipped but marked seen. |

> Tip: confirm the Gist is private and contains `seen.json` with `[]` before the first workflow execution.

### Local Environment File
`.env.example` mirrors the secrets for local testing with `python-dotenv` or manual exports.

## Deployment
1. Fork or clone this repository.
2. Commit any adjustments (e.g., README tweaks) and push to GitHub.
3. Add the secrets described above in the target repo settings.
4. Ensure the private Gist exists with `seen.json` seeded as `[]`.
5. GitHub Actions will automatically schedule every 5 minutes; you can also trigger manually via **Actions → RSS → Gmail Alerts → Run workflow**.

## Acceptance Tests
Run these checks after configuring secrets:

1. **First Run Backlog Guard** – Manual dispatch should log `Emails sent this run: 0` when the feed only contains posts older than `POLL_LOOKBACK_MINUTES`, and the Gist state grows to include them.
2. **New Post Alert** – After a new subreddit post appears, the next scheduled run sends a single email with subject `[r/<subreddit>] <title>` and HTML body listing author, published time (if present), permalink, media link (if available), and summary.
3. **Deduplication** – Subsequent workflow runs do not resend emails for previously-seen post IDs (confirm by checking logs and avoiding duplicate messages).
4. **Keyword Filters (optional)** – If `INCLUDE_KEYWORDS`/`EXCLUDE_KEYWORDS` are set, verify that matching and non-matching posts behave as expected while still updating the Gist state.

## Manual Test Procedure
1. Configure all required secrets and optional filters.
2. Create/verify the private Gist with `seen.json` containing `[]`.
3. Trigger the workflow via **Actions → Run workflow**; expect `Emails sent this run: 0` on first execution unless new posts exist.
4. Publish or observe a new post in the target subreddit.
5. Wait for the next scheduled run (≤5 minutes) and confirm the Gmail inbox receives the formatted alert.
6. Observe additional runs; they should not resend the same post.

## Operational Notes
- **Latency**: Bounded by the 5-minute cron plus Reddit RSS propagation (~2–3 minutes typical).
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
- Respects Reddit’s RSS interface and terms of service; avoid excessive polling beyond the 5-minute schedule.
