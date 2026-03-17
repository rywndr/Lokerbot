from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class Job:
    job_id: str
    title: str
    company: str
    location: str | None
    job_type: str | None
    salary_range: str | None
    url: str
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    posted_at: str | None = None
    scraped_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "job_type": self.job_type,
            "salary_range": self.salary_range,
            "url": self.url,
            "description": self.description,
            "tags": list(self.tags),
            "posted_at": self.posted_at,
            "scraped_at": self.scraped_at,
        }
