# Runbook - Social Feed Digest on the Hostinger VPS

Deploy target: Sam's Hostinger VPS, twice-daily runs, email via Resend, private
token-protected page. Everything secret lives in one `.env` file; nothing secret
is ever committed.

```
systemd timer (07:30 & 17:30 local) ─> digest run ─> data/digests/<tag>/ + data/pages/
                                                 ├─> Resend SMTP -> samjookim@gmail.com
                                                 └─> private page (token required)
```

## 1. Prerequisites

- Ubuntu 22.04/24.04 VPS with Python 3.10+ (`python3 --version`) and git.
- An xAI API key from <https://console.x.ai> (team API key).
- A Resend account (<https://resend.com>) - API key with sending permission.
- Optional: Reddit app credentials once the manual review approves (the worker
  works before that via RSS).
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
| `SMTP_PASSWORD` | Resend **API key** (`re_...`) - used as the SMTP password |
| `SMTP_FROM` | `onboarding@resend.dev` (see below) |

### Email via Resend (primary path)

The worker sends over standard SMTP, and Resend's SMTP bridge is
`smtp.resend.com`, port `587` (STARTTLS; `465` SSL also works), username
`resend`, password = your Resend API key (verified against
resend.com/docs/send-with-smtp, September 2026).

**No-domain constraint:** without a verified sending domain, Resend delivers only
to the Resend account owner's own address, and the from-address must be the
onboarding sender (`onboarding@resend.dev`). Because the digest targets
`samjookim@gmail.com`, the zero-DNS path works when Sam's Resend account email is
`samjookim@gmail.com`: set `SMTP_FROM=onboarding@resend.dev`, send the first test
to himself, done. **Optional upgrade:** verify a domain in Resend (DNS records in
their dashboard) for a custom from-address like `digest@sam's-domain`.

Volume is 2 sends/day, comfortably inside Resend's free tier - check the current
limits at <https://resend.com/pricing> rather than relying on a number here.

Optional overrides: `DIGEST_EMAIL_TO` (defaults to `profile.yaml` delivery
address), `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` once approved.

## 4. First dry run

```bash
cd /opt/social-feed-digest
set -a; source .env; set +a
.venv/bin/python -m digest run --dry-run
```

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

Until the CLI is present the run still completes: comments and post ideas are
clearly marked `TEMPLATE DRAFT`, and the digest header records
`drafting: template-fallback`. `DIGEST_DISABLE_CLAUDE=1` forces that fallback.

Voice bootstrap: `profile.yaml` starts with no voice samples and a neutral sharp
register. Comments Sam approves or edits go into `drafting.voice_samples`; review
the profile after two weeks of digests.

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

The portfolio watchlist sweeps once per day (state marker in
`data/state/`) and folds into that day's first digest. Each run prunes artifacts
older than `retention_days` (default 30), meeting the 30-day retention floor.

## 8. Verification checklist

- [ ] `systemctl list-timers digest.timer` shows two upcoming triggers.
- [ ] `ls data/digests` shows a dated run dir twice a day; `digest.md/html/json/eml` present.
- [ ] Email arrives at samjookim@gmail.com; `From: onboarding@resend.dev` until a domain is verified.
- [ ] Private page 200 with token, 404 without; `journalctl -u digest-page` clean.
- [ ] Run log shows `est. external cost` under $0.25 and no `WARNING` lines.

## 9. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `xAI auth failed (401/403)` | bad or revoked `XAI_API_KEY` in `.env` |
| `xAI billing error (402)` | out of credits at console.x.ai |
| `rate-limited, backing off` (Reddit) | expected for unauthenticated RSS; the run retries with backoff. Fill `REDDIT_*` once approved, or ignore if 2 of 3 subs still yield 5+ topics |
| `using template drafts` in the log | `claude` CLI missing or not logged in for the service user; install/login per section 5 |
| `run cost $... exceeded budget` | tighten `x_search.candidate_topics`, per-call search caps in `digest/collect/xai_collector.py`, or raise `cost.per_run_budget_usd` |
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
