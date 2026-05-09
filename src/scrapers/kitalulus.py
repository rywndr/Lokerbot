from __future__ import annotations

import copy
import json
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from src.http_client import build_session as _build_session
from src.models import Job
from src.utils import (
    clean_string as _clean_string,
    dedupe_list as _dedupe_list,
    humanize_label as _humanize_label,
    is_recent_job_post as _is_recent_job_post,
    normalize_description_text as _normalize_description_text,
)

_LISTING_PAGE_URL = "https://www.kitalulus.com/lowongan"
_GQL_HOST = "gql.kitalulus.com"
_BOOTSTRAP_TIMEOUT_MS = 45_000
_LOAD_MORE_BUTTON_TEXT = "Lebih Banyak"
_DEFAULT_PAGE_LIMIT = 30
_VACANCIES_PAYLOAD_KEYS = ("vacanciesV4", "vacanciesV3")
_RELATIVE_TIME_PATTERN = re.compile(r"(\d+)\s+(menit|jam|hari|minggu|bulan|tahun)")
_DROP_REQUEST_HEADERS = frozenset(
    {
        "content-length",
        "host",
        "connection",
        "accept-encoding",
        "transfer-encoding",
    }
)


def scrape(
    max_pages: int | None = 1,
    fetch_details: bool = False,
    delay: float = 0.0,
    session: requests.Session | None = None,
    progress: Any | None = None,
    max_jobs: int | None = None,
) -> list[Job]:
    if max_jobs is not None and max_jobs < 1:
        raise ValueError("max_jobs must be at least 1")
    if session is None:
        session = _build_session()

    if progress is not None:
        progress("bootstrapping browser")
    request_template = _bootstrap_request_template()

    scraped_at_dt = datetime.now(tz=timezone.utc)
    scraped_at = scraped_at_dt.isoformat().replace("+00:00", "Z")
    all_jobs: list[Job] = []
    seen_ids: set[str] = set()
    page_num = 1
    total_pages_available: int | None = None
    per_page_count: int | None = None

    while True:
        if max_pages is not None and page_num > max_pages:
            break

        if progress is not None:
            if total_pages_available is not None:
                progress(f"loading page {page_num}/{total_pages_available}")
            else:
                progress(f"loading page {page_num}")

        try:
            response_data = _fetch_vacancies_page(
                session=session,
                template=request_template,
                page=page_num,
            )
        except Exception as e:
            if page_num == 1:
                raise ValueError(
                    f"Failed to fetch first page from KitaLulus API: {e}"
                ) from e
            if progress is not None:
                progress(f"warning: failed page {page_num}, stopping")
            else:
                print(
                    f"Warning: Failed to fetch page {page_num}, stopping pagination: {e}",
                    file=sys.stderr,
                    flush=True,
                )
            break

        list_items = response_data.get("list") or []
        if page_num == 1:
            per_page_count = len(list_items) or _DEFAULT_PAGE_LIMIT
            elements = response_data.get("elements", 0)
            if elements > 0 and per_page_count > 0:
                total_pages_available = (elements + per_page_count - 1) // per_page_count
                if progress is not None:
                    progress(f"found {elements} jobs (~{total_pages_available} pages)")
                else:
                    print(
                        f"Found {elements} jobs across ~{total_pages_available} pages on KitaLulus",
                        file=sys.stderr,
                        flush=True,
                    )

        page_jobs = _parse_and_filter_jobs(
            vacancies_list=list_items,
            scraped_at=scraped_at,
            scraped_at_dt=scraped_at_dt,
        )

        new_jobs: list[Job] = []
        for job in page_jobs:
            if job.job_id in seen_ids:
                continue
            seen_ids.add(job.job_id)
            new_jobs.append(job)

        if max_jobs is not None:
            remaining = max_jobs - len(all_jobs)
            if remaining <= 0:
                break
            if len(new_jobs) > remaining:
                new_jobs = new_jobs[:remaining]

        if progress is not None:
            total_text = total_pages_available if total_pages_available is not None else "?"
            progress(f"page {page_num}/{total_text} • {len(new_jobs)} jobs")

        if not page_jobs:
            if progress is not None:
                progress(f"no recent jobs on page {page_num}, stopping")
            else:
                print(
                    f"No recent jobs found on page {page_num}, stopping pagination",
                    file=sys.stderr,
                    flush=True,
                )
            break

        if fetch_details:
            for job in new_jobs:
                try:
                    _enrich_job_from_detail(session, job)
                    if delay > 0:
                        time.sleep(delay)
                except Exception as e:
                    if progress is not None:
                        progress(f"warning: failed to enrich {job.job_id}")
                    else:
                        print(
                            f"Warning: Failed to enrich job {job.job_id} ({job.title}): {e}",
                            file=sys.stderr,
                            flush=True,
                        )

        all_jobs.extend(new_jobs)

        if max_jobs is not None and len(all_jobs) >= max_jobs:
            break

        has_next = response_data.get("hasNextPage", False)
        if not has_next:
            if progress is not None:
                progress(f"page {page_num}: last page")
            else:
                print(
                    f"Reached last page at page {page_num}",
                    file=sys.stderr,
                    flush=True,
                )
            break

        page_num += 1
        if delay > 0:
            time.sleep(delay)

    if max_jobs is not None:
        all_jobs = all_jobs[:max_jobs]
    if progress is not None:
        progress(f"done • {len(all_jobs)} jobs")
    return all_jobs


