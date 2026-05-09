from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup

RECENT_POST_WINDOW = timedelta(days=30)


def humanize_label(value: str) -> str:
    text = value.replace("-", " ").replace("_", " ")
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return " ".join(part.capitalize() for part in text.split())


def clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def normalize_description_text(value: Any) -> str | None:
    raw = clean_string(value)
    if raw is None:
        return None

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup.find_all("br"):
        tag.replace_with("\n")

    text = soup.get_text("\n")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    normalized = "\n".join(line for line in lines if line)
    return normalized or None


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_recent_job_post(posted_at: str | None, scraped_at: datetime) -> bool:
    posted_at_dt = parse_iso_datetime(posted_at)
    if posted_at_dt is None:
        return False
    return scraped_at - RECENT_POST_WINDOW <= posted_at_dt <= scraped_at


def dedupe_list(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped


__all__ = [
    "RECENT_POST_WINDOW",
    "clean_string",
    "dedupe_list",
    "humanize_label",
    "is_recent_job_post",
    "normalize_description_text",
    "parse_iso_datetime",
]
