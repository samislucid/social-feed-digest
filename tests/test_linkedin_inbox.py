from __future__ import annotations

import json

from digest.collect.linkedin_inbox import collect_linkedin


def test_collects_json_and_markdown_batches(settings):
    inbox = settings.data_dir / "inbox" / "linkedin"
    inbox.mkdir(parents=True)
    (inbox / "batch1.json").write_text(
        json.dumps(
            [
                {
                    "title": "Post about AI ops tooling",
                    "url": "https://www.linkedin.com/feed/update/1/",
                    "summary": "Ops teams adopting agents",
                    "author": "connection A",
                }
            ]
        ),
        encoding="utf-8",
    )
    (inbox / "batch2.md").write_text(
        "# Voice note on agent evals\n\n"
        "[Original post](https://www.linkedin.com/feed/update/2/)\n\n"
        "Interesting thread on eval harnesses.\n",
        encoding="utf-8",
    )

    items = collect_linkedin(settings, {"linkedin": {"inbox": "inbox/linkedin"}})

    assert len(items) == 2
    assert all(i.channel == "linkedin" for i in items)
    assert {i.url for i in items} == {"https://www.linkedin.com/feed/update/1/"} | {
        "https://www.linkedin.com/feed/update/2/"
    }
    # processed files move out of the inbox so nothing is ingested twice
    assert not list(inbox.glob("*.json"))
    assert not list(inbox.glob("*.md"))
    assert len(list((inbox / "processed").iterdir())) == 2


def test_missing_inbox_is_not_an_error(settings):
    items = collect_linkedin(settings, {"linkedin": {"inbox": "inbox/linkedin"}})
    assert items == []
