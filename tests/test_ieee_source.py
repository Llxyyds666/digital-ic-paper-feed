import importlib
import json
import traceback
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from ic_feed.enrich import MetadataResponse
from ic_feed.normalize import identity_aliases


KEY = "secret-fixture-key"


def article(**changes):
    item = {
        "title": "A Digital Circuit",
        "publication_title": "IEEE Journal of Solid-State Circuits",
        "publication_date": "15 March 2026",
        "abstract": "An original description of the digital circuit architecture.",
        "authors": {"authors": [{"full_name": "Ada Lovelace"}]},
        "doi": "https://doi.org/10.1109/JSSC.2026.123",
        "article_number": "123456",
        "html_url": "https://ieeexplore.ieee.org/document/123456/",
        "content_type": "Early Access Articles",
    }
    item.update(changes)
    return item


def response(payload, status=200):
    return MetadataResponse(status, json.dumps(payload).encode())


def invoke(results, **changes):
    ieee = importlib.import_module("ic_feed.sources.ieee")
    calls = []
    waits = []
    iterator = iter(results)

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, headers, body, timeout))
        result = next(iterator)
        if isinstance(result, Exception):
            raise result
        return result

    kwargs = dict(query="journal:0018-9200", from_date=date(2026, 1, 1),
                  until_date=date(2026, 10, 7), start_record=201, max_records=200,
                  key=KEY, transport=transport, wait=waits.append)
    kwargs.update(changes)
    return ieee, calls, waits, lambda: ieee.request_page(**kwargs)


def test_journal_early_access_and_explicit_paging_date_window():
    _, calls, _, run = invoke([response({"total_records": 300, "articles": [article()]})])
    page = run()
    method, url, headers, body, timeout = calls[0]
    assert method == "GET" and body is None and timeout == 20
    assert urlsplit(url).path == "/api/v1/search/articles"
    assert parse_qs(urlsplit(url).query) == {
        "apikey": [KEY], "format": ["json"], "issn": ["0018-9200"],
        "start_record": ["201"], "max_records": ["200"],
        "sort_field": ["article_number"], "sort_order": ["asc"],
        "start_date": ["20260101"], "end_date": ["20261007"],
    }
    assert page.total_records == 300 and page.item_count == 1
    assert len(page.records) == 1


def test_conference_year_and_title_are_not_doi_identity_filters():
    _, calls, _, run = invoke([response({"total_records": 0})],
                              query="conference:2026:Design Automation Conference: Main")
    run()
    params = parse_qs(urlsplit(calls[0][1]).query)
    assert params["publication_title"] == ["Design Automation Conference: Main"]
    assert params["publication_year"] == ["2026"]
    assert params["content_type"] == ["Conferences"]
    assert not {"doi", "article_number", "issn"} & params.keys()


def test_parses_metadata_and_preserves_publisher_identity():
    _, _, _, run = invoke([response({"total_records": 1, "articles": [article()]})])
    record = run().records[0]
    assert record.title == "A Digital Circuit"
    assert record.journal == "IEEE Journal of Solid-State Circuits"
    assert record.abstract == article()["abstract"]
    assert record.authors == ["Ada Lovelace"]
    assert record.doi == "10.1109/jssc.2026.123"
    assert record.published_at == datetime(2026, 3, 15, tzinfo=timezone.utc)
    assert record.url == "https://ieeexplore.ieee.org/document/123456/"
    assert record.sources == ["ieee"]
    assert "ieee:123456" in identity_aliases(record)


@pytest.mark.parametrize("value, expected", [
    ("March 2026", (2026, 3, 1)), ("2026", (2026, 1, 1)),
    ("2026-03-15", (2026, 3, 15)), ("2026-03", (2026, 3, 1)),
    ("15 Mar. 2026", (2026, 3, 15)), ("Mar. 2026", (2026, 3, 1)),
])
def test_publication_dates_default_month_and_day_to_one(value, expected):
    _, _, _, run = invoke([response({"total_records": 1, "articles": [article(publication_date=value)]})])
    assert run().records[0].published_at == datetime(*expected, tzinfo=timezone.utc)


