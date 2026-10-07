from datetime import datetime, timezone
import importlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.parse import parse_qs, urlparse

import pytest

from ic_feed.enrich import AbstractEnricher, MetadataResponse
from ic_feed.models import PaperRecord
from ic_feed.normalize import record_key
from ic_feed.state import FeedState, load_state, save_state


JOURNAL = "IEEE Transactions on Computer-Aided Design of Integrated Circuits and Systems"
TITLE = "An RTL Controller With Assertion-Based Formal Verification"
DOI = "10.1109/tcad.2026.1234567"


def paper(**changes):
    fields = dict(title=TITLE, abstract="", authors=[], journal=JOURNAL,
                  published_at=datetime(2026, 10, 7, tzinfo=timezone.utc), doi=None,
                  url="https://ieeexplore.ieee.org/document/1234567", sources=["rss"], source_ids=["1234567"])
    fields.update(changes)
    return PaperRecord(**fields)


def article(**changes):
    fields = {"article_number": "1234567", "title": TITLE, "publication_title": JOURNAL,
              "doi": DOI, "authors": {"authors": [{"full_name": "A. Author"}, {"full_name": "B. Author"}]},
              "abstract": "We implement an RTL controller and prove safety assertions using formal verification."}
    fields.update(changes)
    return fields


def response(articles):
    return MetadataResponse(200, json.dumps({"articles": articles}).encode("utf-8"))


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body, timeout))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def client(transport, key="test-ieee-secret", **kwargs):
    return importlib.import_module("ic_feed.ieee").IeeeEnricher(key, transport=transport, wait=lambda _: None, **kwargs)


def state_for(record):
    key = record_key(record)
    return FeedState(papers={key: record}, pending_ai=[key]), key


def test_ieee_article_number_fills_missing_metadata_and_rekeys_pending_identity(tmp_path):
    record = paper()
    state, old_key = state_for(record)
    transport = Transport([response([article()])])
    stats = client(transport).enrich(state, [old_key, old_key])
    query = parse_qs(urlparse(transport.calls[0][1]).query)
    assert query["article_number"] == ["1234567"]
    assert query["apikey"] == ["test-ieee-secret"]
    assert query["start_record"] == ["1"]
    assert query["max_records"] == ["5"]
    assert transport.calls[0][0] == "GET"
    assert stats.queried == stats.enriched == stats.metadata_enriched == 1
    assert stats.key_mapping[old_key] == f"doi:{DOI}"
    assert set(state.papers) == {f"doi:{DOI}"}
    assert state.pending_ai == [f"doi:{DOI}"]
    updated = state.papers[f"doi:{DOI}"]
    assert updated.authors == ["A. Author", "B. Author"]
    assert updated.abstract.startswith("We implement")
    assert updated.sources == ["rss", "ieee"]
    path = tmp_path / "state.json"
    save_state(path, state)
    assert load_state(path).pending_ai == [f"doi:{DOI}"]


def test_known_ieee_doi_queries_by_doi_when_no_xplore_article_number():
    record = paper(doi=DOI, url=f"https://doi.org/{DOI}")
    state, key = state_for(record)
    transport = Transport([response([article()])])
    client(transport).enrich(state, [key])
    query = parse_qs(urlparse(transport.calls[0][1]).query)
    assert query["doi"] == [DOI]
    assert "article_number" not in query


@pytest.mark.parametrize("changes", [
    {"doi": "10.1109/tcad.2026.wrong"},
    {"title": "Extended Evaluation of An RTL Controller With Assertion-Based Formal Verification"},
    {"article_number": "9999999"},
    {"publication_title": "IEEE Journal of Solid-State Circuits"},
    {"title": None},
])
def test_ieee_rejects_wrong_identity_title_article_number_or_venue(changes):
    record = paper(doi=DOI)
    state, key = state_for(record)
    original = record.to_dict()
    stats = client(Transport([response([article(**changes)])])).enrich(state, [key])
    assert stats.enriched == stats.metadata_enriched == 0
    assert record.to_dict() == original


