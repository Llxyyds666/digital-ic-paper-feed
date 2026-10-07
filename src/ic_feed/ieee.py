"""Secret-safe IEEE metadata enrichment of exact, approved paper identities."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import re
import time
from typing import Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit

from ic_feed.enrich import (
    DEFAULT_TIMEOUT_SECONDS,
    MetadataTransport,
    _clean_text,
    _default_transport,
    _missing,
    _report_provider_error,
    _title_tokens,
    _valid_abstract,
)
from ic_feed.models import PaperRecord
from ic_feed.normalize import group_records, normalize_doi, record_key
from ic_feed.retry import DEFAULT_RETRY_POLICY, call_with_retry, retryable_http_status
from ic_feed.state import FeedState
from ic_feed.venues import match_venue


IEEE_SEARCH_URL = "https://ieeexploreapi.ieee.org/api/v1/search/articles"
_IEEE_DOI_PREFIXES = ("10.1109/", "10.23919/")
_XPLORE_HOSTS = {"ieeexplore.ieee.org", "www.ieeexplore.ieee.org"}


@dataclass(frozen=True, slots=True)
class IeeeEnrichmentStats:
    queried: int = 0
    enriched: int = 0
    metadata_enriched: int = 0
    key_mapping: dict[str, str] = field(default_factory=dict)


def _article_number(value: object) -> str | None:
    if type(value) not in (str, int):
        return None
    text = str(value).strip()
    return text if re.fullmatch(r"[0-9]+", text) else None


def _query_identity(record: PaperRecord) -> tuple[str | None, str | None] | None:
    if match_venue(record) is None:
        return None
    doi = normalize_doi(record.doi)
    if doi and not doi.startswith(_IEEE_DOI_PREFIXES):
        return None
    try:
        parsed = urlsplit(record.url)
        parsed.port
    except ValueError:
        return None
    official = (
        parsed.scheme in {"https", "http"}
        and parsed.hostname in _XPLORE_HOSTS
        and parsed.username is None
        and parsed.password is None
    )
    number = None
    if official:
        match = re.fullmatch(r"/document/([0-9]+)/?", parsed.path)
        if match:
            number = match[1]
    if number is None and doi is None:
        return None
    return number, doi


def _compatible_article(record: PaperRecord, value: object, number: str | None, doi: str | None) -> bool:
    if type(value) is not dict or type(value.get("title")) is not str:
        return False
    # Exact normalized tokens allow punctuation/case changes, never a title
    # containment or a distinct "extended version" with a different identity.
    title = _title_tokens(_clean_text(value["title"]))
    if not title or title != _title_tokens(_clean_text(record.title)):
        return False
    raw_doi = value.get("doi")
    if raw_doi is not None and type(raw_doi) is not str:
        return False
    result_doi = normalize_doi(raw_doi)
    if doi is not None and result_doi != doi:
        return False
    if result_doi is not None and not result_doi.startswith(_IEEE_DOI_PREFIXES):
        return False
    if number is not None and _article_number(value.get("article_number")) != number:
        return False
    publication = value.get("publication_title")
    if type(publication) is not str or not publication.strip():
        return False
    original_venue = match_venue(record)
    result_venue = match_venue(replace(record, journal=publication, doi=result_doi or record.doi))
    return original_venue is not None and result_venue is not None and original_venue.id == result_venue.id


def _author_names(value: object) -> list[str]:
    if type(value) is not dict or type(value.get("authors")) is not list:
        return []
    result = []
    for author in value["authors"]:
        if type(author) is not dict or type(author.get("full_name")) is not str:
            continue
        name = _clean_text(author["full_name"])
        if name and name not in result:
            result.append(name)
    return result


def _rebuild_identities(state: FeedState, originals: list[tuple[str, PaperRecord]]) -> dict[str, str]:
    """Rekey DOI promotions and merge collisions while preserving AI judgments."""
    pending = set(state.pending_ai)
    groups = group_records([record for _, record in originals])
    key_mapping = {old_key: record_key(record) for old_key, record in originals}
    original_groups: dict[str, list[tuple[str, PaperRecord]]] = {}
    for old_key, record in originals:
        original_groups.setdefault(record_key(record), []).append((old_key, record))
    new_pending = []
    for key, (merged, aliases) in groups.items():
        members = [member for alias in set(aliases) for member in original_groups.get(alias, [])]
        processed = [(old_key, record) for old_key, record in members if record.ai_relevant is not None]
        if processed:
            # Prefer an already processed canonical DOI record over a newly
            # DOI-promoted RSS duplicate. Metadata must not erase its judgment.
            _, previous = min(processed, key=lambda item: (item[0] != key, item[0]))
            merged.ai_relevant = previous.ai_relevant
            merged.ai_confidence = previous.ai_confidence
            merged.summary_zh = previous.summary_zh
            merged.categories = list(previous.categories)
        pending_members = [(old_key, record) for old_key, record in members if old_key in pending]
        if pending_members and (not processed or any(record.ai_relevant is not None for _, record in pending_members)):
            new_pending.append(key)
        for old_key, _ in members:
            key_mapping[old_key] = key
    state.papers = {key: record for key, (record, _) in groups.items()}
    state.pending_ai = sorted(set(new_pending), key=lambda key: (state.papers[key].published_at, key))
    return key_mapping


class IeeeEnricher:
    def __init__(
        self,
        key: str | None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: MetadataTransport | None = None,
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = key or None
        self._timeout_seconds = timeout_seconds
        self._transport = transport or _default_transport
        self._wait = wait

    def _request(self, number: str | None, doi: str | None) -> object | None:
        params = {"apikey": self._key, "format": "json", "start_record": 1, "max_records": 5}
        params["article_number" if number is not None else "doi"] = number if number is not None else doi
        url = IEEE_SEARCH_URL + "?" + urlencode(params)

        def operation() -> object:
            response = self._transport("GET", url, {}, None, self._timeout_seconds)
            if not 200 <= response.status < 300:
                raise HTTPError(url, response.status, "", {}, None)
            return json.loads(response.body)

        def should_retry(error: Exception) -> bool:
            if isinstance(error, HTTPError):
                return retryable_http_status(error.code)
            return isinstance(error, (TimeoutError, ConnectionError, URLError, json.JSONDecodeError))

        try:
            return call_with_retry(operation, should_retry, policy=DEFAULT_RETRY_POLICY, wait=self._wait)
        except Exception as error:
            status = error.code if isinstance(error, HTTPError) else None
            # Never format the URL: the official API carries its credential
            # in the query string, including inside HTTPError/URLError objects.
            _report_provider_error("ieee", type(error).__name__, status)
            return None

    def enrich(self, state: FeedState, candidate_keys: Sequence[str]) -> IeeeEnrichmentStats:
        if not self._key:
            return IeeeEnrichmentStats()
        originals = list(state.papers.items())
        queried = enriched = metadata_enriched = 0
        for key in dict.fromkeys(candidate_keys):
            record = state.papers.get(key)
            if record is None:
                continue
            identity = _query_identity(record)
            if identity is None:
                continue
            missing_abstract = _missing(record.title, record.abstract)
            missing_authors = not any(name.strip() for name in record.authors)
            if record.doi and not missing_abstract and not missing_authors:
                continue
            queried += 1
            number, doi = identity
            payload = self._request(number, doi)
            if type(payload) is not dict or type(payload.get("articles")) is not list:
                continue
            matches = [article for article in payload["articles"] if _compatible_article(record, article, number, doi)]
            if len(matches) != 1:
                continue
            result = matches[0]
            changed = False
            if not record.doi:
                new_doi = normalize_doi(result.get("doi"))
                if new_doi:
                    record.doi = new_doi
                    changed = True
            if missing_authors:
                authors = _author_names(result.get("authors"))
                if authors:
                    record.authors = authors
                    changed = True
            if missing_abstract:
                abstract = _valid_abstract(record.title, result.get("abstract"))
                if abstract is not None:
                    record.abstract = abstract
                    enriched += 1
                    changed = True
            if changed:
                metadata_enriched += 1
                if "ieee" not in record.sources:
                    record.sources.append("ieee")
        mapping = _rebuild_identities(state, originals) if metadata_enriched else {}
        return IeeeEnrichmentStats(queried, enriched, metadata_enriched, mapping)
