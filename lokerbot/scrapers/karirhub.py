from __future__ import annotations

import re
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

from lokerbot.http_client import SessionPool, build_session
from lokerbot.models import Job, utc_now_iso
from lokerbot.utils import (
    clean_string as _clean_string,
    dedupe_list as _dedupe_list,
    is_recent_job_post as _is_recent_job_post,
    normalize_description_text as _normalize_description_text,
    parse_iso_datetime as _parse_iso_datetime,
)

KARIRHUB_LISTING_URL = "https://karirhub.kemnaker.go.id/lowongan-dalam-negeri/lowongan"
KARIRHUB_LISTING_API_URL = "https://api.kemnaker.go.id/karirhub/catalogue/v1/industrial-vacancies"
LISTING_PAGE_SIZE = 18
DETAIL_WORKER_COUNT = 10  # Balanced for performance vs rate limiting


def fetch_listing_page(
    page_number: int = 1,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    if page_number < 1:
        raise ValueError("page_number must be at least 1")

    owns_session = session is None
    session = session or build_session()
    try:
        return _fetch_listing_page_data(session, page_number)
    finally:
        if owns_session:
            session.close()


def parse_jobs(
    html_or_payload: str | dict[str, Any],
    payload: dict[str, Any] | None = None,
    scraped_at: str | None = None,
) -> list[Job]:
    listing_payload = payload if payload is not None else html_or_payload
    if not isinstance(listing_payload, dict):
        raise ValueError("Karirhub parse_jobs requires a payload dictionary")

    timestamp = scraped_at or utc_now_iso()
    scraped_at_dt = _parse_iso_datetime(timestamp)
    if scraped_at_dt is None:
        raise ValueError("scraped_at must be a valid ISO 8601 timestamp")

    return _parse_listing_jobs(listing_payload, scraped_at=timestamp, scraped_at_dt=scraped_at_dt)


def scrape(
    max_pages: int | None = 1,
    fetch_details: bool = False,
    delay: float = 0.0,
    session: requests.Session | None = None,
    progress: Any | None = None,
) -> list[Job]:
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if delay < 0:
        raise ValueError("delay must be non-negative")

    owns_session = session is None
    session = session or build_session()

    scraped_at_dt = datetime.now(tz=timezone.utc)
    scraped_at = scraped_at_dt.isoformat().replace("+00:00", "Z")
    all_jobs: list[Job] = []
    seen_job_ids: set[str] = set()
    page_number = 1
    last_page: int | None = None
    detail_executor: ThreadPoolExecutor | None = None
    session_pool: SessionPool | None = None
    pending_detail_futures: list[tuple[Job, Any]] = []
    batch_size = 5  # Process details in batches to avoid accumulating too many pending futures

    # If a session is provided externally, use it directly for detail fetching (for testing/mocking)
    # Otherwise, create a session pool for better performance
    use_session_pool = owns_session

    try:
        if fetch_details:
            detail_executor = ThreadPoolExecutor(max_workers=DETAIL_WORKER_COUNT)
            if use_session_pool:
                session_pool = SessionPool(size=DETAIL_WORKER_COUNT)

        while True:
            if last_page is not None:
                progress_last_page = str(min(last_page, max_pages)) if max_pages is not None else str(last_page)
            else:
                progress_last_page = None

            if progress is not None:
                loading_page_text = (
                    f"loading page {page_number}/{progress_last_page}"
                    if progress_last_page is not None
                    else f"loading page {page_number}"
                )
                progress(loading_page_text)

            try:
                payload = fetch_listing_page(page_number=page_number, session=session)
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if page_number > 1 and status_code == 400:
                    warnings.warn(
                        f"Karirhub listing API rejected page {page_number}; stopping pagination at page {page_number - 1}.",
                        RuntimeWarning,
                    )
                    break
                raise

            payload_last_page = _extract_last_page(payload)
            if payload_last_page is not None:
                last_page = payload_last_page

            page_jobs = _parse_listing_jobs(
                payload,
                scraped_at=scraped_at,
                scraped_at_dt=scraped_at_dt,
            )

            new_jobs: list[Job] = []
            for job in page_jobs:
                if job.job_id in seen_job_ids:
                    continue
                seen_job_ids.add(job.job_id)
                new_jobs.append(job)

            if not new_jobs:
                break

            if fetch_details and detail_executor is not None:
                if session_pool is not None:
                    pending_detail_futures.extend(
                        _enrich_jobs_from_detail(detail_executor, session_pool, new_jobs, delay=delay)
                    )
                else:
                    # Use provided session directly (for testing)
                    pending_detail_futures.extend(
                        _enrich_jobs_from_detail_with_session(detail_executor, session, new_jobs, delay=delay)
                    )

                # Drain batch if we've accumulated enough futures
                if len(pending_detail_futures) >= batch_size * LISTING_PAGE_SIZE:
                    _drain_pending_detail_enrichment(pending_detail_futures[:batch_size * LISTING_PAGE_SIZE])
                    pending_detail_futures = pending_detail_futures[batch_size * LISTING_PAGE_SIZE:]

            all_jobs.extend(new_jobs)

            if progress is not None:
                page_text = f"{page_number}/{progress_last_page}" if progress_last_page is not None else str(page_number)
                progress(f"page {page_text} • {len(all_jobs)} jobs")

            if max_pages is not None and page_number >= max_pages:
                break

            page_number += 1
            if delay > 0:
                time.sleep(delay)
    finally:
        try:
            _drain_pending_detail_enrichment(pending_detail_futures)
        finally:
            if session_pool is not None:
                session_pool.close_all()
            if detail_executor is not None:
                detail_executor.shutdown(wait=True)
            if owns_session:
                session.close()

    if progress is not None:
        progress(f"done • {len(all_jobs)} jobs")

    return all_jobs


def _enrich_jobs_from_detail(
    executor: ThreadPoolExecutor,
    session_pool: SessionPool,
    jobs: list[Job],
    *,
    delay: float,
) -> list[tuple[Job, Any]]:
    jobs_to_enrich = [job for job in jobs if _job_needs_detail_enrichment(job)]
    if not jobs_to_enrich:
        return []

    return [
        (job, executor.submit(_enrich_job_with_delay, session_pool, job, delay))
        for job in jobs_to_enrich
    ]


def _enrich_jobs_from_detail_with_session(
    executor: ThreadPoolExecutor,
    session: requests.Session,
    jobs: list[Job],
    *,
    delay: float,
) -> list[tuple[Job, Any]]:
    """Enrich jobs using a single shared session (for testing/mocking)."""
    jobs_to_enrich = [job for job in jobs if _job_needs_detail_enrichment(job)]
    if not jobs_to_enrich:
        return []

    return [
        (job, executor.submit(_enrich_job_with_delay_using_session, session, job, delay))
        for job in jobs_to_enrich
    ]


def _drain_pending_detail_enrichment(pending_detail_futures: list[tuple[Job, Any]]) -> None:
    if not pending_detail_futures:
        return

    future_to_job = {future: job for job, future in pending_detail_futures}
    for future in as_completed(future_to_job):
        job = future_to_job[future]
        try:
            future.result()
        except Exception as exc:
            warnings.warn(
                f"Failed to enrich Karirhub job {job.job_id} ({job.title}): {exc}",
                RuntimeWarning,
            )


def _enrich_job_with_delay(session_pool: SessionPool, job: Job, delay: float) -> None:
    session = session_pool.acquire()
    try:
        _enrich_job_from_detail(session, job)
    finally:
        if delay > 0:
            time.sleep(delay)


def _enrich_job_with_delay_using_session(session: requests.Session, job: Job, delay: float) -> None:
    """Enrich job using a single shared session (for testing/mocking)."""
    try:
        _enrich_job_from_detail(session, job)
    finally:
        if delay > 0:
            time.sleep(delay)


def _job_needs_detail_enrichment(job: Job) -> bool:
    return job.location is None or job.job_type is None or job.salary_range is None or not job.tags or job.description is None


def _fetch_listing_page_data(session: requests.Session, page_number: int) -> dict[str, Any]:
    response = session.get(
        KARIRHUB_LISTING_API_URL,
        params={"page": page_number, "limit": LISTING_PAGE_SIZE},
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Karirhub listing API did not return a JSON object")
    return payload


def _parse_listing_jobs(
    payload: dict[str, Any],
    *,
    scraped_at: str,
    scraped_at_dt: datetime,
) -> list[Job]:
    api_items = _extract_listing_items(payload)
    jobs: list[Job] = []

    for item in api_items:
        job = _parse_listing_item(item, scraped_at=scraped_at)
        if job is None:
            continue
        if not _is_recent_job_post(job.posted_at, scraped_at_dt):
            continue
        jobs.append(job)

    return jobs


def _extract_last_page(payload: dict[str, Any]) -> int | None:
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None

    last_page = meta.get("last_page")
    if isinstance(last_page, int) and last_page > 0:
        return last_page
    return None


def _extract_listing_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("data")
    if not isinstance(items, list):
        raise ValueError("Karirhub listing payload did not include a data list")

    listing_items: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            listing_items.append(item)
    return listing_items


def _parse_listing_item(item: dict[str, Any], *, scraped_at: str) -> Job | None:
    job_id = _clean_string(item.get("id") or item.get("_id") or item.get("job_id"))
    title = _clean_string(item.get("title"))
    company = _clean_string(item.get("company_name"))
    if not job_id or not title or not company:
        return None

    posted_at = _format_posted_at(item.get("published_at"))
    if posted_at is None:
        return None

    location = _clean_string(item.get("city_name") or item.get("location"))
    job_type = _clean_string(item.get("job_type_name") or item.get("job_type"))
    salary_range = _format_salary_range(None, item)
    tags = _collect_tags(item)
    description = _clean_string(item.get("description") or item.get("job_description") or item.get("description_text"))
    url = _build_detail_url(title, job_id)

    return Job(
        job_id=job_id,
        title=title,
        company=company,
        location=location,
        job_type=job_type,
        salary_range=salary_range,
        url=url,
        tags=tags,
        posted_at=posted_at,
        scraped_at=scraped_at,
        description=description,
    )


def _extract_card_text(card: Any, selector: str) -> str | None:
    if card is None:
        return None
    node = card.select_one(selector)
    if node is None:
        return None
    return _clean_string(node.get_text(" ", strip=True))


def _format_posted_at(value: Any) -> str | None:
    if isinstance(value, int):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OSError, OverflowError, ValueError):
            return None
    return None


def _format_salary_range(card: Any | None, item: dict[str, Any]) -> str | None:
    if card is not None:
        card_salary = _extract_card_text(card, "sisnaker-element-karirhub-vacancy-price")
        if card_salary:
            return None if card_salary == "Dirahasiakan" else card_salary

    if not item.get("show_salary"):
        return None

    min_amount = item.get("min_salary_amount")
    max_amount = item.get("max_salary_amount")
    if isinstance(min_amount, int) and isinstance(max_amount, int):
        return f"Rp {min_amount:,} - Rp {max_amount:,}"
    if isinstance(min_amount, int):
        return f"From Rp {min_amount:,}"
    if isinstance(max_amount, int):
        return f"Up to Rp {max_amount:,}"
    return None


def _collect_tags(item: dict[str, Any]) -> list[str]:
    tags: list[str] = []

    skills = item.get("skills")
    if isinstance(skills, list):
        for skill in skills:
            if isinstance(skill, str):
                cleaned = _clean_string(skill)
                if cleaned:
                    tags.append(cleaned)

    if not tags:
        job_function_name = _clean_string(item.get("job_function_name"))
        if job_function_name:
            tags.append(job_function_name)

    return _dedupe_list(tags)


def _build_detail_url(title: str, job_id: str) -> str:
    slug = re.sub(r"\s+", "-", title.strip().lower())
    slug = slug.replace("/", "-")
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return f"{KARIRHUB_LISTING_URL}/{slug}-{job_id}"


def _enrich_job_from_detail(session: requests.Session, job: Job) -> None:
    try:
        response = session.get(job.url, timeout=30)
        response.raise_for_status()
        detail_html = response.text
    except Exception as exc:
        warnings.warn(
            f"Failed to fetch detail page for job {job.job_id} ({job.title}): {exc}",
            RuntimeWarning,
        )
        return

    try:
        detail = _parse_detail_page(detail_html)
    except Exception as exc:
        warnings.warn(
            f"Failed to parse detail page for job {job.job_id} ({job.title}): {exc}",
            RuntimeWarning,
        )
        return

    detail_location = _clean_string(detail.get("location"))
    if _should_replace_location(job.location, detail_location):
        job.location = detail_location

    detail_job_type = _clean_string(detail.get("job_type"))
    if detail_job_type and job.job_type is None:
        job.job_type = detail_job_type

    detail_salary_range = _clean_string(detail.get("salary_range"))
    if detail_salary_range == "Dirahasiakan":
        detail_salary_range = None
    if detail_salary_range and job.salary_range is None:
        job.salary_range = detail_salary_range

    detail_tags = detail.get("tags")
    if isinstance(detail_tags, list):
        cleaned_tags = [tag for tag in (_clean_string(tag) for tag in detail_tags) if tag]
        if cleaned_tags and (not job.tags or len(cleaned_tags) > len(job.tags)):
            job.tags = _dedupe_list(cleaned_tags)

    description = _clean_string(detail.get("description"))
    if description and job.description is None:
        job.description = description


def _parse_detail_page(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    lines = _extract_text_lines(soup)
    if not lines:
        warnings.warn("Karirhub detail page did not contain any text", RuntimeWarning)
        return {
            "title": None,
            "location": None,
            "posted_line": None,
            "salary_range": None,
            "job_type": None,
            "tags": [],
            "description": None,
        }

    title = _find_first_line_after(lines, "Lowongan dalam negeri")
    location = _find_line_after_title(lines, title)
    posted_line = _find_line_after_location(lines, location)
    salary_range = _find_line_after_label(lines, "Rentang gaji")
    if salary_range == "Dirahasiakan":
        salary_range = None
    job_type = _find_line_after_label(lines, "Jenis pekerjaan")
    tags = _find_section_lines(lines, "Keterampilan", stop_tokens=("PT Duta Generasi Mandiri", "Lowongan dari"))
    description = _build_description_from_lines(lines)

    if not description:
        meta = soup.find("meta", attrs={"name": "description"})
        if meta is not None:
            description = _clean_string(meta.get("content"))

    return {
        "title": title,
        "location": location,
        "posted_line": posted_line,
        "salary_range": salary_range,
        "job_type": job_type,
        "tags": tags,
        "description": description,
    }


def _extract_text_lines(soup: BeautifulSoup) -> list[str]:
    text = soup.get_text("\n")
    lines: list[str] = []
    for line in text.splitlines():
        cleaned = _clean_string(line)
        if cleaned:
            lines.append(cleaned)
    return lines


def _find_first_line_after(lines: list[str], marker: str) -> str | None:
    try:
        index = lines.index(marker)
    except ValueError:
        return None
    if index + 1 < len(lines):
        return lines[index + 1]
    return None


def _find_line_after_title(lines: list[str], title: str | None) -> str | None:
    if title is None:
        return None
    try:
        index = lines.index(title)
    except ValueError:
        return None
    if index + 1 < len(lines):
        return lines[index + 1]
    return None


def _find_line_after_location(lines: list[str], location: str | None) -> str | None:
    if location is None:
        return None
    try:
        index = lines.index(location)
    except ValueError:
        return None
    if index + 1 < len(lines):
        return lines[index + 1]
    return None


def _find_line_after_label(lines: list[str], label: str) -> str | None:
    try:
        index = lines.index(label)
    except ValueError:
        return None
    if index + 1 < len(lines):
        return lines[index + 1]
    return None


def _find_section_lines(lines: list[str], heading: str, *, stop_tokens: tuple[str, ...]) -> list[str]:
    try:
        start = lines.index(heading) + 1
    except ValueError:
        return []

    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index] in stop_tokens:
            end = index
            break

    return lines[start:end]


def _build_description_from_lines(lines: list[str]) -> str | None:
    sections: list[str] = []
    section_specs = [
        ("Deskripsi Pekerjaan", ("Persyaratan Khusus", "Persyaratan Umum", "Keterampilan", "PT Duta Generasi Mandiri", "Lowongan dari")),
        ("Persyaratan Khusus", ("Persyaratan Umum", "Keterampilan", "PT Duta Generasi Mandiri", "Lowongan dari")),
        ("Persyaratan Umum", ("Keterampilan", "PT Duta Generasi Mandiri", "Lowongan dari")),
        ("Keterampilan", ("PT Duta Generasi Mandiri", "Lowongan dari")),
    ]

    for heading, stop_tokens in section_specs:
        section_lines = _find_section_lines(lines, heading, stop_tokens=stop_tokens)
        if not section_lines:
            continue
        joined = "\n".join([heading, *section_lines])
        cleaned = _normalize_description_text(joined)
        if cleaned:
            sections.append(cleaned)

    if not sections:
        return None
    return "\n\n".join(sections)


def _should_replace_location(current: str | None, candidate: str | None) -> bool:
    """Determine if candidate location should replace current location.

    Prioritizes more specific locations (e.g., "Jakarta Selatan, DKI Jakarta" over "Indonesia").
    Uses length, comma count, and specificity heuristics.
    """
    if candidate is None:
        return False
    if current is None:
        return True
    if current == candidate:
        return False

    # Always replace generic "Indonesia" with anything more specific
    if current.lower() == "indonesia":
        return True
    if candidate.lower() == "indonesia":
        return False

    # Count commas as a proxy for specificity (e.g., "City, Province" is more specific than "Province")
    current_commas = current.count(",")
    candidate_commas = candidate.count(",")

    if candidate_commas > current_commas:
        return True
    if candidate_commas < current_commas:
        return False

    # If comma count is equal, prefer longer string (more detail)
    # But only if the difference is significant (>10 chars)
    if len(candidate) > len(current) + 10:
        return True

    # Default: keep current
    return False


__all__ = ["fetch_listing_page", "parse_jobs", "scrape"]
