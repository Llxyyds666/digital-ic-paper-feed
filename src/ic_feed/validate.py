"""Read-only cross-file validation before RSS deployment."""

import argparse
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

from ic_feed.config import AppConfig, load_config
from ic_feed.focus import load_focus_overrides, render_focus_feeds
from ic_feed.normalize import group_records, record_key
from ic_feed.publication import cumulative_records, is_repository_artifact, load_withheld_aliases
from ic_feed.render import render_rss
from ic_feed.state import FeedState, load_state
from ic_feed.venues import match_venue


def _items(contents: str, name: str) -> dict[str, tuple[str, str]]:
    root = ET.fromstring(contents)
    if root.tag != "rss" or root.get("version") != "2.0" or root.find("channel") is None:
        raise ValueError(f"invalid RSS channel in {name}")
    result = {}
    for item in root.findall("channel/item"):
        guid, title = item.findtext("guid"), item.findtext("title")
        if not guid or not title or not item.findtext("link") or not item.findtext("pubDate"):
            raise ValueError(f"incomplete RSS item in {name}")
        if guid in result:
            raise ValueError(f"duplicate GUID in {name}")
        result[guid] = (title, item.findtext("description") or "")
    return result


def validate_publication(
    output_dir: Path,
    state: FeedState,
    config: AppConfig,
    withheld_aliases: set[str],
    overrides: dict[str, frozenset[str]],
) -> dict[str, int]:
    if len(group_records(list(state.papers.values()))) != len(state.papers):
        raise ValueError("duplicate publisher identity in state")
    for key, record in state.papers.items():
        if record_key(record) != key:
            raise ValueError("state identity mismatch")
        if match_venue(record) is None or is_repository_artifact(record):
            raise ValueError(f"unapproved venue or repository artifact in state: {key}")
    selected = cumulative_records(state, withheld_aliases)
    expected = render_focus_feeds(state, config, withheld_aliases, overrides)
    expected["ai_summary_feed.xml"] = render_rss(
        [replace(record, abstract=record.summary_zh or "") for record in selected],
        config.publication.title, config.publication.base_url, len(selected), cap_at_2000=False,
    )
    expected["filtered_feed.xml"] = render_rss(
        list(state.papers.values()), config.publication.title, config.publication.base_url,
        config.collection.raw_feed_max_items,
    )
    counts = {}
    for name, contents in expected.items():
        actual = _items((output_dir / name).read_text(encoding="utf-8"), name)
        canonical = _items(contents, name)
        if actual.keys() != canonical.keys():
            raise ValueError(f"state / RSS membership mismatch in {name}")
        for guid, (title, summary) in canonical.items():
            if actual[guid][0] != title:
                raise ValueError(f"title mismatch in {name}: {guid}")
            # Abstract enrichment updates state during summarization, not the raw RSS.
            if name != "filtered_feed.xml" and actual[guid][1] != summary:
                raise ValueError(f"summary mismatch in {name}: {guid}")
        counts[name] = len(actual)
    html = (output_dir / "ai_summary.html").read_text(encoding="utf-8")
    if not html.strip() or "<html" not in html.lower():
        raise ValueError("missing digest HTML")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    parser.add_argument("--state", type=Path, default=Path("state.json"))
    parser.add_argument("--config", type=Path, default=Path("paper_feed_config.json"))
    args = parser.parse_args(argv)
    counts = validate_publication(
        args.output_dir, load_state(args.state), load_config(args.config),
        load_withheld_aliases(Path("config/ai_publication.json")),
        load_focus_overrides(Path("config/focus_overrides.json")),
    )
    print("validated " + " ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
