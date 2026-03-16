from __future__ import annotations

import re
from typing import Any


def humanize_label(value: str) -> str:
    text = value.replace("-", " ").replace("_", " ")
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return " ".join(part.capitalize() for part in text.split())


def clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


__all__ = ["clean_string", "humanize_label"]
