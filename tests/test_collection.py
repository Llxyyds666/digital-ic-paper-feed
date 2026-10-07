from datetime import date, datetime, timezone
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from ic_feed import collect
from ic_feed.http import HttpResult
from ic_feed.sources import arxiv, crossref
from ic_feed.state import FeedState, SourceContinuation, load_state, save_state


def test_crossref_journal_query_uses_issn_endpoint():
    url = crossref.build_url("journal:0278-0070", date(2026, 9, 7), 100)
    assert urlparse(url).path == "/journals/0278-0070/works"
    assert "query.bibliographic" not in parse_qs(urlparse(url).query)


def test_crossref_conference_search_sorts_by_relevance_not_index_timestamp():
    url = crossref.build_url("venue:Design Automation Conference", date(2026, 9, 7), 100)
    parameters = parse_qs(urlparse(url).query)
    assert parameters["sort"] == ["score"]
    assert parameters["order"] == ["desc"]


def test_crossref_exact_container_uses_filter_not_fuzzy_query():
    url = crossref.build_url("container:Computer Aided Verification", date(2026, 9, 7), 100)
    parameters = parse_qs(urlparse(url).query)
    assert "container-title:Computer Aided Verification" in parameters["filter"][0]
    assert "query.container-title" not in parameters
    assert "query.bibliographic" not in parameters


def test_arxiv_structured_query_keeps_category_and_boolean_operators():
    url = arxiv.build_url("cat:cs.AR AND all:Verilog", date(2026, 9, 7), 100)
    query = parse_qs(urlparse(url).query)["search_query"][0]
    assert '(cat:cs.AR AND all:Verilog)' in query
    assert 'all:"cat:' not in query


def test_independent_query_cursors_roundtrip(tmp_path):
    state = FeedState(source_continuations={
        "crossref:verilog": SourceContinuation(date(2026, 9, 7), "opaque-1"),
        "crossref:hardware-verification": SourceContinuation(date(2026, 9, 8), "opaque-2"),
    })
    path = tmp_path / "state.json"
    save_state(path, state)
    assert load_state(path).source_continuations == state.source_continuations


def test_collection_keeps_failed_query_watermark_and_advances_successful_query(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    sources = tmp_path / "sources.tsv"
    sources.write_text("name\turl\n", encoding="utf-8")
    queries = tmp_path / "scholarly.json"
    queries.write_text(json.dumps({"version": 1, "queries": [
        {"source": "crossref", "id": "one", "query": "Verilog", "limit": 10},
        {"source": "crossref", "id": "two", "query": "hardware verification", "limit": 10},
    ]}), encoding="utf-8")
    state_path = tmp_path / "state.json"
    old_watermark = "2026-09-07T00:00:00+00:00"
    save_state(state_path, FeedState(source_watermarks={"crossref:two": old_watermark}))

    def fetch(url, *_):
        query = parse_qs(urlparse(url).query)["query.bibliographic"][0]
        if query == "hardware verification":
            return HttpResult(b"unavailable", 503, url)
        return HttpResult(b'{"message":{"items":[]}}', 200, url)

    monkeypatch.setattr(collect, "fetch_bytes", fetch)
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    result = collect.main([
        "--config", str(root / "paper_feed_config.json"), "--state", str(state_path),
        "--sources", str(sources), "--queries", str(root / "config/queries.json"),
        "--scholarly-queries", str(queries), "--feed", str(tmp_path / "feed.xml"),
        "--failures", str(tmp_path / "failures.tsv"),
    ], now=lambda: now)

    assert result == 0
    state = load_state(state_path)
    assert state.source_watermarks["crossref:one"] == now.isoformat()
    assert state.source_watermarks["crossref:two"] == old_watermark
    assert "http_503" in (tmp_path / "failures.tsv").read_text(encoding="utf-8")


def test_query_configuration_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "queries.json"
    entry = {"source": "crossref", "id": "rtl", "query": "Verilog", "limit": 10}
    path.write_text(json.dumps({"version": 1, "queries": [entry, entry]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        collect._read_scholarly_queries(path)
