from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup


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


__all__ = ["clean_string", "humanize_label", "normalize_description_text"]
