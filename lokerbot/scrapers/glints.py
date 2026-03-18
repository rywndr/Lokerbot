from __future__ import annotations

import json
import re
import time
import warnings
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import Page, Playwright, sync_playwright

from lokerbot.models import Job, utc_now_iso
from lokerbot.nextjs import extract_next_data
from lokerbot.utils import (
    clean_string as _clean_string,
    dedupe_list as _dedupe,
    humanize_label as _humanize_label,
    is_recent_job_post as _is_recent_job_post,
    normalize_description_text as _normalize_description_text,
    parse_iso_datetime as _parse_iso_datetime,
)

GLINTS_LISTING_URL = "https://glints.com/id/lowongan-kerja"
GLINTS_JOB_PATH = "/id/opportunities/jobs/"
DEFAULT_BROWSER_NAME = "firefox"
PAGE_TIMEOUT_MS = 120_000
LOGIN_GATE_TEXT = "Login untuk lihat loker lebih banyak"
JOB_ID_PATTERN = re.compile(r"/id/opportunities/jobs/[^/]+/(?P<job_id>[0-9a-f-]+)")
WORK_ARRANGEMENT_LABELS = {
    "HYBRID": "Hybrid",
    "ONSITE": "On Site",
    "REMOTE": "Remote",
}


def fetch_listing_page(page_number: int = 1, browser_name: str = DEFAULT_BROWSER_NAME) -> str:
    if page_number < 1:
        raise ValueError("page_number must be at least 1")

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, browser_name)
        try:
            context = browser.new_context(locale="id-ID", viewport={"width": 1280, "height": 720})
            try:
                page = context.new_page()
                return _fetch_listing_snapshot(page, page_number)["html"]
            finally:
                context.close()
        finally:
            browser.close()


def parse_jobs(
    payload: dict[str, Any],
    *,
    job_urls: dict[str, str] | None = None,
    scraped_at: str | None = None,
) -> list[Job]:
    page_data = _extract_jobs_page(payload)
    raw_jobs = page_data.get("jobsInPage")
    if not isinstance(raw_jobs, list):
        raise ValueError("Glints payload did not include a jobsInPage list")

    timestamp = scraped_at or utc_now_iso()
    url_map = job_urls or {}
    jobs: list[Job] = []
    for item in raw_jobs:
        if not isinstance(item, dict):
            continue
        job = _parse_job_record(item, job_urls=url_map, scraped_at=timestamp)
        if job is not None:
            jobs.append(job)
    return jobs