def _bootstrap_request_template() -> dict[str, Any]:
    """Open the public listings page in headless Firefox, click the
    "Lebih Banyak" pagination button, intercept the resulting Vacancies
    GraphQL response, and return a request template that can be replayed
    via requests.Session for subsequent pages.
    """
    from playwright.sync_api import sync_playwright

    captured: dict[str, Any] = {}

    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=True)
        try:
            context = browser.new_context(
                locale="id-ID",
                viewport={"width": 1280, "height": 720},
            )
            try:
                page = context.new_page()

                def _maybe_capture(response) -> None:
                    if captured:
                        return
                    if _GQL_HOST not in response.url:
                        return

                    try:
                        data = response.json()
                    except Exception:
                        return

                    if not isinstance(data, dict) or data.get("errors"):
                        return
                    payload = data.get("data")
                    if not isinstance(payload, dict):
                        return
                    if not any(payload.get(key) for key in _VACANCIES_PAYLOAD_KEYS):
                        return

                    request = response.request
                    captured["method"] = request.method
                    captured["url"] = request.url
                    captured["headers"] = dict(request.headers)
                    captured["post_data"] = request.post_data

                page.on("response", _maybe_capture)

                try:
                    page.goto(
                        _LISTING_PAGE_URL,
                        wait_until="domcontentloaded",
                        timeout=_BOOTSTRAP_TIMEOUT_MS,
                    )
                except Exception:
                    pass

                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    pass

                button = page.get_by_role("button", name=_LOAD_MORE_BUTTON_TEXT).first
                try:
                    button.scroll_into_view_if_needed(timeout=10_000)
                    button.click(timeout=10_000)
                except Exception as exc:
                    raise RuntimeError(
                        "KitaLulus bootstrap failed: could not click the "
                        f"'{_LOAD_MORE_BUTTON_TEXT}' button. The site layout may have changed."
                    ) from exc

                deadline = time.monotonic() + (_BOOTSTRAP_TIMEOUT_MS / 1000)
                while not captured and time.monotonic() < deadline:
                    page.wait_for_timeout(200)

                if not captured:
                    raise RuntimeError(
                        "KitaLulus bootstrap failed: no successful Vacancies GraphQL "
                        "response was observed within "
                        f"{_BOOTSTRAP_TIMEOUT_MS // 1000}s. The site layout may have changed."
                    )
            finally:
                context.close()
        finally:
            browser.close()

    return _normalize_template(captured)


def _normalize_template(captured: dict[str, Any]) -> dict[str, Any]:
    method = (captured.get("method") or "POST").upper()
    url = captured["url"]
    raw_headers = captured.get("headers") or {}
    post_data = captured.get("post_data")

    headers = {
        key: value
        for key, value in raw_headers.items()
        if key.lower() not in _DROP_REQUEST_HEADERS
    }

    if method == "GET":
        parsed = urllib.parse.urlparse(url)
        endpoint = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        return {
            "method": "GET",
            "endpoint": endpoint,
            "headers": headers,
            "params": params,
            "body": None,
        }

    if isinstance(post_data, str) and post_data:
        try:
            body: Any = json.loads(post_data)
        except json.JSONDecodeError:
            body = post_data
    else:
        body = None

    return {
        "method": "POST",
        "endpoint": url,
        "headers": headers,
        "params": None,
        "body": body,
    }


