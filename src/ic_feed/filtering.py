from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import unicodedata

from ic_feed.models import PaperRecord


@dataclass(frozen=True, slots=True)
class QueryRules:
    include_any: list[str]
    material_context: list[str]
    obvious_noise: list[str]
    context_required: list[str] = field(default_factory=list)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).strip()


def load_rules(path: Path) -> QueryRules:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return QueryRules(
        include_any=[_normalize_text(str(value)) for value in raw["include_any"]],
        material_context=[_normalize_text(str(value)) for value in raw["material_context"]],
        obvious_noise=[_normalize_text(str(value)) for value in raw["obvious_noise"]],
        context_required=[_normalize_text(str(value)) for value in raw.get("context_required", [])],
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?:s)?(?!\w)", text) is not None


def matches_rules(record: PaperRecord, rules: QueryRules) -> bool:
    text = _normalize_text(f"{record.title} {record.abstract}")
    strong_match = any(_contains_phrase(text, term) for term in rules.include_any)
    contextual_match = (
        any(_contains_phrase(text, term) for term in rules.context_required)
        and any(_contains_phrase(text, term) for term in rules.material_context)
    )
    if not (strong_match or contextual_match):
        return False
    if any(_contains_phrase(text, term) for term in rules.obvious_noise):
        return False
    return True
