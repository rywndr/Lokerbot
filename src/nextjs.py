from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup


def extract_next_data(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None:
        raise ValueError("Could not find __NEXT_DATA__ in listing page HTML")

    payload = script.string or script.get_text(strip=True)
    if not payload:
        raise ValueError("The __NEXT_DATA__ script was empty")

    return json.loads(payload)


__all__ = ["extract_next_data"]
