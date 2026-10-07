"""Ungated IEEE metadata discovery with explicit paging and secret-safe errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
import re
import time
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit

from ic_feed.enrich import MetadataTransport, _default_transport, _valid_abstract
from ic_feed.models import PaperRecord
from ic_feed.normalize import normalize_doi
from ic_feed.retry import RetryPolicy, call_with_retry, retryable_http_status


IEEE_SEARCH_URL = "https://ieeexploreapi.ieee.org/api/v1/search/articles"


@dataclass(frozen=True)
class IeeePage:
    records: list[PaperRecord]
    item_count: int
    total_records: int


class IeeeFetchError(Exception):
    def __init__(self, category: str, detail: str) -> None:
        self.category = category
        self.detail = detail
        super().__init__(f"{category}: {detail}")


def _http_error(status: int) -> IeeeFetchError:
    return IeeeFetchError("http", f"ieee status={status}")


def _publication_period_start(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError("missing publication date")
    # Periods represent only known precision: use their start, not an invented
    # exact publication day. IEEE also emits issue and conference date ranges.
    text = " ".join(value.replace(".", "").replace(",", "")
                    .replace("\u2013", "-").replace("\u2014", "-").split())
    quarter = re.fullmatch(r"(1st|first|2nd|second|3rd|third|4th|fourth) Quarter ([0-9]{4})",
                           text, re.IGNORECASE)
    if quarter:
        quarters = {"1st": 1, "first": 1, "2nd": 2, "second": 2,
                    "3rd": 3, "third": 3, "4th": 4, "fourth": 4}
        return datetime(int(quarter[2]), 3 * quarters[quarter[1].lower()] - 2, 1,
                        tzinfo=timezone.utc)
    range_patterns = (
        (r"([A-Za-z]+)\s*-\s*([A-Za-z]+) ([0-9]{4})",
         lambda m: (f"{m[1]} {m[3]}", f"{m[2]} {m[3]}")),
        (r"([0-9]{1,2})\s*-\s*([0-9]{1,2}) ([A-Za-z]+) ([0-9]{4})",
         lambda m: (f"{m[1]} {m[3]} {m[4]}", f"{m[2]} {m[3]} {m[4]}")),
        (r"([A-Za-z]+) ([0-9]{1,2})\s*-\s*([0-9]{1,2}) ([0-9]{4})",
         lambda m: (f"{m[2]} {m[1]} {m[4]}", f"{m[3]} {m[1]} {m[4]}")),
        (r"([0-9]{1,2}) ([A-Za-z]+)\s*-\s*([0-9]{1,2}) ([A-Za-z]+) ([0-9]{4})",
         lambda m: (f"{m[1]} {m[2]} {m[5]}", f"{m[3]} {m[4]} {m[5]}")),
    )
    for pattern, endpoints in range_patterns:
        match = re.fullmatch(pattern, text)
        if match:
            start_text, end_text = endpoints(match)
            start = _publication_period_start(start_text)
            end = _publication_period_start(end_text)
            if end < start:
                raise ValueError("invalid publication period")
            return start
    for pattern in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y",
                    "%B %Y", "%b %Y", "%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError("invalid publication date")


def _publication_date(value: object, publication_year: object = None) -> datetime:
    try:
        return _publication_period_start(value)
    except ValueError:
        # A reliable publication year permits year precision only. Insertion
        # dates are harvesting metadata and must never substitute for this.
        if type(publication_year) is int:
            year = publication_year
        elif type(publication_year) is str and re.fullmatch(r"[0-9]{4}", publication_year):
            year = int(publication_year)
        else:
            raise ValueError("missing reliable publication year") from None
        if not 1000 <= year <= 9999:
            raise ValueError("invalid publication year") from None
        return datetime(year, 1, 1, tzinfo=timezone.utc)


def _document_url(item: dict) -> str | None:
    number = item.get("article_number")
    if type(number) in (int, str) and re.fullmatch(r"[0-9]+", str(number).strip()):
        return f"https://ieeexplore.ieee.org/document/{str(number).strip()}/"
    for name in ("html_url", "document_url", "url"):
        value = item.get(name)
        if type(value) is not str:
            continue
        try:
            parsed = urlsplit(value)
            parsed.port
        except ValueError:
            continue
        if (parsed.scheme in {"http", "https"}
                and parsed.hostname in {"ieeexplore.ieee.org", "www.ieeexplore.ieee.org"}
                and parsed.username is None and parsed.password is None):
            match = re.fullmatch(r"/document/([0-9]+)/?", parsed.path)
            if match:
                return f"https://ieeexplore.ieee.org/document/{match[1]}/"
    return None


def _parse_record(item: object) -> PaperRecord | None:
    if type(item) is not dict:
        return None
    title = item.get("title")
    publication = item.get("publication_title")
    if type(title) is not str or not title.strip():
        return None
    if type(publication) is not str or not publication.strip():
        return None
    published_at = _publication_date(item.get("publication_date"), item.get("publication_year"))
    doi = normalize_doi(item.get("doi") if type(item.get("doi")) is str else None)
    document = _document_url(item)
    if document is None and doi is None:
        return None
    authors = []
    author_data = item.get("authors")
    if type(author_data) is dict and type(author_data.get("authors")) is list:
        for author in author_data["authors"]:
            if type(author) is dict and type(author.get("full_name")) is str:
                name = author["full_name"].strip()
                if name and name not in authors:
                    authors.append(name)
    return PaperRecord(
        title=title.strip(), abstract=_valid_abstract(title, item.get("abstract")) or "",
        authors=authors, journal=publication.strip(), published_at=published_at,
        doi=doi, url=document or f"https://doi.org/{doi}", sources=["ieee"],
        source_ids=[document] if document else [doi],
    )


def _parse_page(body: bytes) -> IeeePage:
    payload = json.loads(body)
    if type(payload) is not dict or any(name in payload for name in ("error", "errors")):
        raise ValueError("invalid envelope")
    totals = [payload[name] for name in ("total_records", "totalfound") if name in payload]
    if not totals or any(type(total) is not int or total < 0 for total in totals):
        raise ValueError("invalid total")
    total = totals[0]
    if any(other != total for other in totals):
        raise ValueError("contradictory totals")
    articles = payload.get("articles", [] if total == 0 else None)
    if (type(articles) is not list or (total > 0 and not articles)
            or len(articles) > total):
        raise ValueError("invalid articles")
    records = []
    for item in articles:
        try:
            record = _parse_record(item)
        except (ValueError, TypeError, OverflowError):
            continue
        if record is not None:
            records.append(record)
    if articles and not records:
        raise ValueError("no usable article metadata")
    return IeeePage(records, len(articles), total)


def request_page(
    query: str, from_date: date, until_date: date,
    start_record: int, max_records: int, key: str, *,
    timeout_seconds: float = 20, attempts: int = 3,
    transport: MetadataTransport | None = None,
    wait: Callable[[float], None] = time.sleep,
) -> IeeePage:
    """Fetch one insertion-date window page; never infer coverage from failure."""
    if (type(start_record) is not int or start_record < 1
            or type(max_records) is not int or not 1 <= max_records <= 200
            or type(attempts) is not int or attempts < 1):
        raise ValueError("invalid IEEE pagination or retry input")
    if (type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("invalid IEEE timeout")
    if type(key) is not str or not key.strip():
        raise ValueError("missing IEEE key")
    if (type(from_date) is not date or type(until_date) is not date
            or from_date > until_date):
        raise ValueError("invalid IEEE insertion date window")
    if type(query) is not str:
        raise ValueError("invalid IEEE query")
    parameters = {
        "apikey": key, "format": "json", "start_record": start_record,
        "max_records": max_records, "sort_field": "article_number", "sort_order": "asc",
        "start_date": from_date.strftime("%Y%m%d"), "end_date": until_date.strftime("%Y%m%d"),
    }
    if query.startswith("journal:"):
        issn = query.removeprefix("journal:")
        if not re.fullmatch(r"[0-9]{4}-[0-9]{3}[0-9Xx]", issn):
            raise ValueError("invalid IEEE journal ISSN")
        parameters["issn"] = issn
    elif query.startswith("conference:"):
        parts = query.split(":", 2)
        if len(parts) != 3 or not re.fullmatch(r"[0-9]{4}", parts[1]) or not parts[2].strip():
            raise ValueError("invalid IEEE conference query")
        parameters.update(publication_title=parts[2], publication_year=parts[1], content_type="Conferences")
    else:
        raise ValueError("invalid IEEE query")
    url = IEEE_SEARCH_URL + "?" + urlencode(parameters)
    send = transport or _default_transport

    def operation() -> IeeePage:
        # Construct sanitized exceptions outside their except blocks. Neither
        # __cause__ nor __context__ can retain a transport URL or response body.
        failure = None
        try:
            response = send("GET", url, {}, None, timeout_seconds)
        except HTTPError as error:
            failure = _http_error(error.code)
        except Exception:
            failure = IeeeFetchError("network", "ieee transport failure")
        if failure is not None:
            raise failure
        if not 200 <= response.status < 300:
            raise _http_error(response.status)
        try:
            page = _parse_page(response.body)
        except (ValueError, TypeError, UnicodeError, OverflowError):
            failure = IeeeFetchError("protocol", "ieee invalid metadata response")
        if failure is not None:
            raise failure
        return page

    def should_retry(error: Exception) -> bool:
        if not isinstance(error, IeeeFetchError):
            return False
        if error.category in {"network", "protocol"}:
            return True
        status = int(error.detail.removeprefix("ieee status="))
        # Fatal 4xx stop immediately; only 429 is retried among client errors.
        return status == 429 or (500 <= status <= 599 and retryable_http_status(status))

    failure = None
    try:
        return call_with_retry(
            operation, should_retry,
            policy=RetryPolicy(attempts, tuple(float(2 ** index) for index in range(attempts - 1))),
            wait=wait,
        )
    except IeeeFetchError as error:
        failure = IeeeFetchError(error.category, error.detail)
    if failure is not None:
        raise failure
