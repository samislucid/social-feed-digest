"""CLI entry points.

  python -m digest run [--dry-run] [--skip-portfolio] [--profile PATH] [--data-dir PATH]
  python -m digest serve [--host HOST] [--port PORT]
  python -m digest prune
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .collect.linkedin_inbox import collect_linkedin
from .collect.reddit_collector import collect_reddit
from .collect.xai_collector import XaiError, collect_web_sweep, collect_x_topics
from .config import ConfigError, Settings, load_profile
from .deliver import local_date, prune, run_tag_for, send_email, store_digest
from .draft import draft_digest
from .rank import build_topics
from .render import Digest, build_email, render_html, render_markdown
from .server import make_server


def _log(msg: str) -> None:
    print(msg, flush=True)


def _portfolio_state_path(settings: Settings) -> Path:
    return Path(settings.data_dir) / "state" / "portfolio_sweep_date.txt"


def _portfolio_due(settings: Settings) -> bool:
    path = _portfolio_state_path(settings)
    try:
        return path.read_text(encoding="utf-8").strip() != local_date()
    except OSError:
        return True


def _mark_portfolio_done(settings: Settings) -> None:
    path = _portfolio_state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(local_date(), encoding="utf-8")


def _run_web_sweep(
    settings: Settings,
    profile: dict,
    skip_portfolio: bool,
    budget: float,
    items: list,
    collection: dict[str, int],
    usage: list,
) -> None:
    include_portfolio = (
        (not skip_portfolio)
        and bool((profile.get("portfolio") or {}).get("enabled", True))
        and _portfolio_due(settings)
    )
    try:
        web_items, web_usage = collect_web_sweep(settings, profile, include_portfolio=include_portfolio)
        items.extend(web_items)
        collection["web"] = collection.get("web", 0) + len(web_items)
        usage.append(("web_sweep" + ("+portfolio" if include_portfolio else ""), web_usage))
        if include_portfolio:
            _mark_portfolio_done(settings)
    except XaiError as exc:
        _log(f"WARN: xAI web/news sweep failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - one flaky source must not kill the run
        _log(f"WARN: xAI web/news sweep failed unexpectedly: {exc}")


def _collect(settings: Settings, profile: dict, skip_portfolio: bool) -> tuple[list, dict, list]:
    items: list = []
    collection: dict[str, int] = {}
    usage: list[tuple[str, dict]] = []

    try:
        reddit_items = collect_reddit(settings, profile, log=_log)
        items.extend(reddit_items)
        collection["reddit"] = len(reddit_items)
    except Exception as exc:  # one flaky source must not kill the run
        _log(f"WARN: Reddit collection failed: {exc}")
        collection["reddit"] = 0

    budget = float((profile.get("cost") or {}).get("per_run_budget_usd", 0.25))

    if settings.xai_api_key:
        try:
            x_items, x_usage = collect_x_topics(settings, profile)
            items.extend(x_items)
            collection["x"] = len(x_items)
            usage.append(("x_trends", x_usage))
        except XaiError as exc:
            _log(f"WARN: xAI X-trends collection failed: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface and continue
            _log(f"WARN: xAI X-trends collection failed unexpectedly: {exc}")

        spent_so_far = x_usage.get("cost_usd", 0.0) if isinstance(x_usage, dict) else 0.0
        if spent_so_far > 0.6 * budget:
            _log(
                f"WARN: skipping web/news sweep; X-trends call already cost "
                f"${spent_so_far:.4f} of the ${budget:.2f} run budget"
            )
        else:
            _run_web_sweep(settings, profile, skip_portfolio, budget, items, collection, usage)
    else:
        _log("WARN: XAI_API_KEY not set; X trends and web/news sweep skipped (Reddit + LinkedIn only)")

    try:
        li_items = collect_linkedin(settings, profile, log=_log)
        items.extend(li_items)
        collection["linkedin"] = len(li_items)
    except Exception as exc:  # noqa: BLE001
        _log(f"WARN: LinkedIn inbox read failed: {exc}")
        collection["linkedin"] = 0

    return items, collection, usage


def cmd_run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    settings = Settings.from_env()
    if args.data_dir:
        settings.data_dir = Path(args.data_dir)
    profile = load_profile(args.profile)

    items, collection, usage = _collect(settings, profile, args.skip_portfolio)
    topics = build_topics(items, profile)

    warnings: list[str] = []
    min_topics = int((profile.get("rank") or {}).get("min_topics", 5))
    max_topics = int((profile.get("rank") or {}).get("max_topics", 8))
    if len(topics) < min_topics:
        warnings.append(f"only {len(topics)} topics ranked (profile minimum {min_topics})")
    if not topics:
        _log("ERROR: no topics collected; nothing to deliver")
        return 1

    draft = draft_digest(topics, profile, settings, log=_log)

    tool_calls = sum(int(u.get("search_tool_calls", 0)) for _, u in usage)
    total_cost = sum(float(u.get("cost_usd", 0.0)) for _, u in usage)
    budget = float((profile.get("cost") or {}).get("per_run_budget_usd", 0.25))
    if usage and total_cost > budget:
        warnings.append(f"run cost ${total_cost:.4f} exceeded budget ${budget:.2f}")

    now = datetime.now(timezone.utc)
    digest = Digest(
        run_tag=run_tag_for(now),
        generated_at=now,
        profile_name=str(profile.get("name", "digest")),
        topics=topics,
        post_ideas=draft["post_ideas"],
        draft_source=draft["source"],
        collection=collection,
        cost={
            "search_tool_calls": tool_calls,
            "total_usd": round(total_cost, 4),
            "budget_usd": budget,
            "calls": [{"name": name, **u} for name, u in usage],
        },
        email_to=settings.email_to_override
        or ((profile.get("delivery") or {}).get("email_to") or ""),
        subject_prefix=(profile.get("delivery") or {}).get("email_subject_prefix", "[Feed Digest]"),
        duration_s=time.monotonic() - started,
        warnings=warnings,
    )

    markdown = render_markdown(digest)
    html = render_html(digest)
    message = build_email(digest, markdown, html, settings.smtp_from or "digest@localhost")
    run_dir = store_digest(digest, markdown, html, message, settings)
    email_status = send_email(message, settings, dry_run=args.dry_run, log=_log)
    prune(settings, int(profile.get("retention_days", 30)), log=_log)

    if args.json_summary:
        summary = {
            "run_tag": digest.run_tag,
            "topic_count": len(topics),
            "topics": [
                {
                    "rank": i,
                    "title": t.title,
                    "niche": t.niche,
                    "channels": t.channel_labels,
                    "source_url": t.source_url,
                    "quiet_share_url": t.quiet_share_url,
                    "comment": t.comment,
                }
                for i, t in enumerate(digest.topics, 1)
            ],
            "post_idea_counts": {k: len(v) for k, v in digest.post_ideas.items()},
            "draft_source": digest.draft_source,
            "collection": collection,
            "cost_usd": round(total_cost, 4),
            "budget_usd": budget,
            "search_tool_calls": tool_calls,
            "email_status": email_status,
            "duration_s": round(digest.duration_s, 1),
            "warnings": warnings,
            "artifacts_dir": str(run_dir),
        }
        Path(args.json_summary).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    _log(
        f"done: {len(digest.topics)}/{max_topics} topics, xAI cost ${total_cost:.4f} "
        f"(budget ${budget:.2f}), {digest.duration_s:.0f}s, artifacts in {run_dir}"
    )
    if digest.duration_s > 300:
        _log("WARN: run exceeded 5 minutes (non-functional requirement)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    server = make_server(settings, host=args.host, port=args.port)
    host, port = server.server_address[:2]
    _log(f"digest page serving on http://{host}:{port}/ (token required)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    profile = load_profile(args.profile)
    prune(settings, int(profile.get("retention_days", 30)), log=_log)
    return 0


def cmd_send(args) -> int:
    """Re-send an existing digest .eml through the configured SMTP bridge.

    No collection, no xAI spend: this is the deploy-time email verification
    step and a free replay of any past digest.
    """
    import smtplib
    from email import policy as email_policy
    from email.parser import BytesParser

    settings = Settings.from_env()
    if not settings.smtp_host:
        _log("SMTP_HOST not set; nothing to send (see RUNBOOK.md, email via Resend)")
        return 2
    runs_dir = Path(settings.data_dir) / "digests"
    tag = args.run_tag
    if not tag:
        candidates = sorted((p.name for p in runs_dir.iterdir() if p.is_dir()), reverse=True) if runs_dir.exists() else []
        if not candidates:
            _log(f"no digest runs under {runs_dir}; run `python -m digest run` first")
            return 2
        tag = candidates[0]
        _log(f"no --run-tag given; using latest run: {tag}")
    eml_path = runs_dir / tag / "digest.eml"
    if not eml_path.exists():
        _log(f"digest.eml not found: {eml_path}")
        return 2

    message = BytesParser(policy=email_policy.default).parsebytes(eml_path.read_bytes())
    if settings.smtp_from and str(message.get("From", "")) != settings.smtp_from:
        del message["From"]
        message["From"] = settings.smtp_from
        _log(f"From set to {settings.smtp_from} (from current SMTP_FROM, overriding artifact)")
    try:
        status = send_email(message, settings, dry_run=False, log=_log)
    except smtplib.SMTPException as exc:
        _log(f"send failed: {exc}")
        return 1
    except OSError as exc:
        _log(f"send failed: {exc}")
        return 1
    _log(f"send result: {status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="digest", description="Social feed digest worker")
    subs = parser.add_subparsers(dest="command", required=True)

    run_p = subs.add_parser("run", help="collect, rank, draft, render, deliver")
    run_p.add_argument("--dry-run", action="store_true", help="collect and render live but do not send email")
    run_p.add_argument("--skip-portfolio", action="store_true", help="skip the batched portfolio watchlist sweep")
    run_p.add_argument("--profile", default="profile.yaml")
    run_p.add_argument("--data-dir", default=None)
    run_p.add_argument("--json-summary", default=None, help="write a machine-readable run summary to this path")

    serve_p = subs.add_parser("serve", help="serve the private digest page")
    serve_p.add_argument("--host", default="0.0.0.0")
    serve_p.add_argument("--port", type=int, default=8787)

    prune_p = subs.add_parser("prune", help="delete digest artifacts older than retention_days")
    prune_p.add_argument("--profile", default="profile.yaml")

    send_p = subs.add_parser(
        "send", help="re-send an existing digest .eml via the SMTP bridge (no re-collection)"
    )
    send_p.add_argument("--run-tag", default=None, help="digest run tag; defaults to the latest")

    args = parser.parse_args(argv)
    handlers = {"run": cmd_run, "serve": cmd_serve, "prune": cmd_prune, "send": cmd_send}
    try:
        return handlers[args.command](args)
    except ConfigError as exc:
        _log(f"config error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