def test_raw_item_count_includes_unusable_entries_and_keeps_missing_abstract():
    items = [None, {}, article(title=""), article(publication_date="nonsense"),
             article(abstract=None, doi=None, html_url=None),
             article(article_number=None, html_url="https://ieeexplore.ieee.org/document/888/")]
    _, _, _, run = invoke([response({"total_records": 6, "articles": items})])
    page = run()
    assert page.item_count == 6
    assert len(page.records) == 2
    assert page.records[0].abstract == "" and page.records[0].doi is None
    assert page.records[0].url == "https://ieeexplore.ieee.org/document/123456/"
    assert "ieee:888" in identity_aliases(page.records[1])


@pytest.mark.parametrize("payload", [{"total_records": 0}, {"total_records": 0, "articles": []}, {"totalfound": 0}])
def test_explicit_zero_is_success(payload):
    _, calls, waits, run = invoke([response(payload)])
    assert run().item_count == 0
    assert len(calls) == 1 and waits == []


@pytest.mark.parametrize("payload", [
    {"total_records": 1}, {"total_records": 1, "articles": []},
    {"articles": [article()]}, {"total_records": -1}, {"total_records": True},
    {"total_records": "1"}, {"total_records": 1.5}, [],
    {"total_records": 0, "articles": {}},
    {"total_records": 0, "error": "key rejected"},
])
def test_invalid_envelopes_retry_then_report_protocol_failure(payload):
    ieee, calls, waits, run = invoke([response(payload)] * 3)
    with pytest.raises(ieee.IeeeFetchError) as raised:
        run()
    assert raised.value.category == "protocol"
    assert len(calls) == 3 and waits == [1.0, 2.0]


def test_malformed_json_retries_and_documented_totalfound_succeeds():
    _, calls, waits, run = invoke([MetadataResponse(200, b"not json"),
                                 response({"totalfound": 1, "articles": [article()]})])
    assert run().total_records == 1
    assert len(calls) == 2 and waits == [1.0]


@pytest.mark.parametrize("failure", [MetadataResponse(429, b""), MetadataResponse(503, b""),
                                    TimeoutError("timeout"), URLError("network"), ConnectionError("closed")])
def test_transient_failures_retry(failure):
    _, calls, waits, run = invoke([failure, response({"total_records": 0})])
    run()
    assert len(calls) == 2 and waits == [1.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 425, 302])
def test_fatal_status_does_not_retry(status):
    ieee, calls, waits, run = invoke([MetadataResponse(status, b"credential-bearing body")])
    with pytest.raises(ieee.IeeeFetchError) as raised:
        run()
    assert raised.value.category == "http"
    assert str(status) in raised.value.detail
    assert len(calls) == 1 and waits == []


@pytest.mark.parametrize("failure", [
    URLError(f"https://ieeexploreapi.ieee.org/api/v1/search/articles?apikey={KEY}"),
    HTTPError(f"https://ieeexploreapi.ieee.org/?apikey={KEY}", 401, KEY, {}, None),
    RuntimeError(f"https://ieeexploreapi.ieee.org/?apikey={KEY}"),
])
def test_exception_and_formatted_traceback_never_expose_transport_url(failure, capsys):
    ieee, _, _, run = invoke([failure] * 3)
    with pytest.raises(ieee.IeeeFetchError) as raised:
        run()
    rendered = "".join(traceback.format_exception(raised.value))
    assert KEY not in rendered and "apikey=" not in rendered
    assert KEY not in repr(raised.value) and KEY not in raised.value.detail
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("changes", [
    {"start_record": 0}, {"start_record": True}, {"max_records": 201},
    {"max_records": 0}, {"attempts": 0}, {"attempts": 1.5},
    {"timeout_seconds": 0}, {"timeout_seconds": float("nan")}, {"key": ""},
    {"query": "journal:invalid"}, {"query": "conference:26:DAC"},
    {"query": "conference:2026:"}, {"query": "other"},
    {"from_date": date(2027, 1, 1)},
])
def test_input_errors_fail_before_transport(changes):
    _, calls, _, run = invoke([], **changes)
    with pytest.raises(ValueError):
        run()
    assert calls == []


def test_attempts_argument_limits_retries():
    ieee, calls, waits, run = invoke([MetadataResponse(500, b"")] * 2, attempts=2)
    with pytest.raises(ieee.IeeeFetchError):
        run()
    assert len(calls) == 2 and waits == [1.0]


