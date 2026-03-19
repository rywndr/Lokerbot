from __future__ import annotations

import json
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import requests

from lokerbot.http_client import build_session as _build_session
from lokerbot.models import Job
from lokerbot.utils import (
    clean_string as _clean_string,
    dedupe_list as _dedupe_list,
    humanize_label as _humanize_label,
    is_recent_job_post as _is_recent_job_post,
    normalize_description_text as _normalize_description_text,
)

_GQL_ENDPOINT = "https://gql.kitalulus.com/graphql"
_OPERATION_NAME = "vacanciesV3"
_PERSISTED_QUERY_HASH = "4439d81c984afb32d9e1bae2196a3383ede6241f4742bc8c289e28340025dbf9"

_DEFAULT_LOCATION = "DKI JAKARTA"
_DEFAULT_AREA_TYPE = "PROVINCE"


def scrape(
    max_pages: int | None = 1,
    fetch_details: bool = False,
    delay: float = 0.0,
    session: requests.Session | None = None,
    progress: Any | None = None,
) -> list[Job]:
    if session is None:
        session = _build_session()

    scraped_at_dt = datetime.now(tz=timezone.utc)
    scraped_at = scraped_at_dt.isoformat().replace("+00:00", "Z")
    all_jobs: list[Job] = []
    page_num = 0
    total_pages_available = None

    while True:
        if max_pages is not None and page_num >= max_pages:
            break

        if progress is not None:
            progress(f"loading page {page_num + 1}")

        try:
            response_data = _fetch_vacancies_page(
                session=session,
                page=page_num,
                limit=20,
            )
        except Exception as e:
            if page_num == 0:
                raise ValueError(
                    f"Failed to fetch first page from KitaLulus API: {e}"
                ) from e
            print(
                f"Warning: Failed to fetch page {page_num + 1}, stopping pagination: {e}",
                file=sys.stderr,
                flush=True,
            )
            break

        if page_num == 0:
            elements = response_data.get("elements", 0)
            if elements > 0:
                total_pages_available = (elements + 19) // 20
                print(
                    f"Found {elements} jobs across ~{total_pages_available} pages on KitaLulus",
                    file=sys.stderr,
                    flush=True,
                )

        page_jobs = _parse_and_filter_jobs(
            vacancies_list=response_data.get("list", []),
            scraped_at=scraped_at,
            scraped_at_dt=scraped_at_dt,
        )

        if progress is not None:
            total_text = total_pages_available if total_pages_available is not None else "?"
            progress(f"page {page_num + 1}/{total_text} • {len(page_jobs)} jobs")

        if not page_jobs:
            print(
                f"No recent jobs found on page {page_num + 1}, stopping pagination",
                file=sys.stderr,
                flush=True,
            )
            break

        if fetch_details:
            for job in page_jobs:
                try:
                    _enrich_job_from_detail(session, job)
                    if delay > 0:
                        time.sleep(delay)
                except Exception as e:
                    print(
                        f"Warning: Failed to enrich job {job.job_id} ({job.title}): {e}",
                        file=sys.stderr,
                        flush=True,
                    )

        all_jobs.extend(page_jobs)

        has_next = response_data.get("hasNextPage", False)
        if not has_next:
            print(
                f"Reached last page at page {page_num + 1}",
                file=sys.stderr,
                flush=True,
            )
            break

        page_num += 1
        if delay > 0:
            time.sleep(delay)

    if progress is not None:
        progress(f"done • {len(all_jobs)} jobs")
    return all_jobs


def _fetch_vacancies_page(
    session: requests.Session,
    page: int = 0,
    limit: int = 20,
    location: str = _DEFAULT_LOCATION,
    area_type: str = _DEFAULT_AREA_TYPE,
) -> dict[str, Any]:
    variables = {
        "keyword": "",
        "filter": {"page": page, "limit": limit},
        "filters": [
            {"key": "sortBy", "value": ["isHighlighted"]},
            {"key": "fuzzySearch", "value": ["false"]},
        ],
        "locations": [{"areaType": area_type, "name": location}],
        "haveMisiSeruLimit": True,
    }

    extensions = {
        "persistedQuery": {"version": 1, "sha256Hash": _PERSISTED_QUERY_HASH}
    }

    params = {
        "operationName": _OPERATION_NAME,
        "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps(extensions, separators=(",", ":")),
    }

    url = f"{_GQL_ENDPOINT}?{urllib.parse.urlencode(params)}"

    headers = {
        "x-apollo-operation-name": _OPERATION_NAME,
        "content-type": "application/json",
    }

    response = session.get(url, headers=headers, timeout=30)

    if response.status_code != 200:
        raise ValueError(
            f"KitaLulus API returned status {response.status_code}: {response.text[:200]}"
        )

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON response from KitaLulus API: {e}") from e

    if "errors" in data:
        errors = data["errors"]
        raise ValueError(f"KitaLulus GraphQL errors: {errors}")

    if "data" not in data or "vacanciesV3" not in data["data"]:
        raise ValueError(
            f"Unexpected API response structure. Expected data.vacanciesV3, got: {list(data.keys())}"
        )

    return data["data"]["vacanciesV3"]