def test_unknown_doi_rejects_non_ieee_doi_and_ambiguous_api_results():
    for articles in ([article(doi="10.1145/123.456")], [article(), article()]):
        record = paper()
        state, key = state_for(record)
        stats = client(Transport([response(articles)])).enrich(state, [key])
        assert stats.metadata_enriched == 0
        assert record.doi is None and record.abstract == ""


def test_ieee_normalized_exact_title_accepts_punctuation_case_variants():
    record = paper()
    state, key = state_for(record)
    stats = client(Transport([response([article(title="AN RTL CONTROLLER: with assertion based FORMAL verification!")])])).enrich(state, [key])
    assert stats.metadata_enriched == 1


def test_ieee_never_overwrites_existing_abstract_authors_or_ai_fields():
    record = paper(abstract="A short real RTL abstract.", authors=["Original Author"], ai_relevant=True,
                   ai_confidence=0.83, summary_zh="已有中文精选。", categories=["rtl-microarchitecture", "digital-design"])
    state, key = state_for(record)
    client(Transport([response([article()])])).enrich(state, [key])
    updated = state.papers[f"doi:{DOI}"]
    assert updated.abstract == "A short real RTL abstract."
    assert updated.authors == ["Original Author"]
    assert (updated.ai_relevant, updated.ai_confidence, updated.summary_zh, updated.categories) == (True, 0.83, "已有中文精选。", ["rtl-microarchitecture", "digital-design"])


def test_complete_ieee_metadata_and_missing_key_do_not_request_again():
    for api_key, record in [("test-ieee-secret", paper(doi=DOI, abstract="We prove RTL safety.", authors=["A. Author"])), (None, paper())]:
        state, key = state_for(record)
        transport = Transport([])
        stats = client(transport, key=api_key).enrich(state, [key])
        assert transport.calls == []
        assert stats.queried == stats.metadata_enriched == 0


@pytest.mark.parametrize("record", [
    paper(url="https://ieeexplore.ieee.org.evil.invalid/document/1234567"),
    paper(url="https://example.com/document/1234567"),
    paper(journal="Design, Automation & Test in Europe Conference & Exhibition"),
    paper(doi="10.1145/123.456", url="https://doi.org/10.1145/123.456"),
])
def test_ieee_only_queries_ieee_identity_and_approved_venue(record):
    state, key = state_for(record)
    transport = Transport([])
    stats = client(transport).enrich(state, [key])
    assert transport.calls == []
    assert stats.queried == 0


def test_ieee_retries_transient_errors_and_never_logs_api_key_or_url(capsys):
    record = paper()
    state, key = state_for(record)
    transport = Transport([MetadataResponse(503, b'busy'), response([article()])])
    assert client(transport).enrich(state, [key]).metadata_enriched == 1
    assert len(transport.calls) == 2
    another = paper()
    state, key = state_for(another)
    denied = Transport([MetadataResponse(401, b'test-ieee-secret https://bad.test')])
    assert client(denied).enrich(state, [key]).metadata_enriched == 0
    assert len(denied.calls) == 1
    output = capsys.readouterr()
    assert "ieee HTTPError 401" in output.err
    assert "test-ieee-secret" not in output.err + output.out
    assert "https://" not in output.err + output.out


def test_ieee_doi_collision_keeps_existing_processed_record_and_does_not_requeue():
    incoming = paper()
    prior = paper(doi=DOI, abstract="Previously retrieved RTL abstract.", authors=["Known Author"],
                  ai_relevant=True, ai_confidence=0.98, summary_zh="已处理的历史摘要。", categories=["simulation-uvm", "digital-verification"])
    old_key, canonical_key = record_key(incoming), record_key(prior)
    state = FeedState(papers={old_key: incoming, canonical_key: prior}, pending_ai=[old_key])
    client(Transport([response([article()])])).enrich(state, [old_key])
    assert set(state.papers) == {canonical_key}
    assert state.pending_ai == []
    assert state.papers[canonical_key].categories == prior.categories
    assert state.papers[canonical_key].summary_zh == prior.summary_zh
    assert state.papers[canonical_key].ai_relevant is True


