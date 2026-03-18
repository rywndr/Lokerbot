from __future__ import annotations

import json
import re
import time
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import Playwright, sync_playwright

from lokerbot.models import Job, utc_now_iso
from lokerbot.utils import (
    clean_string as _clean_string,
    dedupe_list as _dedupe_list,
    humanize_label as _humanize_label,
    is_recent_job_post as _is_recent_job_post,
    normalize_description_text as _normalize_description_text,
    parse_iso_datetime as _parse_iso_datetime,
)

LOKERID_LISTING_URL = "https://www.loker.id/cari-lowongan-kerja"
DEFAULT_BROWSER_NAME = "firefox"
PAGE_TIMEOUT_MS = 120_000
CARD_SELECTORS = (
    "article.job",
    ".job-card",
    "li.job",
    "[data-job-id]",
    ".jobs-list > li",
)


def parse_jobs(payload: dict[str, Any], scraped_at: str | None = None) -> list[Job]:
    timestamp = scraped_at or utc_now_iso()
    scraped_at_dt = _parse_iso_datetime(timestamp)
    if scraped_at_dt is None:
        raise ValueError("scraped_at must be a valid ISO 8601 timestamp")

    records = _extract_job_records(payload)
    jobs = [job for job in _build_jobs_from_records(records, scraped_at=timestamp, scraped_at_dt=scraped_at_dt) if _is_recent_job_post(job.posted_at, scraped_at_dt)]
    return jobs


def parse_listing_html(html: str, scraped_at: str | None = None) -> list[Job]:
    timestamp = scraped_at or utc_now_iso()
    scraped_at_dt = _parse_iso_datetime(timestamp)
    if scraped_at_dt is None:
        raise ValueError("scraped_at must be a valid ISO 8601 timestamp")

    jobs, _ = _parse_listing_html(html, scraped_at=timestamp, scraped_at_dt=scraped_at_dt)
    return jobs


def scrape(
    max_pages: int | None = 1,
    fetch_details: bool = False,
    delay: float = 0.0,
    browser_name: str = DEFAULT_BROWSER_NAME,
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
                )
            finally:
                context.close()
        finally:
            browser.close()


