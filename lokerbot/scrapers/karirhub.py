from __future__ import annotations

import re
import time
import warnings
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import Page, Playwright, sync_playwright

from lokerbot.http_client import build_session
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
DEFAULT_BROWSER_NAME = "chromium"
PAGE_TIMEOUT_MS = 120_000
LISTING_PAGE_SIZE = 18


def fetch_listing_page(page_number: int = 1, browser_name: str = DEFAULT_BROWSER_NAME) -> str:
    if page_number < 1:
        raise ValueError("page_number must be at least 1")

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, browser_name)
        try:
            context = browser.new_context(locale="id-ID", viewport={"width": 1280, "height": 720})
            try:
                page = context.new_page()
                _load_listing_page(page, page_number)
                return page.content()
            finally:
                context.close()
        finally:
            browser.close()


def parse_jobs(html: str, payload: dict[str, Any], scraped_at: str | None = None) -> list[Job]:
    timestamp = scraped_at or utc_now_iso()
    scraped_at_dt = _parse_iso_datetime(timestamp)
    if scraped_at_dt is None:
        raise ValueError("scraped_at must be a valid ISO 8601 timestamp")

    return _parse_listing_jobs(html, payload, scraped_at=timestamp, scraped_at_dt=scraped_at_dt)


def scrape(
    max_pages: int | None = 1,
    fetch_details: bool = False,
    delay: float = 0.0,
    session: requests.Session | None = None,
    browser_name: str = DEFAULT_BROWSER_NAME,
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

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, browser_name)
        try:
            context = browser.new_context(locale="id-ID", viewport={"width": 1280, "height": 720})
            try:
                page = context.new_page()
                if progress is not None:
                    progress(f"loading page {page_number}")
                _load_listing_page(page, page_number)

                while True:
                    html = page.content()
                    payload = _fetch_listing_page_data(page, page_number)
                    page_jobs = _parse_listing_jobs(
                        html,
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

                    if fetch_details:
                        for job in new_jobs:
                            try:
                                _enrich_job_from_detail(session, job)
                            except Exception as exc:
                                warnings.warn(
                                    f"Failed to enrich Karirhub job {job.job_id} ({job.title}): {exc}",
                                    RuntimeWarning,
                                )
                            if delay > 0:
                                time.sleep(delay)

                    all_jobs.extend(new_jobs)

                    if progress is not None:
                        progress(f"page {page_number} • {len(new_jobs)} jobs")

                    if max_pages is not None and page_number >= max_pages:
                        break

                    if not _click_next_listing_page(page):
                        break

                    page_number += 1
                    if progress is not None:
                        progress(f"loading page {page_number}")
                    if delay > 0:
                        time.sleep(delay)
            finally:
                context.close()
        finally:
            browser.close()

    if owns_session:
        session.close()

    if progress is not None:
        progress(f"done • {len(all_jobs)} jobs")

    return all_jobs


def _launch_browser(playwright: Playwright, browser_name: str):
    if browser_name not in {"chromium", "firefox", "webkit"}:
        raise ValueError("browser_name must be chromium, firefox, or webkit")
    return getattr(playwright, browser_name).launch(headless=True)


def _load_listing_page(page: Page, page_number: int) -> None:
    if page_number < 1:
        raise ValueError("page_number must be at least 1")

    page.goto(KARIRHUB_LISTING_URL, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
    for _ in range(1, page_number):
        if not _click_next_listing_page(page):
            break


def _click_next_listing_page(page: Page) -> bool:
    pagination_buttons = page.locator("sisnaker-element-common-pagination-button-web ion-button")
    if pagination_buttons.count() == 0:
        return False

    next_button = pagination_buttons.last
    if next_button.get_attribute("disabled") is not None or next_button.get_attribute("aria-disabled") == "true":
        return False

    previous_url = page.url
    next_button.locator("button").click()
    page.wait_for_function(
        "oldUrl => window.location.href !== oldUrl",
        arg=previous_url,
        timeout=PAGE_TIMEOUT_MS,
    )
    return True


def _fetch_listing_page_data(page: Page, page_number: int) -> dict[str, Any]:
    api_url = f"{KARIRHUB_LISTING_API_URL}?page={page_number}&limit={LISTING_PAGE_SIZE}"
    payload = page.evaluate(
        """async (url) => {
            const response = await fetch(url, { headers: { accept: 'application/json' } });
            if (!response.ok) {
                throw new Error(`Karirhub listing API returned ${response.status}`);
            }
            return await response.json();
        }""",
        api_url,
    )
    if not isinstance(payload, dict):
        raise ValueError("Karirhub listing API did not return a JSON object")
    return payload


def _parse_listing_jobs(
    html: str,
    payload: dict[str, Any],
    *,
    scraped_at: str,
    scraped_at_dt: datetime,
) -> list[Job]:
    cards = _extract_listing_cards(html)
    api_items = _extract_listing_items(payload)
    jobs: list[Job] = []

    for card, item in zip(cards, api_items):
        job = _parse_listing_card(card, item, scraped_at=scraped_at)
        if job is None:
            continue
        if not _is_recent_job_post(job.posted_at, scraped_at_dt):
            continue
        jobs.append(job)

    return jobs


def _extract_listing_cards(html: str) -> list[Any]:
    soup = BeautifulSoup(html, "html.parser")
    return list(soup.select("sisnaker-element-karirhub-domestic-vacancy-card-web"))


def _extract_listing_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("data")
    if not isinstance(items, list):
        raise ValueError("Karirhub listing payload did not include a data list")

    listing_items: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            listing_items.append(item)
    return listing_items


def _parse_listing_card(card: Any, item: dict[str, Any], *, scraped_at: str) -> Job | None:
    job_id = _clean_string(item.get("id") or item.get("_id") or item.get("job_id"))
    title = _clean_string(_extract_card_text(card, "div.header-section > div:nth-of-type(1)")) or _clean_string(
        item.get("title")
    )
    company = _clean_string(_extract_card_text(card, "div.header-section > div:nth-of-type(2)")) or _clean_string(
        item.get("company_name")
    )
    if not job_id or not title or not company:
        return None

    posted_at = _format_posted_at(item.get("published_at"))
    if posted_at is None:
        return None

    location = _clean_string(_extract_card_text(card, "div.header-section > div:nth-of-type(3)")) or _clean_string(
        item.get("city_name")
    )
    job_type = _clean_string(item.get("job_type_name"))
    salary_range = _format_salary_range(card, item)
    tags = _collect_tags(item)
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
    )


def _extract_card_text(card: Any, selector: str) -> str | None:
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


def _format_salary_range(card: Any, item: dict[str, Any]) -> str | None:
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
    return f"{KARIRHUB_LISTING_URL}/{slug}-{job_id}"


def _enrich_job_from_detail(session: requests.Session, job: Job) -> None:
    response = session.get(job.url, timeout=30)
    response.raise_for_status()
    detail_html = response.text
    detail = _parse_detail_page(detail_html)

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
        raise ValueError("Karirhub detail page did not contain any text")

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
    if candidate is None:
        return False
    if current is None:
        return True
    if current == candidate:
        return False
    if current.lower() == "indonesia":
        return True
    return candidate.count(",") > current.count(",")


__all__ = ["fetch_listing_page", "parse_jobs", "scrape"]
