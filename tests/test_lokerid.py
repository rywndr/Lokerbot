from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

from lokerbot.models import Job
from lokerbot.scrapers.lokerid import parse_jobs, parse_listing_html, scrape

FIXTURE_DIR = Path(__file__).parent / "fixtures"
API_FIXTURE_PATH = FIXTURE_DIR / "lokerid_api_response.json"
LISTING_FIXTURE_PATH = FIXTURE_DIR / "lokerid_listing.html"
DETAIL_FIXTURE_PATH = FIXTURE_DIR / "lokerid_detail.html"
FIXTURE_SCRAPED_AT = "2026-03-18T12:00:00Z"
RECENCY_BOUNDARY = "2026-02-16T12:00:00Z"


def load_api_fixture() -> dict[str, object]:
    with open(API_FIXTURE_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def build_api_job(
    job_id: str,
    posted_at: str,
    *,
    title: str = "Example Role",
    company_name: str = "Example Co",
    slug: str | None = None,
    location: str | None = "Jakarta",
    job_type: str | None = "full_time",
    salary_min: int | None = 5_000_000,
    salary_max: int | None = 7_000_000,
    salary_currency: str | None = "IDR",
    skills: list[object] | None = None,
    company: dict[str, object] | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "id": job_id,
        "title": title,
        "posted_at": posted_at,
        "slug": slug or f"/roles/{job_id}",
    }
    if company is not None:
        record["company"] = company
    else:
        record["company_name"] = company_name
    if location is not None:
        record["location"] = location
    if job_type is not None:
        record["job_type"] = job_type
    if salary_min is not None:
        record["salary_min"] = salary_min
    if salary_max is not None:
        record["salary_max"] = salary_max
    if salary_currency is not None:
        record["salary_currency"] = salary_currency
    if skills is not None:
        record["job_skills"] = skills
    return record


def build_api_response(jobs: list[dict[str, object]], *, current_page: int, last_page: int | None) -> dict[str, object]:
    meta: dict[str, object] = {
        "current_page": current_page,
        "total": len(jobs),
        "links": [],
    }
    if last_page is not None:
        meta["last_page"] = last_page
        if last_page > current_page:
            meta["links"] = [
                {"url": f"https://www.loker.id/cari-lowongan-kerja?page={current_page}", "label": str(current_page), "rel": "prev"},
                {"url": f"https://www.loker.id/cari-lowongan-kerja?page={last_page}", "label": str(last_page), "rel": "next"},
            ]
    return {"data": {"jobs": jobs, "meta": meta}}


def build_rendered_card(
    job_id: str,
    title: str,
    company: str,
    posted_at: str,
    *,
    slug: str | None = None,
    location: str | None = "Jakarta",
    job_type: str | None = "Full Time",
    salary: str | None = "Rp 5,000,000 - Rp 7,000,000",
    tags: list[str] | None = None,
) -> str:
    tags = tags or []
    tag_html = "".join(f"<span>{tag}</span>" for tag in tags)
    location_html = f"<span class=\"location\">{location}</span>" if location is not None else ""
    job_type_html = f"<span class=\"job-type\">{job_type}</span>" if job_type is not None else ""
    salary_html = f"<span class=\"salary\">{salary}</span>" if salary is not None else ""
    detail_href = slug or f"/roles/{job_id}.html"
    return (
        f'<article class="job-card" data-job-id="{job_id}">'
        f'<div class="company-name">{company}</div>'
        f'<h3 class="title"><a href="{detail_href}">{title}</a></h3>'
        f'<div class="meta">{location_html}{job_type_html}{salary_html}</div>'
        f'<a class="rincian" href="/cari-lowongan-kerja?jobid={job_id}">Rincian</a>'
        f'<div class="tags">{tag_html}</div>'
        f'<time datetime="{posted_at}"></time>'
        "</article>"
    )


def build_rendered_listing_html(cards: list[str], *, include_remix_context: bool = False, meta: dict[str, object] | None = None) -> str:
    if include_remix_context:
        meta = meta or {"current_page": 1, "last_page": 1, "total": len(cards), "links": []}
        jobs_json = []
        for index, _card in enumerate(cards, start=1):
            jobs_json.append(
                {
                    "id": f"card-{index}",
                    "title": f"Card {index}",
                    "company_name": f"Company {index}",
                    "posted_at": FIXTURE_SCRAPED_AT,
                }
            )
        script = json.dumps(
            {
                "state": {
                    "loaderData": {
                        "routes/_lowongan.cari-lowongan-kerja.(page).($number_page)": {
                            "jobs": jobs_json,
                            "meta": meta,
                        }
                    }
                }
            }
        )
        remix = f"<script>window.__remixContext = {script};</script>"
    else:
        remix = ""
    return f"<!DOCTYPE html><html><body>{remix}{''.join(cards)}</body></html>"


def build_scrape_job(
    job_id: str,
    posted_at: str,
    *,
    title: str = "Example Role",
    company: str = "Example Co",
    url: str = "https://www.loker.id/roles/example-role.html",
    location: str | None = "Jakarta",
    job_type: str | None = "Full Time",
    salary_range: str | None = "Rp 5,000,000 - Rp 7,000,000",
    tags: list[str] | None = None,
    description: str | None = None,
) -> Job:
    return Job(
        job_id=job_id,
        title=title,
        company=company,
        location=location,
        job_type=job_type,
        salary_range=salary_range,
        url=url,
        description=description,
        tags=tags if tags is not None else ["General"],
        posted_at=posted_at,
        scraped_at=FIXTURE_SCRAPED_AT,
    )


def build_playwright_stack(detail_page: Mock | None = None) -> tuple[Mock, Mock, Mock, Mock, Mock]:
    playwright = Mock()
    browser = Mock()
    context = Mock()
    listing_page = Mock()
    cm = MagicMock()
    cm.__enter__.return_value = playwright
    cm.__exit__.return_value = None
    playwright.firefox.launch.return_value = browser
    browser.new_context.return_value = context
    context.new_page.side_effect = [listing_page] + ([detail_page] if detail_page is not None else [])
    return cm, browser, context, listing_page, detail_page


class LokeridParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api_fixture = load_api_fixture()
        self.listing_html = LISTING_FIXTURE_PATH.read_text(encoding="utf-8")
        self.detail_html = DETAIL_FIXTURE_PATH.read_text(encoding="utf-8")

    def test_parse_jobs_from_api_fixture_normalizes_fields(self) -> None:
        jobs = parse_jobs(self.api_fixture, scraped_at=FIXTURE_SCRAPED_AT)

        self.assertEqual(len(jobs), 2)
        first_job = jobs[0]
        self.assertEqual(first_job.job_id, "1001")
        self.assertEqual(first_job.title, "Administrasi Staff")
        self.assertEqual(first_job.company, "PT Contoh Maju")
        self.assertEqual(first_job.location, "Jakarta Selatan")
        self.assertEqual(first_job.job_type, "Full Time")
        self.assertEqual(first_job.salary_range, "Rp 5,000,000 - Rp 7,000,000")
        self.assertEqual(first_job.url, "https://www.loker.id/administrasi/administrasi-staff-1001.html")
        self.assertEqual(first_job.tags, ["Excel", "Administrasi"])
        self.assertEqual(first_job.posted_at, "2026-03-16T08:00:00Z")
        self.assertEqual(first_job.scraped_at, FIXTURE_SCRAPED_AT)
        self.assertIsNone(first_job.description)

        second_job = jobs[1]
        self.assertEqual(second_job.job_id, "1002")
        self.assertEqual(second_job.company, "PT Pelayanan Prima")
        self.assertEqual(second_job.location, "Bandung, Jawa Barat")
        self.assertEqual(second_job.job_type, "Contract")
        self.assertEqual(second_job.salary_range, "Negotiable")
        self.assertEqual(
            second_job.url,
            "https://www.loker.id/customer-care/customer-care-officer-1002.html",
        )
        self.assertEqual(second_job.tags, ["Communication", "Empathy"])
        self.assertEqual(second_job.scraped_at, FIXTURE_SCRAPED_AT)
        self.assertIsNone(second_job.description)

    def test_parse_jobs_accepts_epoch_posted_at(self) -> None:
        payload = build_api_response(
            [
                build_api_job(
                    "1003",
                    1773648000,
                    title="Epoch Job",
                    company_name="PT Epoch Sukses",
                    location="Surabaya",
                    job_type="part_time",
                    salary_min=4_000_000,
                    salary_max=6_000_000,
                    skills=["Python"],
                )
            ],
            current_page=1,
            last_page=1,
        )

        jobs = parse_jobs(payload, scraped_at=FIXTURE_SCRAPED_AT)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].posted_at, "2026-03-16T08:00:00Z")
        self.assertEqual(jobs[0].job_type, "Part Time")

    def test_parse_jobs_accepts_relative_posted_at(self) -> None:
        payload = build_api_response(
            [
                build_api_job(
                    "1004",
                    "2 days ago",
                    title="Relative Job",
                    company_name="PT Relative Sukses",
                    location="Bandung",
                    job_type="full_time",
                    salary_min=5_000_000,
                    salary_max=8_000_000,
                    skills=["Python"],
                )
            ],
            current_page=1,
            last_page=1,
        )

        jobs = parse_jobs(payload, scraped_at=FIXTURE_SCRAPED_AT)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].posted_at, "2026-03-16T12:00:00Z")
        self.assertEqual(jobs[0].job_id, "1004")

    def test_parse_jobs_extracts_nested_attribute_records(self) -> None:
        payload = {
            "data": {
                "jobs": [
                    {
                        "attributes": {
                            "id": "nested-job",
                            "title": "Nested Job",
                            "company": {"name": "PT Nested Sukses"},
                            "location": "Jakarta",
                            "employment_type": "full_time",
                            "salary_min": 5_000_000,
                            "salary_max": 7_000_000,
                            "job_skills": [{"name": "Python"}],
                            "posted_at": "2026-03-16T08:00:00Z",
                            "slug": "/nested/nested-job",
                        }
                    }
                ],
                "meta": {"current_page": 1, "last_page": 1, "total": 1, "links": []},
            }
        }

        jobs = parse_jobs(payload, scraped_at=FIXTURE_SCRAPED_AT)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_id, "nested-job")
        self.assertEqual(jobs[0].company, "PT Nested Sukses")
        self.assertEqual(jobs[0].url, "https://www.loker.id/nested/nested-job.html")
        self.assertEqual(jobs[0].tags, ["Python"])

    def test_parse_listing_html_uses_remix_loader_payload(self) -> None:
        jobs = parse_listing_html(self.listing_html, scraped_at=FIXTURE_SCRAPED_AT)

        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].job_id, "1001")
        self.assertEqual(jobs[0].title, "Administrasi Staff")
        self.assertEqual(jobs[0].company, "PT Contoh Maju")
        self.assertEqual(jobs[0].location, "Jakarta Selatan")
        self.assertEqual(jobs[0].job_type, "Full Time")
        self.assertEqual(jobs[0].salary_range, "Rp 5,000,000 - Rp 7,000,000")
        self.assertEqual(jobs[0].tags, ["Excel", "Administrasi"])
        self.assertEqual(jobs[0].posted_at, "2026-03-16T08:00:00Z")
        self.assertIsNone(jobs[0].description)

    def test_parse_listing_html_falls_back_to_rendered_cards(self) -> None:
        html = build_rendered_listing_html(
            [
                build_rendered_card(
                    "2001",
                    "Receptionist",
                    "PT Front Office",
                    "2026-03-16T07:00:00Z",
                    slug="/front-office/receptionist-2001.html",
                    location="Jakarta Pusat",
                    job_type="Part Time",
                    salary="Rp 4,000,000 - Rp 5,000,000",
                    tags=["Customer Service", "Administrasi"],
                )
            ]
        )

        jobs = parse_listing_html(html, scraped_at=FIXTURE_SCRAPED_AT)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_id, "2001")
        self.assertEqual(jobs[0].title, "Receptionist")
        self.assertEqual(jobs[0].company, "PT Front Office")
        self.assertEqual(jobs[0].location, "Jakarta Pusat")
        self.assertEqual(jobs[0].job_type, "Part Time")
        self.assertEqual(jobs[0].salary_range, "Rp 4,000,000 - Rp 5,000,000")
        self.assertEqual(jobs[0].url, "https://www.loker.id/front-office/receptionist-2001.html")
        self.assertEqual(jobs[0].tags, ["Customer Service", "Administrasi"])
        self.assertEqual(jobs[0].posted_at, "2026-03-16T07:00:00Z")
        self.assertIsNone(jobs[0].description)


class LokeridScrapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.listing_html = LISTING_FIXTURE_PATH.read_text(encoding="utf-8")
        self.detail_html = DETAIL_FIXTURE_PATH.read_text(encoding="utf-8")

    def test_scrape_keeps_recent_jobs_deduplicated_across_pages(self) -> None:
        page1_jobs = [
            build_scrape_job("today-job", "2026-03-18T12:00:00Z", title="Today Job"),
            build_scrape_job("boundary-job", RECENCY_BOUNDARY, title="Boundary Job"),
            build_scrape_job("old-job", "2026-02-16T11:59:59Z", title="Old Job"),
            build_scrape_job("future-job", "2026-03-18T12:00:01Z", title="Future Job"),
        ]
        page2_jobs = [
            build_scrape_job("boundary-job", RECENCY_BOUNDARY, title="Boundary Job"),
            build_scrape_job("page-2-job", "2026-03-17T10:00:00Z", title="Page 2 Job"),
        ]
        cm, browser, context, listing_page, _ = build_playwright_stack()
        listing_page.content.side_effect = ["<html>page 1</html>", "<html>page 2</html>"]

        with (
            patch("lokerbot.scrapers.lokerid.sync_playwright", return_value=cm),
            patch(
                "lokerbot.scrapers.lokerid._parse_listing_html",
                side_effect=[
                    (page1_jobs, {"current_page": 1, "last_page": 2}),
                    (page2_jobs, {"current_page": 2, "last_page": 2}),
                ],
            ),
            patch("lokerbot.scrapers.lokerid.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            jobs = scrape(max_pages=None, fetch_details=False, delay=0.0)

        self.assertEqual([job.job_id for job in jobs], ["today-job", "boundary-job", "page-2-job"])
        self.assertEqual(
            listing_page.goto.call_args_list,
            [
                call("https://www.loker.id/cari-lowongan-kerja", wait_until="networkidle", timeout=120000),
                call("https://www.loker.id/cari-lowongan-kerja/page/2", wait_until="networkidle", timeout=120000),
            ],
        )
        listing_page.close.assert_called_once()
        browser.close.assert_called_once()
        context.close.assert_called_once()

    def test_scrape_uses_listing_html_without_detail_enrichment(self) -> None:
        cm, browser, context, listing_page, _ = build_playwright_stack()
        listing_page.content.return_value = self.listing_html

        with (
            patch("lokerbot.scrapers.lokerid.sync_playwright", return_value=cm),
            patch("lokerbot.scrapers.lokerid.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            jobs = scrape(max_pages=1, fetch_details=False, delay=0.0)

        self.assertEqual([job.job_id for job in jobs], ["1001", "1002"])
        self.assertEqual(listing_page.goto.call_count, 1)
        listing_page.goto.assert_called_once_with("https://www.loker.id/cari-lowongan-kerja", wait_until="networkidle", timeout=120000)
        context.new_page.assert_called_once()
        listing_page.close.assert_called_once()
        browser.close.assert_called_once()
        context.close.assert_called_once()

    def test_scrape_fetches_detail_page_for_missing_fields_even_without_fetch_details(self) -> None:
        detail_page = Mock()
        cm, browser, context, listing_page, _ = build_playwright_stack(detail_page)
        listing_page.content.return_value = "<html><body></body></html>"
        detail_page.content.return_value = self.detail_html

        incomplete_job = build_scrape_job(
            "1003",
            "2026-03-16T10:00:00Z",
            title="Marketing Executive",
            company="PT Detail Sukses",
            url="https://www.loker.id/marketing/marketing-executive-1003.html",
            location=None,
            job_type=None,
            salary_range=None,
            tags=[],
        )

        with (
            patch(
                "lokerbot.scrapers.lokerid.sync_playwright",
                return_value=cm,
            ),
            patch(
                "lokerbot.scrapers.lokerid._parse_listing_html",
                return_value=([incomplete_job], {"current_page": 1, "last_page": 1}),
            ),
            patch("lokerbot.scrapers.lokerid.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            jobs = scrape(max_pages=1, fetch_details=False, delay=0.0)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "Surabaya")
        self.assertEqual(jobs[0].job_type, "Full Time")
        self.assertEqual(jobs[0].salary_range, "Rp 6,000,000 - Rp 9,000,000")
        self.assertEqual(jobs[0].tags, ["Digital Marketing", "Copywriting"])
        self.assertIsNone(jobs[0].description)
        detail_page.goto.assert_called_once_with(jobs[0].url, wait_until="networkidle", timeout=120000)
        detail_page.content.assert_called_once()
        listing_page.close.assert_called_once()
        detail_page.close.assert_called_once()
        browser.close.assert_called_once()
        context.close.assert_called_once()

    def test_scrape_populates_description_when_fetch_details_is_enabled(self) -> None:
        detail_page = Mock()
        cm, browser, context, listing_page, _ = build_playwright_stack(detail_page)
        listing_page.content.return_value = "<html><body></body></html>"
        detail_page.content.return_value = self.detail_html

        incomplete_job = build_scrape_job(
            "1003",
            "2026-03-16T10:00:00Z",
            title="Marketing Executive",
            company="PT Detail Sukses",
            url="https://www.loker.id/marketing/marketing-executive-1003.html",
            location=None,
            job_type=None,
            salary_range=None,
            tags=[],
        )

        with (
            patch("lokerbot.scrapers.lokerid.sync_playwright", return_value=cm),
            patch(
                "lokerbot.scrapers.lokerid._parse_listing_html",
                return_value=([incomplete_job], {"current_page": 1, "last_page": 1}),
            ),
            patch("lokerbot.scrapers.lokerid.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            jobs = scrape(max_pages=1, fetch_details=True, delay=0.0)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].description, "Kelola kampanye digital.\nBekerja dengan tim kreatif.")
        self.assertEqual(jobs[0].location, "Surabaya")
        self.assertEqual(jobs[0].job_type, "Full Time")
        self.assertEqual(jobs[0].salary_range, "Rp 6,000,000 - Rp 9,000,000")
        self.assertEqual(jobs[0].tags, ["Digital Marketing", "Copywriting"])
        detail_page.goto.assert_called_once_with(jobs[0].url, wait_until="networkidle", timeout=120000)
        detail_page.content.assert_called_once()
        listing_page.close.assert_called_once()
        detail_page.close.assert_called_once()
        browser.close.assert_called_once()
        context.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