def scrape(
    max_pages: int | None = 1,
    fetch_details: bool = False,
    delay: float = 0.0,
    browser_name: str = DEFAULT_BROWSER_NAME,
    progress: Any | None = None,
) -> list[Job]:
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if delay < 0:
        raise ValueError("delay must be non-negative")

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, browser_name)
        try:
            context = browser.new_context(locale="id-ID", viewport={"width": 1280, "height": 720})
            try:
                return _scrape_with_context(
                    context,
                    max_pages=max_pages,
                    fetch_details=fetch_details,
                    delay=delay,
                    progress=progress,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _scrape_with_context(
    context: Any,
    *,
    max_pages: int | None,
    fetch_details: bool,
    delay: float,
    progress: Any | None = None,
) -> list[Job]:
    listing_page = context.new_page()
    detail_page: Page | None = None
    scraped_at = utc_now_iso()
    scraped_at_dt = _parse_iso_datetime(scraped_at)
    if scraped_at_dt is None:
        raise ValueError("scraped_at must be a valid ISO 8601 timestamp")

    jobs: list[Job] = []
    seen_job_ids: set[str] = set()
    page_number = 1
    detail_fetch_count = 0

    while True:
        if page_number > 1 and delay:
            time.sleep(delay)

        if progress is not None:
            page_text = f"page {page_number}/{max_pages}" if max_pages is not None else f"page {page_number}"
            progress(f"loading {page_text}")

        snapshot = _fetch_listing_snapshot(listing_page, page_number)
        try:
            next_data = extract_next_data(snapshot["html"])
            page_data = _extract_jobs_page(next_data)
        except ValueError:
            if page_number == 1:
                raise
            _warn_unloadable_page(snapshot["body_text"], page_number)
            break

        raw_jobs = page_data.get("jobsInPage")
        if not isinstance(raw_jobs, list):
            raise ValueError("Glints payload did not include a jobsInPage list")
        if not raw_jobs:
            if page_number == 1:
                raise ValueError("Glints listing page did not expose any public job listings")
            _warn_unloadable_page(snapshot["body_text"], page_number)
            break

        page_job_urls = _extract_job_urls(snapshot["html"])
        page_jobs: list[Job] = []
        for item in raw_jobs:
            if not isinstance(item, dict):
                continue
            job = _parse_job_record(item, job_urls=page_job_urls, scraped_at=scraped_at)
            if job is None or not _is_recent_job_post(job.posted_at, scraped_at_dt):
                continue

            if _should_fetch_detail(job, force=fetch_details):
                if detail_page is None:
                    detail_page = context.new_page()
                if delay and detail_fetch_count:
                    time.sleep(delay)
                _enrich_job_from_detail(detail_page, job, include_description=fetch_details)
                detail_fetch_count += 1

            if job.job_id in seen_job_ids:
                continue
            seen_job_ids.add(job.job_id)
            page_jobs.append(job)

        jobs.extend(page_jobs)

        if progress is not None:
            page_text = f"page {page_number}/{max_pages}" if max_pages is not None else f"page {page_number}"
            progress(f"{page_text} • {len(page_jobs)} jobs")

        if max_pages is not None and page_number >= max_pages:
            break
        if not bool(page_data.get("hasMore")):
            break
        page_number += 1

    if progress is not None:
        progress(f"done • {len(jobs)} jobs")
    return jobs


def _launch_browser(playwright: Playwright, browser_name: str):
    if browser_name not in {"chromium", "firefox", "webkit"}:
        raise ValueError("browser_name must be chromium, firefox, or webkit")
    return getattr(playwright, browser_name).launch(headless=True)


def _fetch_listing_snapshot(page: Page, page_number: int) -> dict[str, str]:
    page.goto(_build_listing_url(page_number), wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
    return {
        "html": page.content(),
        "body_text": page.locator("body").inner_text(),
    }


def _fetch_detail_page_html(page: Page, url: str) -> str:
    page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
    return page.content()


def _build_listing_url(page_number: int) -> str:
    if page_number == 1:
        return GLINTS_LISTING_URL
    return f"{GLINTS_LISTING_URL}?page={page_number}"


def _warn_unloadable_page(body_text: str, page_number: int) -> None:
    if LOGIN_GATE_TEXT in body_text:
        warnings.warn(
            f"Glints page {page_number} rendered a login prompt instead of public job listings, so pagination stopped at page {page_number - 1}.",
            RuntimeWarning,
        )
        return

    warnings.warn(
        f"Glints page {page_number} did not expose any public job listings, so pagination stopped at page {page_number - 1}.",
        RuntimeWarning,
    )


def _extract_jobs_page(payload: dict[str, Any]) -> dict[str, Any]:
    page_props = payload.get("props", {}).get("pageProps", {})
    initial_jobs = page_props.get("initialJobs")
    if isinstance(initial_jobs, dict) and isinstance(initial_jobs.get("jobsInPage"), list):
        return initial_jobs

    apollo_cache = payload.get("props", {}).get("apolloCache")
    if isinstance(apollo_cache, dict):
        root_query = apollo_cache.get("ROOT_QUERY")
        if isinstance(root_query, dict):
            for key, value in root_query.items():
                if not key.startswith("searchJobsV3(") or not isinstance(value, dict):
                    continue
                resolved = _resolve_apollo_value(apollo_cache, value)
                if isinstance(resolved, dict) and isinstance(resolved.get("jobsInPage"), list):
                    return resolved

    raise ValueError("Could not find the Glints jobs payload inside __NEXT_DATA__")


def _parse_job_record(item: dict[str, Any], *, job_urls: dict[str, str], scraped_at: str) -> Job | None:
    job_id = _clean_string(item.get("id"))
    title = _clean_string(item.get("title"))
    company = item.get("company") if isinstance(item.get("company"), dict) else {}
    company_name = _clean_string(company.get("name"))
    url = _extract_job_url(item, job_urls)
    posted_at = _clean_string(item.get("createdAt") or item.get("updatedAt"))

    if not job_id or not title or not company_name or not url:
        return None
    if _parse_iso_datetime(posted_at) is None:
        return None

    return Job(
        job_id=job_id,
        title=title,
        company=company_name,
        location=_format_location(item),
        job_type=_format_job_type(item),
        salary_range=_format_salary_range(item),
        url=url,
        tags=_collect_tags(item),
        posted_at=posted_at,
        scraped_at=scraped_at,
    )


def _extract_job_url(item: dict[str, Any], job_urls: dict[str, str]) -> str | None:
    job_id = _clean_string(item.get("id"))
    if job_id and job_id in job_urls:
        return job_urls[job_id]

    direct_url = _clean_string(item.get("url"))
    if direct_url:
        return _normalize_job_url(direct_url)
    return None


def _extract_job_urls(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    job_urls: dict[str, str] = {}

    for card in soup.select(".job-search-results_job-card_link"):
        job_id = _clean_string(card.get("data-gtm-job-id"))
        anchor = card.select_one(f'a[href*="{GLINTS_JOB_PATH}"]')
        href = _clean_string(anchor.get("href")) if anchor is not None else None
        if job_id and href:
            job_urls.setdefault(job_id, _normalize_job_url(href))

    if job_urls:
        return job_urls

    for anchor in soup.select(f'a[href*="{GLINTS_JOB_PATH}"]'):
        href = _clean_string(anchor.get("href"))
        if not href:
            continue
        match = JOB_ID_PATTERN.search(href)
        if match is None:
            continue
        job_urls.setdefault(match.group("job_id"), _normalize_job_url(href))
    return job_urls


def _normalize_job_url(url: str) -> str:
    return urljoin(GLINTS_LISTING_URL, url.split("?", 1)[0])


def _enrich_job_from_detail(page: Page, job: Job, *, include_description: bool = False) -> None:
    detail_html = _fetch_detail_page_html(page, job.url)
    try:
        next_data = extract_next_data(detail_html)
    except ValueError:
        return

    detail = _extract_detail_job(next_data, job.job_id)
    if not isinstance(detail, dict):
        return

    detail_location = _format_location(detail)
    if _should_replace_location(job.location, detail_location):
        job.location = detail_location

    detail_job_type = _format_job_type(detail)
    if detail_job_type and (job.job_type is None or len(detail_job_type) > len(job.job_type)):
        job.job_type = detail_job_type

    job.salary_range = job.salary_range or _format_salary_range(detail)

    detail_tags = _collect_tags(detail)
    if detail_tags and (not job.tags or len(detail_tags) > len(job.tags)):
        job.tags = detail_tags

    if include_description:
        job.description = job.description or _extract_description(detail)


def _extract_detail_job(payload: dict[str, Any], job_id: str) -> dict[str, Any] | None:
    apollo_cache = payload.get("props", {}).get("apolloCache")
    if not isinstance(apollo_cache, dict):
        return None

    detail = apollo_cache.get(f"Job:{job_id}")
    if not isinstance(detail, dict):
        return None

    resolved = _resolve_apollo_value(apollo_cache, detail)
    return resolved if isinstance(resolved, dict) else None


def _resolve_apollo_value(cache: dict[str, Any], value: Any) -> Any:
    if isinstance(value, list):
        return [_resolve_apollo_value(cache, item) for item in value]
    if isinstance(value, dict):
        if set(value) == {"__ref"} and isinstance(value.get("__ref"), str):
            referenced = cache.get(value["__ref"])
            return _resolve_apollo_value(cache, referenced)
        return {
            key: _resolve_apollo_value(cache, nested)
            for key, nested in value.items()
            if key != "__typename"
        }
    return value


def _extract_description(detail: dict[str, Any]) -> str | None:
    raw_description = _clean_string(detail.get("descriptionJsonString"))
    if raw_description is None:
        return None

    try:
        parsed = json.loads(raw_description)
    except json.JSONDecodeError:
        return _normalize_description_text(raw_description)

    if not isinstance(parsed, dict):
        return None

    blocks = parsed.get("blocks")
    if not isinstance(blocks, list):
        return None

    lines: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text = _clean_string(block.get("text"))
        if text:
            lines.append(text)

    return _normalize_description_text("\n".join(lines))


def _should_fetch_detail(job: Job, *, force: bool) -> bool:
    if job.location is None or job.job_type is None:
        return True
    if force and (job.salary_range is None or not job.tags or job.description is None):
        return True
    return False


def _format_location(item: dict[str, Any]) -> str | None:
    if _clean_string(item.get("workArrangementOption")) == "REMOTE":
        return "Remote"

    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    city = item.get("city") if isinstance(item.get("city"), dict) else {}
    country = item.get("country") if isinstance(item.get("country"), dict) else {}

    city_name = _clean_string(city.get("name"))
    country_name = _clean_string(country.get("name"))
    location_name = _clean_string(location.get("formattedName") or location.get("name"))
    province_name: str | None = None

    parents = location.get("parents") if isinstance(location, dict) else None
    if isinstance(parents, list):
        names_by_level: dict[int, str] = {}
        for parent in parents:
            if not isinstance(parent, dict):
                continue
            level = parent.get("level")
            name = _clean_string(parent.get("formattedName") or parent.get("name"))
            if isinstance(level, int) and name:
                names_by_level[level] = name
        city_name = names_by_level.get(3) or city_name
        province_name = names_by_level.get(2)
        country_name = names_by_level.get(1) or country_name

    parts = [part for part in (city_name, province_name) if part]
    if parts:
        return ", ".join(_dedupe(parts))

    parts = [part for part in (location_name, country_name) if part]
    if parts:
        return ", ".join(_dedupe(parts))

    return country_name


def _format_job_type(item: dict[str, Any]) -> str | None:
    parts: list[str] = []

    job_type = _clean_string(item.get("type"))
    if job_type:
        parts.append(_humanize_label(job_type))

    work_arrangement = _format_work_arrangement(item.get("workArrangementOption"))
    if work_arrangement:
        parts.append(work_arrangement)

    formatted = _dedupe(parts)
    if not formatted:
        return None
    return ", ".join(formatted)


def _format_work_arrangement(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return WORK_ARRANGEMENT_LABELS.get(value, _humanize_label(value))


def _format_salary_range(item: dict[str, Any]) -> str | None:
    salary = _select_salary(item)
    if not isinstance(salary, dict):
        return None

    currency = _clean_string(salary.get("CurrencyCode")) or "IDR"
    mode = _clean_string(salary.get("salaryMode"))
    mode_suffix = f" / {_humanize_label(mode)}" if mode else ""
    minimum = salary.get("minAmount")
    maximum = salary.get("maxAmount")

    if _is_amount(minimum) and _is_amount(maximum):
        return f"{currency} {int(minimum):,} - {int(maximum):,}{mode_suffix}"
    if _is_amount(minimum):
        return f"From {currency} {int(minimum):,}{mode_suffix}"
    if _is_amount(maximum):
        return f"Up to {currency} {int(maximum):,}{mode_suffix}"
    return None


def _select_salary(item: dict[str, Any]) -> dict[str, Any] | None:
    salaries = item.get("salaries")
    if isinstance(salaries, list):
        for salary in salaries:
            if isinstance(salary, dict):
                return salary

    estimate = item.get("salaryEstimate")
    if isinstance(estimate, dict):
        return estimate
    return None


def _collect_tags(item: dict[str, Any]) -> list[str]:
    tags: list[str] = []

    work_arrangement = _format_work_arrangement(item.get("workArrangementOption"))
    if work_arrangement:
        tags.append(work_arrangement)

    skills = item.get("skills")
    if isinstance(skills, list):
        for skill in skills:
            if not isinstance(skill, dict):
                continue
            detail = skill.get("skill") if isinstance(skill.get("skill"), dict) else {}
            name = _clean_string(detail.get("name"))
            if name:
                tags.append(name)

    if not tags:
        category = item.get("hierarchicalJobCategory") if isinstance(item.get("hierarchicalJobCategory"), dict) else {}
        category_name = _clean_string(category.get("name"))
        if category_name:
            tags.append(category_name)

    return _dedupe(tags)

def _is_amount(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)

def _should_replace_location(current: str | None, candidate: str | None) -> bool:
    if candidate is None:
        return False
    if current is None:
        return True
    if current == candidate:
        return False
    if current.lower() == "indonesia":
        return True
    return current.count(",") < candidate.count(",")


__all__ = ["fetch_listing_page", "parse_jobs", "scrape"]