def test_ieee_runs_before_openaire_and_uses_remapped_doi_keys_for_fallback():
    record = paper()
    state, old_key = state_for(record)
    original = "OpenAIRE returns a substantive original RTL abstract."
    transport = Transport([
        response([article(abstract="null")]),
        MetadataResponse(200, json.dumps({"results": [{"title": TITLE, "pids": [{"scheme": "doi", "value": DOI}], "descriptions": [original]}]}).encode('utf-8')),
    ])
    stats = AbstractEnricher(None, ieee_key="test-ieee-secret", transport=transport, wait=lambda _: None).enrich(state, [old_key])
    assert urlparse(transport.calls[0][1]).hostname == "ieeexploreapi.ieee.org"
    assert urlparse(transport.calls[1][1]).hostname == "api.openaire.eu"
    assert stats.ieee_queried == stats.metadata_enriched == 1
    assert stats.enriched == 1
    assert state.papers[f"doi:{DOI}"].abstract == original


def test_ieee_accepts_one_exact_identity_and_ignores_other_hits():
    record = paper()
    state, key = state_for(record)
    stats = client(Transport([response([article(article_number="999", title="Different RTL study"), article()])])).enrich(state, [key])
    assert stats.metadata_enriched == 1
    assert state.papers[f"doi:{DOI}"].abstract.startswith("We implement")


def test_exact_article_identity_can_fill_abstract_authors_without_returned_doi():
    record = paper()
    state, key = state_for(record)
    stats = client(Transport([response([article(doi=None)])])).enrich(state, [key])
    assert stats.metadata_enriched == stats.enriched == 1
    assert set(state.papers) == {key}
    assert state.papers[key].doi is None
    assert state.papers[key].authors == ["A. Author", "B. Author"]


def test_ieee_retryable_invalid_json_attempts_three_times_without_mutation(capsys):
    record = paper()
    state, key = state_for(record)
    transport = Transport([MetadataResponse(200, b'not-json')] * 3)
    assert client(transport).enrich(state, [key]).metadata_enriched == 0
    assert len(transport.calls) == 3
    assert record.doi is None and record.abstract == ""
    assert "test-ieee-secret" not in capsys.readouterr().err


def test_ieee_default_transport_never_redirects_credential_query(monkeypatch, capsys):
    ieee = importlib.import_module("ic_feed.ieee")
    target_requests = []
    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            target_requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"articles":[]}')
        def log_message(self, *args):
            pass
    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    target_thread = Thread(target=target.serve_forever, daemon=True)
    target_thread.start()
    target_url = f"http://127.0.0.1:{target.server_address[1]}/credential-leak-target"
    class Source(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target_url)
            self.end_headers()
        def log_message(self, *args):
            pass
    source = ThreadingHTTPServer(("127.0.0.1", 0), Source)
    source_thread = Thread(target=source.serve_forever, daemon=True)
    source_thread.start()
    monkeypatch.setattr(ieee, "IEEE_SEARCH_URL", f"http://127.0.0.1:{source.server_address[1]}/search")
    record = paper()
    state, key = state_for(record)
    try:
        stats = ieee.IeeeEnricher("test-ieee-secret", timeout_seconds=2, wait=lambda _: None).enrich(state, [key])
    finally:
        for server, thread in ((source, source_thread), (target, target_thread)):
            server.shutdown()
            server.server_close()
            thread.join()
    assert stats.metadata_enriched == 0
    assert target_requests == []
    output = capsys.readouterr()
    assert "test-ieee-secret" not in output.err + output.out
    assert "credential-leak-target" not in output.err + output.out
