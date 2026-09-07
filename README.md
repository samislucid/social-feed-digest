# social-feed-digest

Twice-daily digest of hot topics from Sam's circles: X, Reddit, and (via a watched
inbox) LinkedIn. Each digest has 5-8 ranked topics with source links, a
ready-to-paste suggested comment per topic, a no-comment quiet-share link per
topic, and 3-5 original post ideas per channel. Delivered by email (Resend SMTP)
and served from a private token-protected page.

## Pipeline

```
profile.yaml ──┐
XAI Live Search (x_search + web_search tools) ──┤
Reddit (RSS now, PRAW when approved) ──────────┼─> rank (dedupe, score) ─> draft (claude -p) ─> render ─> deliver
data/inbox/linkedin/* (Bright-pasted items) ───┘                                                                     ├─> email (.eml / SMTP send)
                                                                                                                     └─> data/pages (private page)
```

- **Collect**: xAI Responses API with server-side `x_search` / `web_search` tools
  (no paid X API). Reddit hot listings via public `.rss` with backoff; the PRAW
  Data-API source activates automatically when app credentials exist. LinkedIn
  items arrive as files in the watched inbox - the server never reads LinkedIn.
- **Rank**: deterministic scorer. Profile niche/keyword weights drive relevance;
  multi-source, cross-channel agreement and recency drive momentum. Same story is
  merged once and keeps each channel's share target.
- **Draft**: headless `claude -p` matches the voice in `profile.yaml`. Where the
  CLI is unavailable, drafts fall back to placeholders clearly marked
  `TEMPLATE DRAFT` (the digest shows the drafting source).
- **Deliver**: dated artifacts under `data/digests/<run_tag>/` (`digest.md`,
  `digest.html`, `digest.json`, `digest.eml`), a static private page under
  `data/pages/`, and an email send unless `--dry-run`.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env         # fill in secrets (never committed)
.venv/bin/python -m digest run --dry-run
```

Dry run collects from live sources, writes all artifacts and `digest.eml`, and
skips the email send. Production run (cron/systemd): `python -m digest run`.

### Commands

| Command | Purpose |
| --- | --- |
| `python -m digest run [--dry-run] [--skip-portfolio] [--profile PATH] [--data-dir PATH]` | full pipeline; portfolio watchlist sweeps once per day |
| `python -m digest serve [--host HOST] [--port PORT]` | private page server; requires `DIGEST_PAGE_TOKEN`, refuses to start without it |
| `python -m digest prune` | apply `retention_days` to stored runs |

## Configuration

Content settings live in `profile.yaml` (niches, keywords, subreddits, X handles,
portfolio watchlist, voice, post-idea counts, delivery, cost budget, retention).
Edits change the next run with no code changes and no restarts.

Secrets and infra live in environment variables (see `.env.example`): `XAI_API_KEY`,
`DIGEST_PAGE_TOKEN`, `SMTP_*`, optional `REDDIT_CLIENT_ID/SECRET`, `DIGEST_DATA_DIR`.

### LinkedIn watched inbox

Bright writes one file per batch to `data/inbox/linkedin/` on Sam's request:

- `.json`: `{"title", "url", "summary", "author", "posted_at"}` (or a list)
- `.md`: first `# ` heading is the title, first markdown link is the URL

Parsed files move to `data/inbox/linkedin/processed/`. No automated LinkedIn
reads originate from this server (v1 non-goal).

## Cost model

xAI Live Search tool invocations cost $5 per 1,000 calls ($0.005 each); grok-4.6
tokens are $2/1M input, $6/1M output. A full run makes two focused calls
(X trends, web/news sweep) with per-call search caps and a mid-run guard that
skips the sweep if the first call nears the budget. Observed cost per run is
roughly $0.11-$0.14 against the $0.25 hard cap in `profile.yaml` (`cost.per_run_budget_usd`).
Over-budget runs warn in the digest and in the run log.

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

34 offline tests cover config validation, ranking/dedupe, RSS parsing (fixture
feed), the LinkedIn inbox contract, drafting fallback and claude-JSON parsing,
email/HTML rendering, and the page server's token auth.

See `RUNBOOK.md` for Hostinger VPS deployment: secrets, Resend email, the
private page, and the twice-daily schedule.
