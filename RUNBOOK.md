# Runbook - Social Feed Digest on the Hostinger VPS

Deploy target: Sam's Hostinger VPS, twice-daily runs, email via Resend, private
token-protected page. Everything secret lives in one `.env` file; nothing secret
is ever committed.

```
systemd timer (07:30 & 17:30 local) ─> digest run ─> data/digests/<tag>/ + data/pages/
                                                 ├─> Resend SMTP -> samislucid98@gmail.com
                                                 └─> private page (token required)
```

## 1. Prerequisites

- Ubuntu 22.04/24.04 VPS with Python 3.10+ (`python3 --version`) and git.
- An xAI API key from <https://console.x.ai> (team API key).
- A Resend account (<https://resend.com>) - API key with sending permission.
- Optional: Reddit app credentials once the manual review approves (the worker
  works before that via RSS).
- Optional: X API v2 user-context credentials for the followed-account boost
  (section 8); the worker works without them and behavior is unchanged.
- Optional: Claude Code CLI for voice-matched drafting (section 5).

## 2. Clone and install

```bash
sudo mkdir -p /opt && sudo chown "$USER" /opt
git clone https://github.com/samislucid/social-feed-digest.git /opt/social-feed-digest
cd /opt/social-feed-digest
python3 -m venv .venv
.venv/bin/pip install -e .
```

Set the server timezone once so schedule times below are Sam-local:

```bash
sudo timedatectl set-timezone America/Los_Angeles
```

## 3. Secrets: create `.env`

```bash
cp .env.example .env
chmod 600 .env
openssl rand -hex 24   # paste as DIGEST_PAGE_TOKEN
```

Fill in:

| Variable | Value |
| --- | --- |
| `XAI_API_KEY` | xAI API key (`xai-...`) |
| `DIGEST_PAGE_TOKEN` | random hex from above |
| `SMTP_HOST` | `smtp.resend.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | `resend` |
| `RESEND_API_KEY_HERMES_SOCIAL` | Resend **API key** (`re_...`) - used as the SMTP password |
| `SMTP_FROM` | `onboarding@resend.dev` (see below) |

### Email via Resend (primary path)

The worker sends over standard SMTP, and Resend's SMTP bridge is
`smtp.resend.com`, port `587` (STARTTLS; `465` SSL also works), username
`resend`, password = the API key in `RESEND_API_KEY_HERMES_SOCIAL` (verified
against resend.com/docs/send-with-smtp, September 2026). The worker reads that
variable as the SMTP password; `SMTP_PASSWORD` remains a generic override for
any non-Resend transport.

**No-domain constraint (live-verified 2026-09-07), satisfied by design:** without
a verified sending domain, Resend delivers only to the Resend account owner's
own address, and the from-address must be the onboarding sender
(`onboarding@resend.dev`). The recipient below **is** Sam's Resend account
owner address, so this constraint never bites: **no domain verification is ever
needed** for this digest, and `From: onboarding@resend.dev` is permanent unless
a domain is verified later for some other reason.

**Permanent recipient:** `samislucid98@gmail.com`, set in `profile.yaml`
`delivery.email_to`. `DIGEST_EMAIL_TO` remains an optional override (empty by
default). Delivering to any other address would first require verifying a
domain in Resend (DNS records in their dashboard), then setting `SMTP_FROM` to
an address on that domain; nothing in this repo depends on that ever happening.

Volume is 2 sends/day, comfortably inside Resend's free tier - check the current
limits at <https://resend.com/pricing> rather than relying on a number here.

Optional overrides: `DIGEST_EMAIL_TO` (defaults to the `profile.yaml` delivery
address), `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` once approved, and the
X API v2 credentials in section 8.

## 4. First dry run

```bash
cd /opt/social-feed-digest
set -a; source .env; set +a
.venv/bin/python -m digest run --dry-run
```

Deploy-time email verification (no re-collection, no xAI spend): send the latest
existing digest artifact through the bridge and expect Resend's queued response:

```bash
.venv/bin/python -m digest send
# expect: email sent to samislucid98@gmail.com: 250 ... Ok: queued ...
```

If Resend rejects the send, the error prints verbatim; with the onboarding
sender the usual cause is a recipient that is not the Resend account owner's
address.

Expect: three `collected ... items from r/...` lines, one `x_trends` and one
`web_sweep` call, `8/8 topics` (or 5-8), `dry run: email not sent (digest.eml
written...)`, and artifacts in `data/digests/<date_time>/`. Cost prints at the
end; the hard cap is `$0.25` (`cost.per_run_budget_usd` in `profile.yaml`).

To verify email rendering + sending once: `python -m digest run` (no `--dry-run`)
sends the digest to the delivery address - this is the deploy-time email check.

## 5. claude -p drafting (voice-matched comments)

The drafting step calls `claude -p` headless, covered by Sam's Max plan monthly
automation credit:

```bash
sudo npm install -g @anthropic-ai/claude-code   # or the official installer
claude login                                     # once, as the same user systemd runs the worker as
claude -p "Reply with OK"                        # verify non-interactive use works
```

If the service's PATH cannot see the binary, point the worker straight at it via
`.env` (no unit edit needed; the next run start picks it up):

```bash
echo "DIGEST_CLAUDE_BIN=$(sudo -u "$(systemctl show -p User --value digest.service)" bash -lc 'command -v claude' 2>/dev/null)" | sudo tee -a /opt/social-feed-digest/.env
```

Until the CLI is present the run still completes: comments and post ideas are
clearly marked `TEMPLATE DRAFT`, the email subject is tagged
`[DEGRADED: template drafts]`, the run footer names the exact claude failure
reason (`DRAFTING DEGRADED: ...`), and the digest header records
`drafting: template-fallback`. `DIGEST_DISABLE_CLAUDE=1` forces that fallback.

Voice bootstrap: `profile.yaml` starts with no voice samples and a neutral sharp
register. Comments Sam approves or edits go into `drafting.voice_samples`; review
the profile after two weeks of digests.

### 5.1 Timer run emails TEMPLATE DRAFTs but SSH runs draft fine (systemd)

A manual SSH run drafts with real `claude -p`, but the 07:30/17:30 timer run
degrades. Almost always the service environment cannot see the claude binary or
its login state: systemd's default PATH misses `~/.local/bin` (official
installer) and nvm trees, and without the right `User=`/`HOME` the CLI cannot
find its logged-in credentials. Since 2026-09-07 the failure reason is in the
artifacts, so start by reading it:

```bash
cd /opt/social-feed-digest && git pull && .venv/bin/pip install -e .
sudo systemctl start digest.service               # one test run right now (blocks; see below)
journalctl -u digest.service -n 100 --no-pager | grep -iE 'claude|DEGRADED'
```

Step 1 blocks by design: `digest.service` is a oneshot, so `systemctl start`
waits for the full run to finish (2-5 minutes; claude drafting runs inside the
service). A long silent prompt is normal, not a hang. Pasting the whole block
is safe: the `journalctl` line only runs once the start command returns, i.e.
against a finished run. Ctrl-C would detach from the wait; it does not stop
the run, and the log's `done: ...` line marks a finished run.

The log line `WARN: claude -p unavailable: ...` states the failure: binary not
found on PATH (with the PATH the service actually saw), a non-zero exit with
claude's own stderr (auth/login problems), a timeout, or empty output. The
emailed digest matches: subject tagged `[DEGRADED: template drafts]`, footer
line `DRAFTING DEGRADED: <reason>`.

Reproduce exactly what the service sees (not your SSH shell):

```bash
SVC_USER=$(systemctl show -p User --value digest.service); SVC_USER=${SVC_USER:-root}
echo "digest.service runs as: $SVC_USER"
sudo systemd-run --uid="$SVC_USER" --pipe --wait \
  bash -lc 'command -v claude || echo "claude NOT on PATH"; claude --version; claude -p "Reply with OK"'
```

- `claude NOT on PATH`: the binary sits outside systemd's default PATH.
- Found but `claude -p` fails with a login/auth error: the service user's
  `$HOME` does not hold the logged-in `~/.claude` state.

Fix: edit the unit (`sudo systemctl edit --full digest.service`) and add under
`[Service]` (replace `sam` with the real username; check with `echo $USER`):

```ini
[Service]
User=sam
Environment=PATH=/home/sam/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME=/home/sam
```

Unit-free alternative: put `DIGEST_CLAUDE_BIN=/home/sam/.local/bin/claude` in
`.env` (absolute path from `command -v claude` as that user), and if the login
state lives elsewhere also set `CLAUDE_CONFIG_DIR=/home/sam/.claude`.

Apply and verify one full run:

```bash
sudo systemctl daemon-reload
sudo systemctl start digest.service --no-block
journalctl -u digest.service -f    # expect: drafts via claude -p: N comments ...
```

Wait for the run to finish: `--no-block` only makes `systemctl start` return
immediately, and Ctrl-C exits the log tail without stopping the run. The run
takes 2-5 minutes (claude drafting included) and is done when the log prints
`done: N/M topics ... artifacts in ...`.

Pass criteria: the log shows `drafts via claude -p`, no `WARN: claude -p
unavailable` line; the new email has no `[DEGRADED: template drafts]` subject
tag and no `DRAFTING DEGRADED` footer line. The 17:30 scheduled trigger uses the
same unit, so nothing else to change.

## 6. Private digest page

The page server (`digest serve`) serves `data/pages/` and requires the token on
every request (`?token=`, `Authorization: Bearer`, or `X-Digest-Token`). Wrong or
missing token returns 404 so the page's existence is not advertised; it refuses
to start without `DIGEST_PAGE_TOKEN`.

`/etc/systemd/system/digest-page.service`:

```ini
[Unit]
Description=Social feed digest private page
After=network.target

[Service]
User=%i
WorkingDirectory=/opt/social-feed-digest
EnvironmentFile=/opt/social-feed-digest/.env
ExecStart=/opt/social-feed-digest/.venv/bin/python -m digest serve --host 0.0.0.0 --port 8787
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

(If the service runs as a named user, replace `%i` with that username.)

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now digest-page
sudo ufw allow 8787/tcp
curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:8787/latest?token=$DIGEST_PAGE_TOKEN"   # 200
```

Open `http://<vps-ip>:8787/?token=...` (or `/latest`). For HTTPS, front it with
nginx + certbot and proxy to `127.0.0.1:8787`; the token check stays in the app.

## 7. Twice-daily schedule

`/etc/systemd/system/digest.timer`:

```ini
[Unit]
Description=Run social feed digest twice a day

[Timer]
OnCalendar=*-*-* 07:30:00
OnCalendar=*-*-* 17:30:00
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
```

`/etc/systemd/system/digest.service`:

```ini
[Unit]
Description=Social feed digest run

[Service]
Type=oneshot
WorkingDirectory=/opt/social-feed-digest
EnvironmentFile=/opt/social-feed-digest/.env
ExecStart=/opt/social-feed-digest/.venv/bin/python -m digest run
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now digest.timer
systemctl list-timers digest.timer    # next run visible
journalctl -u digest.service -f       # watch a run
```

`Persistent=true` fires a run as soon as the timer starts if a scheduled slot
was missed, so `journalctl` may show one immediately. Each run takes 2-5
minutes (claude drafting included); wait for the `done:` line instead of
reading the quiet stretch as a hang.

The portfolio watchlist sweeps once per day (state marker in
`data/state/`) and folds into that day's first digest. Each run prunes artifacts
older than `retention_days` (default 30), meeting the 30-day retention floor.

## 8. Optional: X API v2 followed-account boost

Without these credentials the worker behaves exactly as before (no requests, no
state). With them, the worker syncs Sam's following list at most once a week via
an Owned Read (`GET /2/users/{id}/following`, `$0.001` per account per docs.x.com
pricing), caches it in `data/state/x_following.json`, and boosts digest topics
authored by accounts Sam follows (the handles also inform the xAI scout prompt
at no extra cost). The sync is hard-capped by `cost.per_run_budget_usd`: it only
fetches pages the remaining run budget can afford, then resumes from the cursor
on the next weekly window, so no full run ever exceeds the budget.

One-time setup:

1. Create an app at <https://console.x.com>: add a project, create an app, and
   enable OAuth 1.0a user authentication with Read permissions.
2. Buy a small credit balance in the console and set a spending limit. The sync
   spends at most the run budget minus a small reserve for the standing xAI
   calls, and never syncs more than once a week.
3. User-context auth on the VPS: in the app's "Keys and tokens" page, generate
   the OAuth 1.0a Consumer Keys and your own user Access Token and Secret (a
   personal app can generate tokens for its owner). Fill them into `.env` per
   `.env.example`:
   - `X_API_OAUTH1_CONSUMER_KEY` / `X_API_OAUTH1_CONSUMER_SECRET`
   - `X_API_ACCESS_TOKEN` / `X_API_ACCESS_TOKEN_SECRET`
4. Find your numeric id once (a single Owned Read, $0.001). With the `.env`
   loaded (`set -a; source .env; set +a`):

   ```bash
   .venv/bin/python - <<'PY'
   import os, requests
   from requests_oauthlib import OAuth1
   a = OAuth1(os.environ["X_API_OAUTH1_CONSUMER_KEY"], os.environ["X_API_OAUTH1_CONSUMER_SECRET"],
              os.environ["X_API_ACCESS_TOKEN"], os.environ["X_API_ACCESS_TOKEN_SECRET"])
   print(requests.get("https://api.x.com/2/users/me", auth=a).json())
   PY
   ```

   Put the returned `data.id` into `.env` as `X_API_USER_ID`.
5. Install the optional extra and verify with a dry run:

   ```bash
   .venv/bin/pip install -e '.[xapi]'
   .venv/bin/python -m digest run --dry-run
   ```

   Expect an `X following sync: N accounts read ...` log line and
   `data/state/x_following.json` present. Pricing and rate limits can change;
   confirm in console.x.com before relying on them.

## 9. Verification checklist

- [ ] `systemctl list-timers digest.timer` shows two upcoming triggers.
- [ ] `ls data/digests` shows a dated run dir twice a day; `digest.md/html/json/eml` present.
- [ ] `python -m digest send` re-sends the latest digest and prints Resend's queued response.
- [ ] Email arrives at samislucid98@gmail.com (the Resend account owner address); `From: onboarding@resend.dev`.
- [ ] If X API credentials are set: the log shows `X following sync` at most once a week and `data/state/x_following.json` exists.
- [ ] Private page 200 with token, 404 without; `journalctl -u digest-page` clean.
- [ ] Run log shows `est. external cost` under $0.25 and no `WARNING` lines.

## 10. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `xAI auth failed (401/403)` | bad or revoked `XAI_API_KEY` in `.env` |
| `xAI billing error (402)` | out of credits at console.x.ai |
| `rate-limited, backing off` (Reddit) | expected for unauthenticated RSS; the run retries with backoff. Fill `REDDIT_*` once approved, or ignore if 2 of 3 subs still yield 5+ topics |
| `using template drafts` in the log / `[DEGRADED: template drafts]` subject | since 2026-09-07 the run footer prints the exact claude failure reason (`DRAFTING DEGRADED: ...`); diagnose and fix per section 5.1 (usually systemd PATH/HOME) |
| `run cost $... exceeded budget` | tighten `x_search.candidate_topics`, per-call search caps in `digest/collect/xai_collector.py`, or raise `cost.per_run_budget_usd` |
| `X following sync failed` | X API creds missing/invalid, `requests-oauthlib` not installed (`pip install -e '.[xapi]'`), or no credit left at console.x.com; the run continues with the cached list |
| `refusing to serve an unprotected private page` | `DIGEST_PAGE_TOKEN` missing from `.env` |
| no email, no error | Resend no-domain rule: recipient must be the Resend account owner's address while `SMTP_FROM=onboarding@resend.dev` |

## 10. Cost notes

- xAI Live Search: $5 per 1,000 tool invocations; grok-4.6 $2/1M input, $6/1M
  output. Two calls per run with hard caps (3 x_search + up to 2 web_search),
  low reasoning effort, and a mid-run guard that skips the sweep if the first
  call nears the budget.
- Observed: ~$0.11-$0.14 per full run (including the once-daily portfolio sweep).
  Worst case 2 runs/day is ~$6-8/month; the PRD target is under $5/month, so if
  needed: keep the portfolio sweep on its once-daily cadence, drop the web sweep
  from the second daily run, or reduce `candidate_topics`. No paid X API is used.
- Email via Resend free tier; Reddit RSS free; VPS already owned; `claude -p`
  covered by the Max plan automation credit.
