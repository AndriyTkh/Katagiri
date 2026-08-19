#!/usr/bin/env python3
"""Append one study-session line to docs/katagiri/katagiri/60-review/study-log.jsonl.

Session-level log only (minutes, activities, items mined, friction notes). Per-item
review records belong in 60-review/reviews.jsonl instead. Append-only: this script
never reads, rewrites, or reorders existing lines.

Python 3.12, standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

VALID_ACTIVITIES = (
    "review",
    "new_material",
    "shadowing",
    "listening",
    "reading",
    "conversation",
)

LOG_RELPATH = Path("docs") / "katagiri" / "katagiri" / "60-review" / "study-log.jsonl"


def repo_root() -> Path:
    """Repo root, resolved from this script's location (scripts/log_study.py)."""
    return Path(__file__).resolve().parent.parent


def parse_activities(raw: str) -> list[str]:
    """Split a comma list, validate against VALID_ACTIVITIES, drop duplicates."""
    names = [part.strip() for part in raw.split(",")]
    names = [name for name in names if name]
    if not names:
        raise argparse.ArgumentTypeError("--activities must name at least one activity")

    unknown = [name for name in names if name not in VALID_ACTIVITIES]
    if unknown:
        raise argparse.ArgumentTypeError(
            "unknown activity: {}; choose from {}".format(
                ", ".join(unknown), ", ".join(VALID_ACTIVITIES)
            )
        )

    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def parse_timestamp(raw: str) -> str:
    """Normalize an override timestamp to ISO 8601 UTC with a Z suffix."""
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--date must be ISO 8601 (e.g. 2026-08-19T18:20:11Z): {exc}"
        ) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def nonnegative_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an integer, got {raw!r}") from exc
    if value < 0:
        raise argparse.ArgumentTypeError(f"expected a non-negative integer, got {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="log_study.py",
        description="Append one study-session line to 60-review/study-log.jsonl.",
    )
    parser.add_argument(
        "--minutes",
        required=True,
        type=nonnegative_int,
        help="wall-clock minutes spent studying",
    )
    parser.add_argument(
        "--activities",
        required=True,
        type=parse_activities,
        metavar="A,B,C",
        help="comma-separated subset of: " + ", ".join(VALID_ACTIVITIES),
    )
    parser.add_argument(
        "--mined",
        type=nonnegative_int,
        default=0,
        help="items that actually became cards (default: 0)",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="free-text friction notes: what stalled, what confused (default: empty)",
    )
    parser.add_argument(
        "--date",
        type=parse_timestamp,
        default=None,
        metavar="ISO8601",
        help="override the timestamp (default: now, UTC)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    record = {
        "ts": args.date or now_utc(),
        "type": "study_session",
        "minutes": args.minutes,
        "activities": args.activities,
        "items_mined": args.mined,
        "notes": args.notes,
    }

    line = json.dumps(record, ensure_ascii=False)
    log_path = repo_root() / LOG_RELPATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")

    print(line, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