def _parse_and_filter_jobs(
    vacancies_list: list[dict[str, Any]],
    scraped_at: str,
    scraped_at_dt: datetime,
) -> list[Job]:
    jobs: list[Job] = []

    for vacancy_doc in vacancies_list:
        job = _parse_vacancy_doc(vacancy_doc, scraped_at, scraped_at_dt)
        if job is None:
            continue

        if not _is_recent_job_post(job.posted_at, scraped_at_dt):
            continue

        jobs.append(job)

    return jobs


def _parse_vacancy_doc(
    vacancy: dict[str, Any],
    scraped_at: str,
    scraped_at_dt: datetime,
) -> Job | None:
    job_id = vacancy.get("code")
    title = vacancy.get("positionName")
    slug = vacancy.get("slug")
    company_data = vacancy.get("company", {})
    company_name = company_data.get("name") if company_data else None

    if not job_id or not title or not slug or not company_name:
        return None

    url = f"https://www.kitalulus.com/lowongan/detail/{slug}"

    posted_at_dt = _parse_microsecond_timestamp(vacancy.get("updatedAt"))
    posted_at = posted_at_dt.isoformat().replace("+00:00", "Z") if posted_at_dt else None

    location = _format_location(vacancy)
    job_type = _format_job_type(vacancy)
    salary_range = _format_salary_range(vacancy)
    tags = _collect_tags(vacancy)
    description = _extract_description(vacancy)

    return Job(
        job_id=job_id,
        title=title,
        company=company_name,
        location=location,
        posted_at=posted_at,
        url=url,
        job_type=job_type,
        salary_range=salary_range,
        tags=tags,
        scraped_at=scraped_at,
        description=description,
    )


def _parse_microsecond_timestamp(timestamp: int | None) -> datetime | None:
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp / 1_000_000, tz=timezone.utc)
    except (ValueError, OSError):
        return None


def _format_location(vacancy: dict[str, Any]) -> str | None:
    city_data = vacancy.get("city", {})
    province_data = vacancy.get("province", {})

    city_name = city_data.get("name") if city_data else None
    province_name = province_data.get("name") if province_data else None

    parts = []
    if city_name:
        parts.append(city_name)
    if province_name:
        parts.append(province_name)

    if parts:
        return ", ".join(parts)
    return None


def _format_job_type(vacancy: dict[str, Any]) -> str | None:
    type_str = vacancy.get("typeStr")
    if type_str:
        return _clean_string(type_str)

    job_type = vacancy.get("type")
    if job_type:
        return _humanize_label(job_type)

    return None


def _format_salary_range(vacancy: dict[str, Any]) -> str | None:
    lower_str = vacancy.get("salaryLowerBoundStr")
    upper_str = vacancy.get("salaryUpperBoundStr")

    if (
        lower_str
        and upper_str
        and "Dinegosiasikan" in lower_str
        and "Dinegosiasikan" in upper_str
    ):
        return None

    lower = vacancy.get("salaryLowerBound", 0)
    upper = vacancy.get("salaryUpperBound", 0)

    if lower > 0 and upper > 0:
        return f"Rp {lower:,} - Rp {upper:,}"
    elif lower > 0:
        return f"Rp {lower:,}+"
    elif upper > 0:
        return f"Up to Rp {upper:,}"

    if lower_str and upper_str:
        return f"{lower_str} - {upper_str}"
    elif lower_str:
        return lower_str
    elif upper_str:
        return upper_str

    return None


def _collect_tags(vacancy: dict[str, Any]) -> list[str]:
    tags = []

    job_role = vacancy.get("jobRole", {})
    if job_role and job_role.get("displayName"):
        tags.append(job_role["displayName"])

    job_spec = vacancy.get("jobSpecialization", {})
    if job_spec and job_spec.get("displayName"):
        tags.append(job_spec["displayName"])

    job_func = vacancy.get("jobFunction")
    if job_func:
        tags.append(job_func)

    edu_level = vacancy.get("educationLevelStr")
    if edu_level:
        tags.append(edu_level)

    return _dedupe_list(tags)


def _extract_description(vacancy: dict[str, Any]) -> str | None:
    formatted_desc = vacancy.get("formattedDescription")
    if formatted_desc:
        return _normalize_description_text(formatted_desc)

    req_str = vacancy.get("requirementStr")
    if req_str:
        return _clean_string(req_str)

    return None


def _enrich_job_from_detail(session: requests.Session, job: Job) -> None:
    pass
