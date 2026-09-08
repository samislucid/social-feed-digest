"""Regenerate the samples/ previews from one demo digest.

The email sample mirrors the hand-drawn mock (app://files/file_oTCbyaOw2gPUVhMS),
built from the healthy 2026-09-07_1931 VPS run: one X story with an engagement
suggestion and three account drafts, Reddit "best 5 of 11" with three comments
and two read-only threads, an empty LinkedIn watched-inbox note, and two web
context items. Sections are constructed directly (no ranker) so the sample is
stable regardless of profile keyword tuning. The page sample renders the same
digest with the unchanged page design.

Run:  .venv/bin/python tools/gen_samples.py   (from the repo root)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from digest.render import Digest, render_email_html, render_html, render_markdown  # noqa: E402
from digest.shape import Engagement, Notable, Section  # noqa: E402

RUN_TAG = "2026-09-07_1931"
GENERATED_AT = datetime(2026, 9, 8, 2, 31, tzinfo=timezone.utc)


def _sections() -> list[Section]:
    x_n1 = Notable(
        index=1,
        title="ALO Yoga names Wang Yibo first global ambassador for China entry",
        url="https://x.com/wybsources/status/1",
        author="@wybsources",
        why=(
            "A brand spending its top-tier ambassador title on one market says "
            "the China consumer opportunity is being priced above the global one, "
            "and the fan-source amplification shows the distribution comes pre-built."
        ),
        niche="asian-economics",
        posted_at=GENERATED_AT,  # renders as 7:31 PM Pacific
    )
    return [
        Section(
            channel="x",
            summary=(
                "One consumer-brand China entry and one token-cost thread ran on X "
                "this window."
            ),
            notable=[x_n1],
            drafts=[
                (
                    "Three agent threads on my feed land on the same point: a rule in "
                    "a prompt is a request, not a constraint. State kept in a machine "
                    "outside the model, a critic that owns the tests, a hidden checker "
                    "comparing what the agent said to what it did. The reliability work "
                    "is all outside the model."
                ),
                (
                    "Teams swapping Claude for cheaper models to cut spend are treating "
                    "the model as the variable. The harness is the variable. Tool-calling "
                    "discipline and honesty about \"done\" differ per model, so a gateway "
                    "swap tends to move the cost into review time rather than remove it."
                ),
                (
                    "A planner routing between domain agents works until one agent reports "
                    "success it cannot back up. Hospital ops, billing, CI: same failure "
                    "shape. We build Brightstack on the assumption that every agent step is "
                    "reviewed against its trail, because self-reported \"done\" is not evidence."
                ),
            ],
            engagements=[
                Engagement(
                    index=1,
                    title=x_n1.title,
                    url=x_n1.url,
                    author="@wybsources",
                    action="comment",
                    comment=(
                        "First global ambassador assigned for a China entry rather than "
                        "a global campaign is the detail worth noting. The brand is buying "
                        "an existing distribution network in one country, not a worldwide face."
                    ),
                )
            ],
        ),
        Section(
            channel="linkedin",
            empty_note=(
                "No LinkedIn posts this run. LinkedIn arrives through the watched inbox "
                "(Bright-assisted input); nothing reached it this run."
            ),
        ),
        Section(
            channel="reddit",
            intro="best 5 of 11 Reddit posts this run",
            summary=(
                "Agentic reliability and harness comparisons dominate the subreddit "
                "this window."
            ),
            notable=[
                Notable(
                    index=1,
                    title=(
                        "Solving Agentic Amnesia: Why I stopped relying on prompt "
                        "engineering and built a local state machine for Claude Code."
                    ),
                    url="https://reddit.com/r/LLMDevs/1",
                    author="/u/SnooComics4579",
                    why="",
                    source_label="r/LLMDevs",
                    niche="ai-core",
                ),
                Notable(
                    index=2,
                    title=(
                        "Loop engineering: I turned the Ralph loop into a verified one. "
                        "One markdown file, any agent, a critic before \"done\"."
                    ),
                    url="https://reddit.com/r/LLMDevs/2",
                    author="/u/raiyanyahya",
                    why="",
                    source_label="r/LLMDevs",
                    niche="ai-core",
                ),
                Notable(
                    index=3,
                    title=(
                        "I tested Claude Code, Codex, Gemini and open source models "
                        "through OpenCode, and compared what each did to what it said it did"
                    ),
                    url="https://reddit.com/r/LLMDevs/3",
                    author="/u/tap3k",
                    why="",
                    source_label="r/LLMDevs",
                    niche="ai-core",
                ),
                Notable(
                    index=4,
                    title="Exploring different agentic harness options",
                    url="https://reddit.com/r/LLMDevs/4",
                    author="/u/Gonjanaenae319",
                    why="",
                    source_label="r/LLMDevs",
                    niche="ai-core",
                ),
                Notable(
                    index=5,
                    title="Can We Trust Agents to Run Hospitals?",
                    url="https://reddit.com/r/LLMDevs/5",
                    author="/u/---starboy--",
                    why="",
                    source_label="r/LLMDevs",
                    niche="ai-core",
                ),
            ],
            engagements=[
                Engagement(
                    index=1,
                    title=(
                        "Solving Agentic Amnesia: Why I stopped relying on prompt "
                        "engineering and built a local state machine for Claude Code."
                    ),
                    url="https://reddit.com/r/LLMDevs/1",
                    author="/u/SnooComics4579",
                    action="comment",
                    comment=(
                        "Anything you actually need enforced has to sit in the code that "
                        "decides what runs next, not in the text the model reads. What "
                        "happens when the agent edits files the machine has no transition "
                        "for, so the recorded state and the repo disagree?"
                    ),
                ),
                Engagement(
                    index=2,
                    title=(
                        "Loop engineering: I turned the Ralph loop into a verified one. "
                        "One markdown file, any agent, a critic before \"done\"."
                    ),
                    url="https://reddit.com/r/LLMDevs/2",
                    author="/u/raiyanyahya",
                    action="comment",
                    comment=(
                        "The constraint that matters is stopping the agent from editing "
                        "the tests that judge it. How does your critic get its view of the "
                        "tests, a separate checkout or a hash checked before the run?"
                    ),
                ),
                Engagement(
                    index=3,
                    title=(
                        "I tested Claude Code, Codex, Gemini and open source models "
                        "through OpenCode, and compared what each did to what it said it did"
                    ),
                    url="https://reddit.com/r/LLMDevs/3",
                    author="/u/tap3k",
                    action="comment",
                    comment=(
                        "Splitting capability from honesty is the useful part of this "
                        "design. Would like the numbers broken out by failure type: "
                        "shortcut taken and admitted versus shortcut taken and reported "
                        "as clean."
                    ),
                ),
            ],
        ),
        Section(
            channel="web",
            summary=(
                "Two context items worth knowing: a frontier-model pricing move and "
                "a following-list sync detail."
            ),
            notable=[
                Notable(
                    index=1,
                    title="Frontier API prices cut as open models close the gap",
                    url="https://example.com/news/1",
                    author="example.com",
                    why="",
                ),
                Notable(
                    index=2,
                    title="Following-list syncs: what platforms charge per read",
                    url="https://example.com/news/2",
                    author="example.com",
                    why="",
                ),
            ],
        ),
    ]


def _digest() -> Digest:
    return Digest(
        run_tag=RUN_TAG,
        generated_at=GENERATED_AT,
        profile_name="Sam Kim",
        sections=_sections(),
        draft_source="claude",
        collection={"x": 10, "reddit": 50, "linkedin": 0, "web": 2},
        cost={"search_tool_calls": 5, "total_usd": 0.1077, "budget_usd": 0.25, "calls": []},
        email_to="samislucid98@gmail.com",
        subject_prefix="[Feed Digest]",
        duration_s=214.0,
        warnings=[],
        claude_error="",
    )


def main() -> None:
    digest = _digest()
    out = ROOT / "samples"
    out.mkdir(exist_ok=True)
    (out / "digest-shape-sample.md").write_text(render_markdown(digest), encoding="utf-8")
    (out / "digest-shape-sample.html").write_text(render_email_html(digest), encoding="utf-8")
    (out / "digest-page-sample.html").write_text(render_html(digest), encoding="utf-8")
    print(f"wrote 3 sample files to {out}")


if __name__ == "__main__":
    main()
