from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import requests

from lokerbot.http_client import build_session
from lokerbot.models import Job, utc_now_iso
from lokerbot.nextjs import extract_next_data
from lokerbot.utils import clean_string as _clean_string, humanize_label as _humanize_label

DEALLS_LISTING_URL = "https://dealls.com/loker"
DEALLS_JOBS_API_URL = "https://api.sejutacita.id/v1/explore-job/job"
DEALLS_JOB_DETAIL_API_URL = "https://api.sejutacita.id/v1/job-portal/job/slug/{slug}"
LISTING_QUERY_KEY = "/v1/explore-job/job"
HTML_ACCEPT_HEADER = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_listing_page(session: requests.Session | None = None) -> str:
    owns_session = session is None
    session = session or build_session()
    try:
        response = session.get(DEALLS_LISTING_URL, headers=HTML_ACCEPT_HEADER)
        response.raise_for_status()
        return response.text
    finally:
        if owns_session:
            session.close()


def parse_jobs(payload: dict[str, Any], scraped_at: str | None = None) -> list[Job]:
    page_data = _extract_jobs_page(payload)
    raw_jobs = page_data.get("docs")
    if not isinstance(raw_jobs, list):
        raise ValueError("Dealls payload did not include a docs list")

    timestamp = scraped_at or utc_now_iso()
    jobs: list[Job] = []
    for item in raw_jobs:
        if not isinstance(item, dict):
            continue

        job = _parse_job_doc(item, scraped_at=timestamp)
        if job is not None:
            jobs.append(job)

    return jobs


def scrape(
    max_pages: int = 1,
    fetch_details: bool = False,
    delay: float = 0.0,
    session: requests.Session | None = None,
) -> list[Job]:
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if delay < 0:
        raise ValueError("delay must be non-negative")

    owns_session = session is None
    session = session or build_session()
    try:
        html = fetch_listing_page(session)
        next_data = extract_next_data(html)
        query_params, first_page = _extract_listing_query(next_data)
        app_version = str(next_data.get("runtimeConfig", {}).get("version") or "")
        scraped_at = utc_now_iso()

        jobs = _parse_and_optionally_enrich(
            first_page,
            session=session,
            fetch_details=fetch_details,
            app_version=app_version,
            scraped_at=scraped_at,
        )

        total_pages = first_page.get("totalPages") if isinstance(first_page, dict) else None
        last_page = min(max_pages, total_pages) if isinstance(total_pages, int) and total_pages > 0 else max_pages

        for page in range(2, last_page + 1):
            if delay:
                time.sleep(delay)
            page_payload = _fetch_api_page(session, page=page, query_params=query_params, app_version=app_version)
            jobs.extend(
                _parse_and_optionally_enrich(
                    page_payload,
                    session=session,
                    fetch_details=fetch_details,
                    app_version=app_version,
                    scraped_at=scraped_at,
                )
            )

        return jobs
    finally:
        if owns_session:
            session.close()


def _parse_and_optionally_enrich(
    payload: dict[str, Any],
    *,
    session: requests.Session,
    fetch_details: bool,
    app_version: str,
    scraped_at: str,
) -> list[Job]:
    page_data = _extract_jobs_page(payload)
    raw_jobs = page_data.get("docs")
    if not isinstance(raw_jobs, list):
        raise ValueError("Dealls payload did not include a docs list")

    jobs: list[Job] = []
    for item in raw_jobs:
        if not isinstance(item, dict):
            continue
        job = _parse_job_doc(item, scraped_at=scraped_at)
        if job is None:
            continue
        if fetch_details and _should_fetch_detail(job):
            _enrich_job_from_detail(session, job, item, app_version=app_version)
        jobs.append(job)
    return jobs


def _fetch_api_page(
    session: requests.Session,
    *,
    page: int,
    query_params: dict[str, Any],
    app_version: str,
) -> dict[str, Any]:
    params = _normalize_query_params(query_params)
    params["page"] = page

    response = session.get(
        DEALLS_JOBS_API_URL,
        params=params,
        headers=_build_api_headers(app_version),
    )
    response.raise_for_status()

    data = response.json().get("data")
    if not isinstance(data, dict):
        raise ValueError("Unexpected response shape from Dealls jobs API")
    return data


def _enrich_job_from_detail(
    session: requests.Session,
    job: Job,
    raw_job: dict[str, Any],
    *,
    app_version: str,
) -> None:
    slug = raw_job.get("slug")
    if not isinstance(slug, str) or not slug:
        return

    response = session.get(
        DEALLS_JOB_DETAIL_API_URL.format(slug=quote(slug, safe="")),
        headers=_build_api_headers(app_version),
    )
    response.raise_for_status()

    detail = response.json().get("data", {}).get("result")
    if not isinstance(detail, dict):
        return

    job.location = job.location or _format_location(detail)
    job.job_type = job.job_type or _format_job_type(detail)
    job.salary_range = job.salary_range or _format_salary_range(
        detail.get("salaryRange"),
        detail.get("salaryType"),
    )
    if not job.tags:
        job.tags = _collect_tags(detail)


def _should_fetch_detail(job: Job) -> bool:
    return job.location is None or job.job_type is None