def test_discovery_leaves_venue_and_edition_admission_to_integration():
    item = article(publication_title="Unlisted Workshop 2025", publication_date="2025")
    _, _, _, run = invoke([response({"total_records": 1, "articles": [item]})])
    assert run().records[0].journal == "Unlisted Workshop 2025"


def test_document_url_identity_drops_query_credentials():
    item = article(article_number=None,
                   html_url=f"https://ieeexplore.ieee.org/document/888/?apikey={KEY}")
    _, _, _, run = invoke([response({"total_records": 1, "articles": [item]})])
    record = run().records[0]
    assert record.url == "https://ieeexplore.ieee.org/document/888/"
    assert KEY not in json.dumps(record.to_dict())


def test_non_json_bytes_are_retried():
    _, calls, _, run = invoke([MetadataResponse(200, b"\xff"), response({"total_records": 0})])
    assert run().item_count == 0
    assert len(calls) == 2


@pytest.mark.parametrize("value, expected", [
    ("March-April 2024", (2024, 3, 1)), ("Nov.-Dec. 2026", (2026, 11, 1)),
    ("19-23 Feb. 2026", (2026, 2, 19)), ("19–23 February 2026", (2026, 2, 19)),
    ("Feb. 19-23, 2026", (2026, 2, 19)),
    ("30 Jan.-2 Feb. 2026", (2026, 1, 30)),
    ("1st Quarter 2026", (2026, 1, 1)), ("First Quarter 2026", (2026, 1, 1)),
    ("2nd Quarter 2026", (2026, 4, 1)), ("Second Quarter 2026", (2026, 4, 1)),
    ("3rd Quarter 2026", (2026, 7, 1)), ("Third Quarter 2026", (2026, 7, 1)),
    ("4th Quarter 2026", (2026, 10, 1)), ("Fourth Quarter 2026", (2026, 10, 1)),
])
def test_ieee_range_and_quarter_dates_use_conservative_period_start(value, expected):
    item = article(publication_date=value, publication_year=2025, insert_date="20261007")
    _, _, _, run = invoke([response({"total_records": 1, "articles": [item]})])
    assert run().records[0].published_at == datetime(*expected, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", [None, "", "unknown", "31 February 2026"])
@pytest.mark.parametrize("year", [2026, "2026"])
def test_unknown_publication_date_falls_back_only_to_reliable_publication_year(value, year):
    item = article(publication_date=value, publication_year=year, insert_date="20261007")
    _, _, _, run = invoke([response({"total_records": 1, "articles": [item]})])
    assert run().records[0].published_at == datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("year", [None, True, 2026.0, "2026-03", "26", 0, 10000, "0000"])
def test_unreliable_year_skips_individual_record_without_using_insertion_date(year):
    items = [article(publication_date="unknown", publication_year=year, insert_date="20261007"),
             article()]
    _, _, _, run = invoke([response({"total_records": 2, "articles": items})])
    page = run()
    assert page.item_count == 2 and len(page.records) == 1
    assert page.records[0].published_at == datetime(2026, 3, 15, tzinfo=timezone.utc)


@pytest.mark.parametrize("payload", [
    {"total_records": 0, "articles": [article()]},
    {"totalfound": 0, "articles": [article()]},
    {"total_records": 1, "articles": [article(), article()]},
    {"total_records": 1, "totalfound": 2, "articles": [article()]},
    {"total_records": 1, "totalfound": True, "articles": [article()]},
    {"total_records": 1, "totalfound": "1", "articles": [article()]},
    {"total_records": 3, "articles": [None, {}, article(publication_date="unknown")]},
])
def test_contradictory_totals_and_wholly_unusable_pages_are_protocol_failures(payload):
    ieee, calls, waits, run = invoke([response(payload)] * 3)
    with pytest.raises(ieee.IeeeFetchError) as raised:
        run()
    assert raised.value.category == "protocol"
    assert len(calls) == 3 and waits == [1.0, 2.0]


def test_matching_total_aliases_are_accepted():
    _, _, _, run = invoke([response({"total_records": 1, "totalfound": 1, "articles": [article()]})])
    assert run().total_records == 1
