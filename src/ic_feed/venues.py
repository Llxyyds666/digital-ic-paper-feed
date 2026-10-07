"""Exact, metadata-based flagship venue admission; never infer from paper text."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import unicodedata

from ic_feed.models import PaperRecord

DEFAULT_VENUES_PATH = Path("config/venues.json")


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))


@dataclass(frozen=True, slots=True)
class Venue:
    id: str
    name: str
    kind: str
    aliases: tuple[str, ...]


def load_venues(path: Path = DEFAULT_VENUES_PATH) -> tuple[Venue, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if type(raw) is not dict or set(raw) != {"version", "venues"} or type(raw["version"]) is not int or raw["version"] != 1 or type(raw["venues"]) is not list or not raw["venues"]:
        raise ValueError("invalid venue whitelist")
    venues = []
    seen = set()
    for item in raw["venues"]:
        if type(item) is not dict or set(item) != {"id", "name", "kind", "aliases"}:
            raise ValueError("invalid venue whitelist entry")
        if any(type(item[k]) is not str or not item[k].strip() for k in ("id", "name", "kind")) or item["id"] in seen or item["kind"] not in {"journal", "conference"}:
            raise ValueError("invalid or duplicate venue whitelist entry")
        if type(item["aliases"]) is not list or not item["aliases"] or any(type(v) is not str or not _normalize(v) for v in item["aliases"]):
            raise ValueError("invalid venue aliases")
        seen.add(item["id"])
        venues.append(Venue(item["id"], item["name"], item["kind"], tuple(_normalize(v) for v in item["aliases"])))
    return tuple(venues)


def _conference_matches(container: str, alias: str, venue_id: str) -> bool:
    """Allow publication wrappers around a complete configured conference name."""
    acronym = re.escape(_normalize(venue_id))
    prefix = (
        rf"(?:proceedings(?: of(?: the)?)?|the|annual|acm|ieee|{('jsap|' if venue_id == 'vlsi' else '')}{acronym}|"
        r"\d{1,4}(?:st|nd|rd|th)?)"
    )
    suffix = (
        rf"(?:{acronym}|\d{{1,4}}|proceedings|"
        r"(?:volume|vol|part) (?:\d{1,3}|[ivx]+))"
    )
    return re.fullmatch(
        rf"(?:{prefix} )*{re.escape(_normalize(alias))}(?: {suffix})*",
        container,
    ) is not None


def match_venue(record: PaperRecord, venues: tuple[Venue, ...] | None = None) -> Venue | None:
    journal = _normalize(record.journal)
    if not journal or any(re.search(rf"\b{term}\b", journal) for term in ("workshops?", "companion", "posters?", "demos?", "tutorials?", "doctoral")):
        return None
    for venue in venues if venues is not None else load_venues():
        for alias in venue.aliases:
            if venue.kind == "journal" and journal == alias:
                return venue
            if venue.kind == "conference" and any(
                _conference_matches(_normalize(container), alias, venue.id)
                for container in record.journal.split("·")
            ):
                # ASME hosts a different mechanical DAC with the same short title.
                if venue.id == "dac" and (
                    "asme" in journal
                    or not ((record.doi or "").casefold().startswith(("10.1145/", "10.1109/")) or re.search(r"\b(?:acm|ieee)\b", journal))
                ):
                    continue
                return venue
    return None