def _scrape_with_context(
    context,
    *,
    max_pages: int | None,
    fetch_details: bool,
    delay: float,
) -> list[Job]:
    listing_page = context.new_page()
    detail_page = None
    scraped_at = utc_now_iso()
    scraped_at_dt = _parse_iso_datetime(scraped_at)
    if scraped_at_dt is None:
        raise ValueError("scraped_at must be a valid ISO 8601 timestamp")

    jobs: list[Job] = []
    seen_job_ids: set[str] = set()
    page_number = 1
    last_page: int | None = None

    try:
        while True:
            if page_number > 1 and delay:
                time.sleep(delay)

            listing_page.goto(_build_listing_url(page_number), wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
            html = listing_page.content()
            page_jobs, page_meta = _parse_listing_html(html, scraped_at=scraped_at, scraped_at_dt=scraped_at_dt)

            if last_page is None:
                last_page = _extract_last_page(page_meta)

            for job in page_jobs:
                if not _is_recent_job_post(job.posted_at, scraped_at_dt):
                    continue

                if fetch_details or _job_needs_detail_enrichment(job):
                    if detail_page is None:
                        detail_page = context.new_page()
                    try:
                        _enrich_job_from_detail(detail_page, job, include_description=fetch_details)
                    except Exception as exc:
                        warnings.warn(
                            f"Failed to enrich Loker.id job {job.job_id} ({job.title}): {exc}",
                            RuntimeWarning,
                        )

                if job.job_id in seen_job_ids:
                    continue
                seen_job_ids.add(job.job_id)
                jobs.append(job)

            if not page_jobs:
                break
            if max_pages is not None and page_number >= max_pages:
                break
            if last_page is not None and page_number >= last_page:
                break
            if page_meta and not _has_next_page(page_meta):
                break

            page_number += 1
    finally:
        listing_page.close()
        if detail_page is not None:
            detail_page.close()

    return jobs


def _launch_browser(playwright: Playwright, browser_name: str):
    if browser_name not in {"chromium", "firefox", "webkit"}:
        raise ValueError("browser_name must be chromium, firefox, or webkit")
    return getattr(playwright, browser_name).launch(headless=True)


def _parse_listing_html(
    html: str,
    *,
    scraped_at: str,
    scraped_at_dt,
) -> tuple[list[Job], dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    context = _extract_remix_context(soup)
    context_meta = _extract_pagination_meta(context) if context is not None else {}
    rendered_meta = _extract_pagination_meta_from_dom(soup)

    url_mappings = _extract_url_mappings(soup)
    loader_records = _extract_job_records(context) if context is not None else []
    rendered_records = _extract_rendered_job_records(soup)

    if not loader_records and not rendered_records:
        raise ValueError("Loker.id listing HTML did not expose any jobs")

    jobs = _build_jobs_from_records(loader_records, scraped_at=scraped_at, scraped_at_dt=scraped_at_dt, url_mappings=url_mappings)
    rendered_jobs = _build_jobs_from_records(rendered_records, scraped_at=scraped_at, scraped_at_dt=scraped_at_dt, url_mappings=url_mappings)
    jobs = _merge_job_lists(jobs, rendered_jobs)
    jobs = [job for job in jobs if _is_recent_job_post(job.posted_at, scraped_at_dt)]

    meta = context_meta or rendered_meta
    return jobs, meta


def _build_jobs_from_records(records: list[dict[str, Any]], *, scraped_at: str, scraped_at_dt: datetime | None = None, url_mappings: dict[str, str] | None = None) -> list[Job]:
    jobs: list[Job] = []
    url_map = url_mappings or {}
    for record in records:
        job = _parse_job_record(record, scraped_at=scraped_at, reference_dt=scraped_at_dt, url_mappings=url_map)
        if job is None:
            nested_record = _find_first_job_like_record(record)
            if nested_record is not None and nested_record is not record:
                job = _parse_job_record(nested_record, scraped_at=scraped_at, reference_dt=scraped_at_dt, url_mappings=url_map)
        if job is not None:
            jobs.append(job)
    return jobs


def _merge_job_lists(primary: list[Job], fallback: list[Job]) -> list[Job]:
    if not fallback:
        return primary

    merged: list[Job] = []
    index_by_job_id: dict[str, Job] = {}
    for job in primary:
        merged.append(job)
        index_by_job_id[job.job_id] = job

    for job in fallback:
        existing = index_by_job_id.get(job.job_id)
        if existing is None:
            merged.append(job)
            index_by_job_id[job.job_id] = job
            continue
        _merge_job(existing, job)

    return merged


def _merge_job(current: Job, candidate: Job) -> None:
    if current.location is None:
        current.location = candidate.location
    if current.job_type is None:
        current.job_type = candidate.job_type
    if current.salary_range is None:
        current.salary_range = candidate.salary_range
    if current.url == LOKERID_LISTING_URL and candidate.url != LOKERID_LISTING_URL:
        current.url = candidate.url
    if not current.tags and candidate.tags:
        current.tags = list(candidate.tags)
    if current.description is None and candidate.description is not None:
        current.description = candidate.description
    if current.posted_at is None:
        current.posted_at = candidate.posted_at


def _extract_job_records(payload: Any) -> list[dict[str, Any]]:
    records = _find_job_records(payload)
    if records is None:
        raise ValueError("Could not find job records in the Loker.id payload")
    return records


def _find_job_records(node: Any) -> list[dict[str, Any]] | None:
    if isinstance(node, list):
        if node and all(isinstance(item, dict) for item in node):
            return [item for item in node if isinstance(item, dict)]
        for item in node:
            found = _find_job_records(item)
            if found is not None:
                return found
        return None

    if not isinstance(node, dict):
        return None

    if "state" in node and isinstance(node["state"], dict):
        state = node["state"]
        loader_data = state.get("loaderData", {})
        if isinstance(loader_data, dict):
            for route_value in loader_data.values():
                if isinstance(route_value, dict) and "jobs" in route_value:
                    jobs = route_value["jobs"]
                    if isinstance(jobs, list) and jobs:
                        return jobs

    for key in ("jobs", "data", "items", "results", "list"):
        value = node.get(key)
        if isinstance(value, list) and value and any(isinstance(item, dict) for item in value):
            return [item for item in value if isinstance(item, dict)]

    for value in node.values():
        found = _find_job_records(value)
        if found is not None:
            return found
    return None


def _extract_pagination_meta(payload: Any) -> dict[str, Any]:
    meta = _find_pagination_meta(payload)
    return meta or {}


def _find_pagination_meta(node: Any) -> dict[str, Any] | None:
    if isinstance(node, dict):
        if any(key in node for key in ("current_page", "last_page", "total", "links", "next_page_url", "prev_page_url")):
            meta: dict[str, Any] = {}
            for key in ("current_page", "last_page", "total", "links", "next_page_url", "prev_page_url"):
                if key in node:
                    meta[key] = node[key]
            return meta
        if isinstance(node.get("meta"), dict):
            return _find_pagination_meta(node["meta"])
        for value in node.values():
            found = _find_pagination_meta(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_pagination_meta(item)
            if found is not None:
                return found
    return None


def _extract_pagination_meta_from_dom(soup: BeautifulSoup) -> dict[str, Any]:
    page_numbers: list[int] = []
    for anchor in soup.select('a[href*="page="]'):
        href = _clean_string(anchor.get("href"))
        if not href:
            continue
        query = parse_qs(urlparse(href).query)
        for value in query.get("page", []):
            try:
                page_numbers.append(int(value))
            except ValueError:
                continue
    if page_numbers:
        return {"last_page": max(page_numbers)}
    return {}


def _extract_last_page(meta: dict[str, Any]) -> int | None:
    last_page = meta.get("last_page") or meta.get("lastPage") or meta.get("total_pages") or meta.get("totalPages")
    if isinstance(last_page, int) and last_page > 0:
        return last_page
    if isinstance(last_page, str):
        try:
            parsed = int(last_page)
        except ValueError:
            parsed = None
        if parsed is not None and parsed > 0:
            return parsed

    links = meta.get("links")
    if isinstance(links, list):
        page_numbers: list[int] = []
        for link in links:
            if not isinstance(link, dict):
                continue
            url = _clean_string(link.get("url"))
            if not url:
                continue
            query = parse_qs(urlparse(url).query)
            for value in query.get("page", []):
                try:
                    page_numbers.append(int(value))
                except ValueError:
                    continue
        if page_numbers:
            return max(page_numbers)
    return None


def _has_next_page(meta: dict[str, Any]) -> bool:
    current_page = meta.get("current_page") or meta.get("currentPage") or meta.get("page")
    last_page = _extract_last_page(meta)
    if isinstance(current_page, int) and last_page is not None:
        return current_page < last_page
    if isinstance(current_page, str):
        try:
            current_page_num = int(current_page)
        except ValueError:
            current_page_num = None
        if current_page_num is not None and last_page is not None:
            return current_page_num < last_page
    next_page_url = meta.get("next_page_url") or meta.get("nextPageUrl")
    if isinstance(next_page_url, str) and next_page_url:
        return True
    links = meta.get("links")
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, dict):
                continue
            label = _clean_string(link.get("label"))
            if label and label.lower() in {"next", "selanjutnya", "berikutnya"}:
                return True
            rel = _clean_string(link.get("rel"))
            if rel and rel.lower() == "next":
                return True
    return False


def _job_needs_detail_enrichment(job: Job) -> bool:
    return job.location is None or job.job_type is None or job.salary_range is None or not job.tags


def _parse_job_record(
    record: dict[str, Any],
    *,
    scraped_at: str,
    reference_dt: datetime | None = None,
    include_description: bool = False,
    url_mappings: dict[str, str] | None = None,
) -> Job | None:
    job_id = _stringify_text(_first_present(record, "id", "jobid", "job_id", "jobId", "uuid", "lowongan_id", "lowonganId", "id_lowongan", "idLowongan"))
    title = _clean_string(_first_present(record, "title", "job_title", "jobTitle", "name", "position", "judul", "nama_lowongan", "namaPekerjaan", "posisi"))
    company = _extract_company_name(record)
    posted_at_value = _first_present(
        record,
        "posted_at",
        "postedAt",
        "published_at",
        "publishedAt",
        "publish_at",
        "publishAt",
        "created_at",
        "createdAt",
        "updated_at",
        "updatedAt",
        "date_posted",
        "datePosted",
        "job_date",
        "jobDate",
        "tanggal_posting",
        "tanggal_publikasi",
        "diposting_pada",
        "dipublikasikan_pada",
    )
    posted_at = _normalize_posted_at(posted_at_value, reference_dt=reference_dt)
    url = _build_job_url(record, url_mappings=url_mappings or {})

    if not job_id or not title or not company or not url or not posted_at:
        return None

    job = Job(
        job_id=job_id,
        title=title,
        company=company,
        location=_format_location(record),
        job_type=_format_job_type(record),
        salary_range=_format_salary_range(record),
        url=url,
        tags=_collect_tags(record),
        posted_at=posted_at,
        scraped_at=scraped_at,
    )
    if include_description:
        job.description = _extract_description(record)
    return job


def _extract_company_name(record: dict[str, Any]) -> str | None:
    company = _first_present(
        record,
        "company",
        "company_name",
        "companyName",
        "nama_perusahaan",
        "namaPerusahaan",
        "perusahaan",
        "employer_name",
        "employerName",
        "employer",
        "organization",
        "organization_name",
        "organizationName",
        "organisasi",
    )
    if isinstance(company, dict):
        name = _clean_string(company.get("name") or company.get("company_name") or company.get("title") or company.get("label"))
        if name:
            return name
        nested_name = _clean_string(_first_present(company, "name", "company_name", "title", "label"))
        if nested_name:
            return nested_name
    return _clean_string(company)


def _build_job_url(record: dict[str, Any], url_mappings: dict[str, str] | None = None) -> str:
    url_map = url_mappings or {}

    slug = _clean_string(_first_present(record, "slug", "path", "permalink"))
    if slug and slug in url_map:
        return url_map[slug]

    direct_url = _clean_string(_first_present(record, "url", "job_url", "detail_url", "link", "tautan", "tautan_lowongan", "url_lowongan"))
    if direct_url:
        return urljoin(LOKERID_LISTING_URL, direct_url)

    if slug:
        normalized = slug.lstrip("/")
        if not normalized.endswith(".html"):
            normalized = f"{normalized}.html"
        return urljoin("https://www.loker.id/", normalized)

    job_id = _stringify_text(_first_present(record, "id", "jobid", "job_id", "uuid"))
    if job_id:
        return f"{LOKERID_LISTING_URL}?jobid={job_id}"

    return LOKERID_LISTING_URL


def _format_location(record: dict[str, Any]) -> str | None:
    location = _clean_string(_first_present(record, "location", "job_location", "location_name", "lokasi", "lokasi_kerja", "kota", "provinsi"))
    if location:
        return location

    if _is_remote(record):
        return "Remote"

    parts: list[str] = []
    for key in ("city", "district", "province", "country"):
        value = record.get(key)
        if isinstance(value, dict):
            name = _clean_string(value.get("name") or value.get("label"))
        else:
            name = _clean_string(value)
        if name:
            parts.append(name)

    if not parts:
        return None
    return ", ".join(_dedupe_list(parts))


def _is_remote(record: dict[str, Any]) -> bool:
    for key in ("work_arrangement", "workArrangement", "work_location", "is_remote", "remote", "kerja_remote", "wfh", "work_from_home"):
        value = _first_present(record, key)
        if isinstance(value, bool) and value:
            return True
        if isinstance(value, str) and value.lower() == "remote":
            return True
    return False


def _format_job_type(record: dict[str, Any]) -> str | None:
    value = _clean_string(_first_present(record, "job_type", "employment_type", "employmentType", "type", "jobType", "jenis_pekerjaan", "tipe_pekerjaan"))
    if value:
        return _humanize_label(value)

    label = _clean_string(_first_present(record, "job_type_label", "employment_type_label"))
    if label:
        return label

    return None


def _format_salary_range(record: dict[str, Any]) -> str | None:
    display = _clean_string(_first_present(record, "salary", "salary_text", "salary_display", "salary_range", "gaji", "gaji_text", "salaryInfo"))
    if display:
        return display

    minimum = _first_present(record, "salary_min", "salary_from", "min_salary", "minSalary")
    maximum = _first_present(record, "salary_max", "salary_to", "max_salary", "maxSalary")
    minimum_int = _as_int(minimum)
    maximum_int = _as_int(maximum)
    if minimum_int is None and maximum_int is None:
        return None

    currency = _format_currency(_first_present(record, "salary_currency", "currency", "salaryCurrency"))
    if minimum_int is not None and maximum_int is not None:
        return f"{currency} {minimum_int:,} - {currency} {maximum_int:,}"
    if minimum_int is not None:
        return f"From {currency} {minimum_int:,}"
    return f"Up to {currency} {maximum_int:,}"


def _format_currency(value: Any) -> str:
    currency = _clean_string(value)
    if currency is None:
        return "Rp"
    if currency.upper() == "IDR":
        return "Rp"
    if currency.lower() == "rp":
        return "Rp"
    return currency.upper()


def _collect_tags(record: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for key in ("job_skills", "skills", "tags", "keywords", "keahlian", "skill", "kata_kunci"):
        value = _first_present(record, key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    name = _clean_string(item.get("name") or item.get("label") or item.get("title"))
                else:
                    name = _clean_string(item)
                if name:
                    tags.append(name)
    if _is_remote(record):
        tags.append("Remote")
    return _dedupe_list(tags)


def _extract_description(record: dict[str, Any]) -> str | None:
    for key in ("description", "job_description", "description_text", "full_description", "deskripsi", "deskripsi_pekerjaan", "keterangan"):
        value = _clean_string(_first_present(record, key))
        if value:
            return _normalize_description_text(value)
    return None


def _enrich_job_from_detail(page, job: Job, *, include_description: bool = False) -> None:
    page.goto(job.url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
    detail_html = page.content()
    detail_job = _extract_detail_job(detail_html, scraped_at=job.scraped_at)
    if detail_job is None:
        return
    _merge_job(job, detail_job)
    if not include_description:
        job.description = None


def _extract_detail_job(html: str, *, scraped_at: str) -> Job | None:
    soup = BeautifulSoup(html, "html.parser")
    reference_dt = _parse_iso_datetime(scraped_at)

    context = _extract_remix_context(soup)
    if context is not None:
        record = _find_first_job_like_record(context)
        if record is not None:
            return _parse_job_record(record, scraped_at=scraped_at, reference_dt=reference_dt, include_description=True)

    description = _extract_description_from_rendered_html(soup)
    if description:
        return Job(
            job_id="placeholder",
            title="placeholder",
            company="placeholder",
            location=None,
            job_type=None,
            salary_range=None,
            url="placeholder",
            posted_at=scraped_at,
            scraped_at=scraped_at,
            description=description,
        )

    records = _extract_rendered_job_records(soup)
    if not records:
        return None
    for record in records:
        job = _parse_job_record(record, scraped_at=scraped_at, reference_dt=reference_dt, include_description=True)
        if job is not None:
            return job
    return None


def _extract_description_from_rendered_html(soup: BeautifulSoup) -> str | None:
    main = soup.select_one("main") or soup.select_one("article")
    if not main:
        return None

    for div in main.find_all(["div", "section"], recursive=True):
        text = div.get_text(separator="\n", strip=True)
        if any(keyword in text for keyword in ["Kami membuka lowongan", "Tanggung Jawab", "Kualifikasi", "Persyaratan"]):
            if len(text) > 100:
                return _normalize_description_text(text)

    return None


def _extract_remix_context(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.find_all("script"):
        text = script.string or script.get_text(strip=True)
        if not text or "window.__remixContext" not in text:
            continue
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            continue
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _extract_url_mappings(soup: BeautifulSoup) -> dict[str, str]:
    url_mappings: dict[str, str] = {}

    for link in soup.select('a[href*=".html"]'):
        href = _clean_string(link.get("href"))
        if not href or "cari-lowongan-kerja" in href:
            continue

        slug = href.split("/")[-1].replace(".html", "")
        if slug:
            url_mappings[slug] = urljoin(LOKERID_LISTING_URL, href)

    return url_mappings


def _find_first_job_like_record(node: Any) -> dict[str, Any] | None:
    if isinstance(node, dict):
        if _looks_like_job_record(node):
            return node
        for key in ("jobs", "data", "items", "results", "list"):
            value = node.get(key)
            if isinstance(value, list):
                for item in value:
                    record = _find_first_job_like_record(item)
                    if record is not None:
                        return record
        for value in node.values():
            record = _find_first_job_like_record(value)
            if record is not None:
                return record
    elif isinstance(node, list):
        for item in node:
            record = _find_first_job_like_record(item)
            if record is not None:
                return record
    return None


def _looks_like_job_record(record: dict[str, Any]) -> bool:
    job_id = _first_present(record, "id", "jobid", "job_id", "uuid")
    title = _first_present(record, "title", "job_title", "name", "position")
    company = _extract_company_name(record)
    return job_id is not None and title is not None and company is not None


def _extract_rendered_job_records(soup: BeautifulSoup) -> list[dict[str, Any]]:
    cards: list[Any] = []
    seen: set[int] = set()
    for selector in CARD_SELECTORS:
        for card in soup.select(selector):
            marker = id(card)
            if marker in seen:
                continue
            seen.add(marker)
            cards.append(card)

    if not cards:
        for anchor in soup.select('a[href$=".html"], a[href*="?jobid="]'):
            card = anchor.find_parent(["article", "li", "div"])
            if card is None:
                continue
            marker = id(card)
            if marker in seen:
                continue
            seen.add(marker)
            cards.append(card)

    records: list[dict[str, Any]] = []
    for card in cards:
        record = _parse_rendered_card(card)
        if record is not None:
            records.append(record)
    return records


def _parse_rendered_card(card: Any) -> dict[str, Any] | None:
    title_link = _first_node(card, ("h3 a", ".title a", "a[href$='.html']", "a[href*='?jobid=']"))
    company_node = _first_node(card, (".company-name", ".company a", ".company", "[class*='company']"))
    location_node = _first_node(card, (".location", ".job-location", "[class*='location']"))
    salary_node = _first_node(card, (".salary", ".job-salary", "[class*='salary']"))
    job_type_node = _first_node(card, (".job-type", ".employment-type", "[class*='type']"))
    posted_node = _first_node(card, ("time[datetime]", "[data-posted-at]"))

    job_id = _stringify_text(
        _clean_string(card.get("data-job-id"))
        or _job_id_from_anchor(title_link)
        or _job_id_from_anchor(_first_node(card, ("a[href*='?jobid=']",)))
    )
    title = _extract_node_text(title_link)
    company = _extract_node_text(company_node)
    posted_at = _clean_string(posted_node.get("datetime")) if posted_node is not None else None
    if posted_at is None and posted_node is not None:
        posted_at = _clean_string(posted_node.get("data-posted-at"))
    if posted_at is None:
        posted_at = _clean_string(card.get("data-posted-at"))

    if not job_id or not title or not company or not posted_at:
        return None

    record: dict[str, Any] = {
        "id": job_id,
        "title": title,
        "company_name": company,
        "posted_at": posted_at,
        "url": _extract_node_href(title_link) or _extract_node_href(_first_node(card, ("a[href*='?jobid=']",))),
        "location": _extract_node_text(location_node),
        "salary": _extract_node_text(salary_node),
        "job_type": _extract_node_text(job_type_node),
        "tags": _extract_rendered_tags(card),
    }
    return record


def _extract_rendered_tags(card: Any) -> list[str]:
    tags: list[str] = []
    for selector in (".tag", ".tags span", ".skills span", "[data-tag]", "[class*='skill']"):
        for node in card.select(selector):
            text = _extract_node_text(node)
            if text:
                tags.append(text)
    return _dedupe_list(tags)


def _extract_node_text(node: Any) -> str | None:
    if node is None:
        return None
    if hasattr(node, "get_text"):
        return _clean_string(node.get_text(" ", strip=True))
    if isinstance(node, str):
        return _clean_string(node)
    return None


def _extract_node_href(node: Any) -> str | None:
    if node is None:
        return None
    if hasattr(node, "get"):
        href = _clean_string(node.get("href"))
        if href:
            return urljoin(LOKERID_LISTING_URL, href)
    return None


def _first_node(card: Any, selectors: tuple[str, ...]) -> Any | None:
    for selector in selectors:
        if hasattr(card, "select_one"):
            node = card.select_one(selector)
            if node is not None:
                return node
    return None


def _job_id_from_anchor(node: Any) -> str | None:
    href = _extract_node_href(node)
    if href is None:
        return None
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    for key in ("jobid", "job_id", "id"):
        values = query.get(key)
        if values:
            return _stringify_text(values[0])
    match = re.search(r"/([0-9]+)\.html$", parsed.path)
    if match is not None:
        return match.group(1)
    return None


def _build_listing_url(page_number: int) -> str:
    if page_number <= 1:
        return LOKERID_LISTING_URL
    return f"{LOKERID_LISTING_URL}/page/{page_number}"


def _first_present(record: Any, *keys: str) -> Any:
    if isinstance(record, dict):
        for key in keys:
            value = record.get(key)
            if value is not None and value != "":
                return value
        for value in record.values():
            found = _first_present(value, *keys)
            if found is not None and found != "":
                return found
    elif isinstance(record, list):
        for item in record:
            found = _first_present(item, *keys)
            if found is not None and found != "":
                return found
    return None


def _stringify_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value)
    return _clean_string(str(value))


def _normalize_posted_at(value: Any, reference_dt: datetime | None = None) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        posted_at_dt = _datetime_from_epoch(value)
    elif isinstance(value, str):
        cleaned = _clean_string(value)
        if cleaned is None:
            return None
        epoch_value = _as_int(cleaned)
        if epoch_value is not None:
            posted_at_dt = _datetime_from_epoch(epoch_value)
        else:
            posted_at_dt = (
                _parse_iso_datetime(cleaned)
                or _parse_common_datetime(cleaned)
                or _parse_relative_datetime(cleaned, reference_dt=reference_dt)
            )
    else:
        cleaned = _clean_string(str(value))
        if cleaned is None:
            return None
        posted_at_dt = _parse_iso_datetime(cleaned) or _parse_common_datetime(cleaned) or _parse_relative_datetime(cleaned, reference_dt=reference_dt)

    if posted_at_dt is None:
        return None
    return posted_at_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_relative_datetime(value: str, reference_dt: datetime | None = None) -> datetime | None:
    text = value.strip().lower()
    if not text:
        return None

    base_dt = reference_dt or datetime.now(timezone.utc)
    if text in {"today", "hari ini", "just now", "baru saja", "sekarang"}:
        return base_dt
    if text in {"yesterday", "kemarin"}:
        return base_dt - timedelta(days=1)

    patterns = (
        (r"(?P<count>\d+)\s*(?:minute|minutes|min|mins|menit)\s*(?:ago|lalu)", "minutes"),
        (r"(?P<count>\d+)\s*(?:hour|hours|hr|hrs|jam)\s*(?:ago|lalu)", "hours"),
        (r"(?P<count>\d+)\s*(?:day|days|hari)\s*(?:ago|lalu)", "days"),
        (r"(?P<count>\d+)\s*(?:week|weeks|minggu)\s*(?:ago|lalu)", "weeks"),
        (r"(?P<count>\d+)\s*(?:month|months|bulan)\s*(?:ago|lalu)", "months"),
        (r"(?P<count>\d+)\s*(?:year|years|tahun)\s*(?:ago|lalu)", "years"),
    )
    for pattern, unit in patterns:
        match = re.search(pattern, text)
        if match is None:
            continue
        count = int(match.group("count"))
        if unit == "minutes":
            return base_dt - timedelta(minutes=count)
        if unit == "hours":
            return base_dt - timedelta(hours=count)
        if unit == "days":
            return base_dt - timedelta(days=count)
        if unit == "weeks":
            return base_dt - timedelta(weeks=count)
        if unit == "months":
            return base_dt - timedelta(days=30 * count)
        if unit == "years":
            return base_dt - timedelta(days=365 * count)
    return None


def _parse_common_datetime(value: str) -> datetime | None:
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%d %b %Y %H:%M:%S",
        "%d %b %Y %H:%M",
        "%d %b %Y",
        "%d %B %Y %H:%M:%S",
        "%d %B %Y %H:%M",
        "%d %B %Y",
    ):
        try:
            parsed = datetime.strptime(value, pattern)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc)
    return None


def _datetime_from_epoch(value: int | float) -> datetime | None:
    raw = float(value)
    if abs(raw) >= 1_000_000_000_000_000:
        raw /= 1_000_000
    elif abs(raw) >= 1_000_000_000_000:
        raw /= 1_000
    try:
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


__all__ = ["parse_jobs", "parse_listing_html", "scrape"]
