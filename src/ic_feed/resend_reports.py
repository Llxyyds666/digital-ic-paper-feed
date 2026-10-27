"""Read-only replay of at most two explicitly requested daily report dates."""

from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys

from ic_feed.bark import send_plan
from ic_feed.config import load_config
from ic_feed.notification import (
    NotificationMessage,
    NotificationPlan,
    build_statistics_message,
)


COUNT_FIELDS = ("candidates", "processed", "selected")


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("invalid report replay configuration")


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument("--dates", required=True, help="One or two comma-separated YYYY-MM-DD dates")
    parser.add_argument("--usage", type=Path, default=Path("ai_usage.json"))
    parser.add_argument("--config", type=Path, default=Path("paper_feed_config.json"))
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _canonical_date(value: object) -> str:
    if type(value) is not str or date.fromisoformat(value).isoformat() != value:
        raise ValueError("invalid report date")
    return value


def _requested_dates(value: str) -> tuple[str, ...]:
    days = tuple(_canonical_date(item) for item in value.split(","))
    if not 1 <= len(days) <= 2 or len(set(days)) != len(days):
        raise ValueError("invalid report dates")
    return days


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate usage field")
        result[key] = value
    return result


def _usage_counts(path: Path, days: tuple[str, ...]) -> dict[str, dict[str, int]]:
    raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if type(raw) is not list:
        raise ValueError("invalid report usage")
    selected: dict[str, dict[str, int]] = {}
    seen_dates: set[str] = set()
    for entry in raw:
        if type(entry) is not dict:
            raise ValueError("invalid report usage")
        day = _canonical_date(entry.get("date"))
        if day in seen_dates:
            raise ValueError("duplicate usage date")
        seen_dates.add(day)
        if day in days:
            counts = {field: entry[field] for field in COUNT_FIELDS}
            if any(type(value) is not int or value < 0 for value in counts.values()):
                raise ValueError("invalid report counts")
            selected[day] = counts
    if set(selected) != set(days):
        raise ValueError("missing report date")
    return selected


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        days = _requested_dates(args.dates)
        config = load_config(args.config)
        counts_by_date = _usage_counts(args.usage, days)
        reports: list[tuple[str, NotificationPlan]] = []
        # Finish validating every requested date and message before the first send.
        for day in days:
            counts = counts_by_date[day]
            statistics = build_statistics_message(
                **counts,
                focus_selected=counts["selected"],
                base_url=config.publication.base_url,
            )
            message = NotificationMessage(
                f"{statistics.title} {day} (补发)",
                f"报告日期：{day}（补发）\n{statistics.body}\n链接：当前累计日报（非历史快照）",
                statistics.url,
            )
            reports.append((day, NotificationPlan((message,))))
    except (OSError, UnicodeError, TypeError, ValueError, KeyError, AttributeError, OverflowError):
        print("Invalid report replay configuration; no reports sent.", file=sys.stderr)
        return 1

    if args.dry_run:
        for day in days:
            counts = counts_by_date[day]
            print(
                f"Dry-run report date={day} "
                + " ".join(f"{field}={counts[field]}" for field in COUNT_FIELDS)
                + f" focus_selected={counts['selected']}"
            )
        return 0

    token = os.environ.get("BARK_TOKEN")
    if token is None or not token.strip():
        print("BARK_TOKEN is missing; no reports sent.", file=sys.stderr)
        return 1

    accepted = 0
    for day, plan in reports:
        print(f"Bark report replay date={day}")
        try:
            results = send_plan(token, plan)
            success = results == (True,)
        except Exception:
            print(f"Bark report replay date={day} failed unexpectedly.", file=sys.stderr)
            success = False
        accepted += int(success)
    failed = len(reports) - accepted
    print(f"Bark report replay: accepted={accepted} failed={failed}")
    return int(failed > 0)


if __name__ == "__main__":
    raise SystemExit(main())
