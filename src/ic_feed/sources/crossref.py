"""Crossref work-record adapter."""

from datetime import date, datetime, timezone
from html import unescape
import json
import re
from urllib.parse import urlencode

from ic_feed.models import PaperRecord, ScholarlyPage
from ic_feed.normalize import normalize_doi


_BASE_URL = "https://api.crossref.org/works"
_SELECT = "DOI,title,type,abstract,author,container-title,published-online,published-print,URL"
_MAX_PAGE_SIZE = 1000
_TAGS = re.compile(r"<[^>]+>")
_TITLE_TAG_NAMES = r"u|scp|sup|sub|i|italic|em|b|bold|strong"
_TITLE_TAGS = re.compile(rf"</?(?:{_TITLE_TAG_NAMES})(?:\s[^<>]*)?>", re.IGNORECASE)
_TITLE_INLINE_ELEMENTS = re.compile(
    rf"(?P<before>[ \t]*[\r\n]+[ \t]*)?<(?P<tag>{_TITLE_TAG_NAMES})(?:\s[^<>]*)?>"
    r"(?P<text>[^<>]*)</(?P=tag)\s*>(?P<after>[ \t]*[\r\n]+[ \t]*)?",
    re.IGNORECASE,
)
_PAPER_TYPES = frozenset(
    {
        "book-chapter",
        "book-section",
        "dissertation",
        "journal-article",
        "monograph",
        "posted-content",
        "proceedings-article",
        "report",
    }
)


def build_url(query: str, from_date: date, rows: int, cursor: str = "*") -> str:
    """Build the Crossref works request for a caller-supplied date window."""
    page_size = min(rows, _MAX_PAGE_SIZE)
    parameters = {'filter': f'from-pub-date:{from_date.isoformat()}', 'rows': page_size, 'cursor': cursor, 'select': _SELECT}
    endpoint = _BASE_URL
    if query.startswith("journal:"):
        issn = query.removeprefix("journal:")
        if not re.fullmatch(r"\d{4}-\d{3}[\dXx]", issn):
            raise ValueError("invalid journal ISSN")
        endpoint = f"https://api.crossref.org/journals/{issn}/works"
    elif query.startswith("container:"):
        parameters['filter'] += f",container-title:{query.removeprefix('container:')}"
    elif query.startswith("venue:"):
        parameters['query.container-title'] = query.removeprefix("venue:")
        parameters['sort'] = 'score'
        parameters['order'] = 'desc'
    else:
        parameters['query.bibliographic'] = query
    return f"{endpoint}?{urlencode(parameters)}"


def _strip_tags(value: object) -> str:
    return " ".join(_TAGS.sub(" ", unescape(str(value or ""))).split())


def _clean_title(value: object) -> str:
    """Remove inline emphasis without turning ACM's indented initials into words."""
    title = unescape(str(value or ""))

    def inline_text(match: re.Match[str]) -> str:
        tag = match["tag"].lower()
        text = match["text"]
        before, after = match["before"] or "", match["after"] or ""
        prefix = re.search(r"(\S+)$", title[:match.start()])
        previous = prefix[0] if prefix else ""
        prior_initial = prefix is not None and re.search(
            r"<u>[A-Z]</u>[ \t]*[\r\n]+[ \t]*$", title[:prefix.start()]
        ) is not None
        # Only layout newlines next to a marked word fragment are joined;
        # ordinary spaces and unmarked line breaks remain word boundaries.
        marked_prefix = tag in {"scp", "sup", "sub"} and bool(re.fullmatch(r"[A-Z]", previous))
        underlined_suffix = (
            tag == "u" and len(text) <= 2 and text[:1].islower()
            and len(previous) <= 3 and (previous[:1].isupper() or prior_initial)
        )
        if before and (marked_prefix or underlined_suffix or (tag == "u" and previous.endswith("-"))):
            before = ""
        following = title[match.end():match.end() + 1]
        next_word = re.match(r"[^\W_]+", title[match.end():])
        fragment = (
            (tag == "u" and (len(text) == 1 or (len(text) <= 2 and not text.isupper()))
             and (following.islower() or (next_word is not None and next_word[0].isupper())))
            or (tag == "scp" and marked_prefix)
            or (tag in {"sup", "sub"} and marked_prefix and next_word is not None
                and (next_word[0].isupper() or next_word[0].isdigit()))
        )
        if after and text[-1:].isalnum() and (
            (fragment and following.isalnum()) or following in {":", ",", ".", ";", ")"}
        ):
            after = ""
        return before + text + after

    title = _TITLE_INLINE_ELEMENTS.sub(inline_text, title)
    return " ".join(_TITLE_TAGS.sub("", title).split())


def _publication_date(item: dict[str, object]) -> datetime:
    """Return UTC publication time, defaulting a missing month or day to one."""
    for field in ("published-online", "published-print"):
        value = item.get(field)
        if not isinstance(value, dict):
            continue
        parts = value.get("date-parts")
        if not (isinstance(parts, list) and parts and isinstance(parts[0], list)):
            continue
        date_parts = parts[0]
        if not 1 <= len(date_parts) <= 3:
            continue
        try:
            year = int(date_parts[0])
            month = int(date_parts[1]) if len(date_parts) >= 2 else 1
            day = int(date_parts[2]) if len(date_parts) >= 3 else 1
            return datetime(year, month, day, tzinfo=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            continue
    raise ValueError("missing publication date")


def _authors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for author in value:
        if not isinstance(author, dict):
            continue
        name = str(author.get("name") or "").strip()
        if not name:
            name = " ".join(str(author.get(part) or "").strip() for part in ("given", "family")).strip()
        if name:
            result.append(name)
    return result


def parse_response(body: bytes) -> list[PaperRecord]:
    """Parse Crossref JSON, ignoring individual incomplete records."""
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
        raise ValueError("invalid Crossref response envelope")
    message = payload["message"]
    if not isinstance(message.get("items"), list):
        raise ValueError("invalid Crossref response envelope")
    items = message["items"]
    records: list[PaperRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            if item.get("type") not in _PAPER_TYPES:
                continue
            titles = item.get("title")
            title = _clean_title(titles[0]) if isinstance(titles, list) and titles else ""
            doi = normalize_doi(item.get("DOI") if isinstance(item.get("DOI"), str) else None)
            if not title or not doi:
                continue
            journals = item.get("container-title")
            journal = " · ".join(str(value) for value in journals) if isinstance(journals, list) and journals else ""
            records.append(
                PaperRecord(
                    title=title,
                    abstract=_strip_tags(item.get("abstract")),
                    authors=_authors(item.get("author")),
                    journal=journal,
                    published_at=_publication_date(item),
                    doi=doi,
                    url=str(item.get("URL") or f"https://doi.org/{doi}"),
                    sources=["crossref"],
                    source_ids=[doi],
                )
            )
        except (TypeError, ValueError, OverflowError):
            continue
    return records


def parse_page(body: bytes) -> ScholarlyPage:
    """Parse records plus the opaque cursor needed for the next Crossref page."""
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
        raise ValueError("invalid Crossref response envelope")
    message = payload["message"]
    items = message.get("items")
    if not isinstance(items, list):
        raise ValueError("invalid Crossref response envelope")
    next_cursor = message.get("next-cursor")
    if next_cursor is not None and (type(next_cursor) is not str or not next_cursor):
        raise ValueError("invalid Crossref next cursor")
    return ScholarlyPage(parse_response(body), next_cursor, len(items))
