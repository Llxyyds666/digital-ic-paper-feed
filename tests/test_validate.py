from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ic_feed.config import load_config
from ic_feed.focus import render_focus_feeds
from ic_feed.models import PaperRecord
from ic_feed.normalize import record_key
from ic_feed.render import render_rss
from ic_feed.state import FeedState, save_state
from ic_feed.summarize import render_publication


ROOT = Path(__file__).parents[1]


def test_publication_validator_exists():
    assert importlib.util.find_spec("ic_feed.validate") is not None


def publication(tmp_path, categories=None):
    config = load_config(ROOT / "paper_feed_config.json")
    record = PaperRecord(
        title="SystemVerilog assertions for RISC-V hardware verification",
        abstract="A hardware verification method.", authors=["A. Author"],
        journal="IEEE Transactions on Computers",
        published_at=datetime(2026, 10, 7, tzinfo=timezone.utc),
        doi="10.1109/tc.2026.123", url="https://doi.org/10.1109/tc.2026.123",
        sources=["crossref"], source_ids=[],
        categories=categories or ["digital-verification", "formal-assertions-equivalence"],
        summary_zh="提出硬件验证方法，摘要未提供量化结果。", ai_relevant=True,
        ai_confidence=0.9,
    )
    state = FeedState(papers={record_key(record): record})
    save_state(tmp_path / "state.json", state)
    rss, html = render_publication(state, config, "2026-10-07", None, set(), new_count=1)
    (tmp_path / "ai_summary_feed.xml").write_text(rss, encoding="utf-8")
    (tmp_path / "ai_summary.html").write_text(html, encoding="utf-8")
    (tmp_path / "filtered_feed.xml").write_text(
        render_rss([record], config.publication.title, config.publication.base_url, 2000),
        encoding="utf-8",
    )
    for name, contents in render_focus_feeds(state, config, set(), {}).items():
        (tmp_path / name).write_text(contents, encoding="utf-8")
    return config, state


def test_validator_accepts_consistent_publication(tmp_path):
    from ic_feed.validate import validate_publication
    config, state = publication(tmp_path)
    counts = validate_publication(tmp_path, state, config, set(), {})
    assert counts["ai_summary_feed.xml"] == 1
    assert counts["design_feed.xml"] == 0
    assert counts["verification_feed.xml"] == 1


def test_validator_rejects_duplicate_rss_guids(tmp_path):
    from ic_feed.validate import validate_publication
    config, state = publication(tmp_path)
    path = tmp_path / "ai_summary_feed.xml"
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    channel = root.find("channel")
    channel.append(ET.fromstring(ET.tostring(channel.find("item"))))
    path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate GUID"):
        validate_publication(tmp_path, state, config, set(), {})


def test_validator_rejects_stale_or_wrong_topic_feed(tmp_path):
    from ic_feed.validate import validate_publication
    config, state = publication(tmp_path)
    (tmp_path / "design_feed.xml").write_text(
        (tmp_path / "verification_feed.xml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="design_feed.xml"):
        validate_publication(tmp_path, state, config, set(), {})


def test_validator_rejects_state_outside_venue_whitelist(tmp_path):
    from ic_feed.validate import validate_publication
    config, state = publication(tmp_path)
    key = next(iter(state.papers))
    state.papers[key] = replace(state.papers[key], journal="IEEE Access")
    with pytest.raises(ValueError, match="venue"):
        validate_publication(tmp_path, state, config, set(), {})


def test_validator_rejects_tampered_chinese_summary(tmp_path):
    from ic_feed.validate import validate_publication
    config, state = publication(tmp_path)
    path = tmp_path / "ai_summary_feed.xml"
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    root.find("channel/item/description").text = "unexpected summary"
    path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    with pytest.raises(ValueError, match="summary"):
        validate_publication(tmp_path, state, config, set(), {})


def test_validator_rejects_duplicate_publisher_identity_despite_distinct_guids(tmp_path):
    from ic_feed.validate import validate_publication
    config, state = publication(tmp_path)
    key = next(iter(state.papers))
    state.papers[key].source_ids = ["https://ieeexplore.ieee.org/document/123456"]
    duplicate = replace(state.papers[key], doi=None,
                        url="http://ieeexplore.ieee.org/document/123456")
    state.papers[record_key(duplicate)] = duplicate
    with pytest.raises(ValueError, match="duplicate publisher identity"):
        validate_publication(tmp_path, state, config, set(), {})