def _fetch_vacancies_page(
    session: requests.Session,
    template: dict[str, Any],
    page: int,
) -> dict[str, Any]:
    method = template.get("method", "POST")
    headers = dict(template.get("headers") or {})

    if method == "GET":
        params = copy.deepcopy(template.get("params") or {})
        variables_raw = params.get("variables")
        if isinstance(variables_raw, str) and variables_raw:
            variables = json.loads(variables_raw)
            _bump_page(variables, page)
            params["variables"] = json.dumps(variables, separators=(",", ":"))
        response = session.get(
            template["endpoint"],
            params=params,
            headers=headers,
            timeout=30,
        )
    else:
        body = copy.deepcopy(template.get("body"))
        if isinstance(body, dict) and isinstance(body.get("variables"), dict):
            _bump_page(body["variables"], page)
            payload = json.dumps(body, separators=(",", ":"))
        elif isinstance(body, list):
            for entry in body:
                if isinstance(entry, dict) and isinstance(entry.get("variables"), dict):
                    _bump_page(entry["variables"], page)
            payload = json.dumps(body, separators=(",", ":"))
        elif isinstance(body, str):
            payload = body
        else:
            payload = json.dumps(body) if body is not None else ""
        if "content-type" not in {key.lower() for key in headers}:
            headers["content-type"] = "application/json"
        response = session.post(
            template["endpoint"],
            data=payload,
            headers=headers,
            timeout=30,
        )

    if response.status_code != 200:
        raise ValueError(
            f"KitaLulus API returned status {response.status_code}: {response.text[:200]}"
        )

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON response from KitaLulus API: {e}") from e

    if data.get("errors"):
        raise ValueError(f"KitaLulus GraphQL errors: {data['errors']}")

    payload = data.get("data") if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        raise ValueError(
            f"Unexpected API response structure. Expected data object, got: {type(data).__name__}"
        )

    for key in _VACANCIES_PAYLOAD_KEYS:
        if key in payload and payload[key] is not None:
            return payload[key]

    raise ValueError(
        f"Unexpected API response structure. Expected one of {_VACANCIES_PAYLOAD_KEYS}, got: {list(payload.keys())}"
    )


def _bump_page(variables: dict[str, Any], page: int) -> None:
    """Set the page index in whichever pagination key the API uses.

    KitaLulus has shipped two shapes: `variables.filter.{page,limit}`
    (legacy `vacanciesV3`) and `variables.pagination.{page,limit}` (current
    `vacanciesV4`). We mutate every one we find so a single template can
    page either API.
    """
    mutated = False
    for key in ("pagination", "filter"):
        slot = variables.get(key)
        if isinstance(slot, dict):
            slot["page"] = page
            mutated = True
    if not mutated:
        variables["pagination"] = {"page": page}


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
    company_data = vacancy.get("company") or {}
    company_name = company_data.get("name") if isinstance(company_data, dict) else None

    if not job_id or not title or not slug or not company_name:
        return None

    url = f"https://www.kitalulus.com/lowongan/detail/{slug}"

    posted_at_dt = _parse_microsecond_timestamp(vacancy.get("updatedAt"))
    if posted_at_dt is None:
        posted_at_dt = _parse_indonesian_relative_time(
            vacancy.get("updatedAtStr"), scraped_at_dt
        )
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


def _parse_indonesian_relative_time(
    text: str | None,
    now: datetime,
) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    lowered = text.lower()
    if "baru saja" in lowered or "hari ini" in lowered:
        return now
    if "kemarin" in lowered:
        return now - timedelta(days=1)

    match = _RELATIVE_TIME_PATTERN.search(lowered)
    if match is None:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "menit":
        return now - timedelta(minutes=amount)
    if unit == "jam":
        return now - timedelta(hours=amount)
    if unit == "hari":
        return now - timedelta(days=amount)
    if unit == "minggu":
        return now - timedelta(weeks=amount)
    if unit == "bulan":
        return now - timedelta(days=amount * 30)
    if unit == "tahun":
        return now - timedelta(days=amount * 365)
    return None


def _format_location(vacancy: dict[str, Any]) -> str | None:
    city_data = vacancy.get("city") or {}
    province_data = vacancy.get("province") or {}

    city_name = city_data.get("name") if isinstance(city_data, dict) else None
    province_name = province_data.get("name") if isinstance(province_data, dict) else None

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

    job_role = vacancy.get("jobRole") or {}
    if isinstance(job_role, dict) and job_role.get("displayName"):
        tags.append(job_role["displayName"])

    job_spec = vacancy.get("jobSpecialization") or {}
    if isinstance(job_spec, dict) and job_spec.get("displayName"):
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