def _extract_jobs_page(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("docs"), list):
        return payload

    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("docs"), list):
        return data

    query_params, page_data = _extract_listing_query(payload)
    _ = query_params
    return page_data


def _extract_listing_query(next_data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    queries = (
        next_data.get("props", {})
        .get("pageProps", {})
        .get("dehydratedState", {})
        .get("queries", [])
    )
    if not isinstance(queries, list):
        raise ValueError("Could not find dehydrated query data in __NEXT_DATA__")

    for query in queries:
        if not isinstance(query, dict):
            continue
        query_key = query.get("queryKey")
        if not isinstance(query_key, list) or not query_key or query_key[0] != LISTING_QUERY_KEY:
            continue

        query_params = query_key[1] if len(query_key) > 1 and isinstance(query_key[1], dict) else {}
        pages = query.get("state", {}).get("data", {}).get("pages", [])
        if not isinstance(pages, list) or not pages or not isinstance(pages[0], dict):
            raise ValueError("Could not find the initial jobs page in __NEXT_DATA__")
        return dict(query_params), pages[0]

    raise ValueError("Could not find the Dealls jobs query inside __NEXT_DATA__")


def _parse_job_doc(item: dict[str, Any], *, scraped_at: str) -> Job | None:
    job_id = item.get("id")
    title = item.get("role")
    if not isinstance(job_id, str) or not job_id:
        return None
    if not isinstance(title, str) or not title.strip():
        return None

    company = item.get("company") if isinstance(item.get("company"), dict) else {}
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    company_name = company.get("name") or author.get("name")
    if not isinstance(company_name, str) or not company_name.strip():
        return None

    return Job(
        job_id=job_id,
        title=title.strip(),
        company=company_name.strip(),
        location=_format_location(item),
        job_type=_format_job_type(item),
        salary_range=_format_salary_range(item.get("salaryRange"), item.get("salaryType")),
        url=_build_job_url(item),
        tags=_collect_tags(item),
        posted_at=_clean_string(item.get("publishedAt") or item.get("createdAt")),
        scraped_at=scraped_at,
    )


def _build_api_headers(app_version: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://dealls.com",
        "Referer": f"{DEALLS_LISTING_URL}",
        "x-client-app-name": "Deall-Talent-Web",
    }
    if app_version:
        headers["x-client-app-version"] = app_version
    return headers


def _normalize_query_params(query_params: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in query_params.items():
        if value in (None, [], {}):
            continue
        if isinstance(value, bool):
            normalized[key] = str(value).lower()
        else:
            normalized[key] = value
    return normalized


def _format_location(item: dict[str, Any]) -> str | None:
    location = _clean_string(item.get("location"))
    if location:
        return location

    city = item.get("city") if isinstance(item.get("city"), dict) else {}
    country = item.get("country") if isinstance(item.get("country"), dict) else {}
    city_name = _clean_string(city.get("name"))
    country_name = _clean_string(country.get("name"))

    parts = [part for part in (city_name, country_name) if part]
    if parts:
        if len(parts) == 2 and parts[0] == parts[1]:
            return parts[0]
        return ", ".join(parts)

    workplace_type = item.get("workplaceType")
    if workplace_type == "remote":
        return "Remote"

    return None


def _format_job_type(item: dict[str, Any]) -> str | None:
    employment_types = item.get("employmentTypes")
    if isinstance(employment_types, list):
        formatted = [_humanize_label(value) for value in employment_types if isinstance(value, str) and value]
        if formatted:
            return ", ".join(formatted)

    workplace_type = item.get("workplaceType")
    if isinstance(workplace_type, str) and workplace_type:
        return _humanize_label(workplace_type)

    return None


def _format_salary_range(salary_range: Any, salary_type: Any) -> str | None:
    if salary_type == "unpaid":
        return "Unpaid"
    if not isinstance(salary_range, dict):
        return None

    start = salary_range.get("start")
    end = salary_range.get("end")
    if isinstance(start, int) and isinstance(end, int):
        return f"IDR {start:,} - {end:,}"
    if isinstance(start, int):
        return f"From IDR {start:,}"
    if isinstance(end, int):
        return f"Up to IDR {end:,}"
    return None


def _build_job_url(item: dict[str, Any]) -> str:
    slug = _clean_string(item.get("slug") or item.get("slugPrimarySegment"))
    company = item.get("company") if isinstance(item.get("company"), dict) else {}
    company_slug = _clean_string(company.get("slug"))

    if slug and company_slug:
        return f"{DEALLS_LISTING_URL}/{slug}~{company_slug}"
    if slug:
        return f"{DEALLS_LISTING_URL}/{slug}"
    return DEALLS_LISTING_URL


def _collect_tags(item: dict[str, Any]) -> list[str]:
    tags: list[str] = []

    workplace_type = item.get("workplaceType")
    if isinstance(workplace_type, str) and workplace_type:
        tags.append(_humanize_label(workplace_type))

    skills = item.get("skills")
    if isinstance(skills, list):
        for skill in skills:
            if not isinstance(skill, dict):
                continue
            name = _clean_string(skill.get("name"))
            if name:
                tags.append(name)

    deduped: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if tag in seen:
            continue
        deduped.append(tag)
        seen.add(tag)
    return deduped


__all__ = ["fetch_listing_page", "parse_jobs", "scrape"]
