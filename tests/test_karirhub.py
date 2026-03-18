from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from bs4 import BeautifulSoup

from lokerbot.models import Job
from lokerbot.scrapers.karirhub import (
    _click_next_listing_page,
    _enrich_job_from_detail,
    _format_salary_range,
    _parse_detail_page,
    parse_jobs,
    scrape,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
LISTING_FIXTURE_PATH = FIXTURE_DIR / "karirhub_listing.html"
LISTING_PAYLOAD_PATH = FIXTURE_DIR / "karirhub_listing_payload.json"
DETAIL_FIXTURE_PATH = FIXTURE_DIR / "karirhub_detail.html"
FIXTURE_SCRAPED_AT = "2026-03-16T12:00:00Z"


def build_card(title: str, company: str, location: str, salary: str | None = None) -> str:
    salary_html = (
        f"<sisnaker-element-karirhub-vacancy-price>{salary}</sisnaker-element-karirhub-vacancy-price>"
        if salary is not None
        else ""
    )
    return (
        "<sisnaker-element-karirhub-domestic-vacancy-card-web>"
        '<div class="header-section">'
        f"<div>{title}</div>"
        f"<div>{company}</div>"
        f"<div>{location}</div>"
        "</div>"
        f"{salary_html}"
        "</sisnaker-element-karirhub-domestic-vacancy-card-web>"
    )


def build_item(
    job_id: str,
    title: str,
    company_name: str,
    city_name: str,
    posted_at: int,
    *,
    show_salary: bool = True,
    min_salary_amount: int | None = 5_000_000,
    max_salary_amount: int | None = 8_000_000,
    skills: list[str] | None = None,
    job_function_name: str = "Engineering",
    job_type_name: str = "Full Time",
) -> dict[str, object]:
    item: dict[str, object] = {
        "id": job_id,
        "_id": job_id,
        "job_id": job_id,
        "title": title,
        "company_name": company_name,
        "city_name": city_name,
        "job_type_name": job_type_name,
        "published_at": posted_at,
        "show_salary": show_salary,
        "skills": skills or [],
        "job_function_name": job_function_name,
    }
    if min_salary_amount is not None:
        item["min_salary_amount"] = min_salary_amount
    if max_salary_amount is not None:
        item["max_salary_amount"] = max_salary_amount
    return item


class KarirhubParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.listing_html = LISTING_FIXTURE_PATH.read_text(encoding="utf-8")
        with open(LISTING_PAYLOAD_PATH, encoding="utf-8") as f:
            self.payload = json.load(f)
        self.detail_html = DETAIL_FIXTURE_PATH.read_text(encoding="utf-8")

    def test_parse_jobs_from_fixture_returns_normalized_jobs(self) -> None:
        jobs = parse_jobs(self.listing_html, self.payload, scraped_at=FIXTURE_SCRAPED_AT)

        self.assertEqual(len(jobs), 2)

        first_job = jobs[0]
        self.assertEqual(first_job.job_id, "job-1")
        self.assertEqual(first_job.title, "Backend Engineer")
        self.assertEqual(first_job.company, "PT Example Tech")
        self.assertEqual(first_job.location, "Jakarta Selatan")
        self.assertEqual(first_job.job_type, "Full Time")
        self.assertEqual(first_job.salary_range, "Rp 5,000,000 - Rp 8,000,000")
        self.assertEqual(
            first_job.url,
            "https://karirhub.kemnaker.go.id/lowongan-dalam-negeri/lowongan/backend-engineer-job-1",
        )
        self.assertEqual(first_job.tags, ["Python", "SQL"])
        self.assertEqual(first_job.posted_at, "2026-03-16T08:00:00Z")
        self.assertEqual(first_job.scraped_at, FIXTURE_SCRAPED_AT)
        self.assertIsNone(first_job.description)

        second_job = jobs[1]
        self.assertEqual(second_job.job_id, "job-2")
        self.assertEqual(second_job.title, "Frontend Engineer")
        self.assertEqual(second_job.company, "PT Example Studio")
        self.assertEqual(second_job.location, "Bandung")
        self.assertEqual(second_job.job_type, "Contract")
        self.assertIsNone(second_job.salary_range)
        self.assertEqual(
            second_job.url,
            "https://karirhub.kemnaker.go.id/lowongan-dalam-negeri/lowongan/frontend-engineer-job-2",
        )
        self.assertEqual(second_job.tags, ["Web Development"])
        self.assertEqual(second_job.posted_at, "2026-03-16T07:00:00Z")
        self.assertEqual(second_job.scraped_at, FIXTURE_SCRAPED_AT)
        self.assertIsNone(second_job.description)

    def test_parse_jobs_keeps_only_recent_cards(self) -> None:
        html = self.listing_html + build_card("Old Job", "Old Co", "Jakarta") + build_card(
            "Future Job",
            "Future Co",
            "Bandung",
        )
        payload_items = list(self.payload["data"])
        payload_items.extend(
            [
                build_item(
                    "old-job",
                    "Old Job",
                    "Old Co",
                    "Jakarta",
                    1771070399,
                    show_salary=False,
                    min_salary_amount=None,
                    max_salary_amount=None,
                    skills=[],
                    job_function_name="Operations",
                ),
                build_item(
                    "future-job",
                    "Future Job",
                    "Future Co",
                    "Bandung",
                    1773662401,
                    show_salary=False,
                    min_salary_amount=None,
                    max_salary_amount=None,
                    skills=[],
                    job_function_name="Operations",
                ),
            ]
        )

        jobs = parse_jobs(html, {"data": payload_items}, scraped_at=FIXTURE_SCRAPED_AT)

        self.assertEqual([job.job_id for job in jobs], ["job-1", "job-2"])

    def test_parse_detail_page_extracts_plain_text_sections(self) -> None:
        detail = _parse_detail_page(self.detail_html)

        self.assertEqual(detail["location"], "Jakarta Selatan, DKI Jakarta")
        self.assertEqual(detail["job_type"], "Full Time")
        self.assertEqual(detail["salary_range"], "Rp 5,000,000 - Rp 8,000,000")
        self.assertEqual(detail["tags"], ["Python", "Go"])
        self.assertEqual(detail["description"], "Deskripsi Pekerjaan\nMembangun dan memelihara layanan.\n\nPersyaratan Khusus\n3+ tahun pengalaman.\n\nPersyaratan Umum\nMampu bekerja dalam tim.\n\nKeterampilan\nPython\nGo")

    def test_parse_detail_page_returns_none_for_dirahasiakan_salary(self) -> None:
        detail = _parse_detail_page(
            "<html><body><div>Lowongan dalam negeri</div><div>Role</div><div>Jakarta</div>"
            "<div>Rentang gaji</div><div>Dirahasiakan</div></body></html>"
        )

        self.assertIsNone(detail["salary_range"])

    def test_format_salary_range_returns_none_for_dirahasiakan(self) -> None:
        card = BeautifulSoup(
            "<sisnaker-element-karirhub-domestic-vacancy-card-web>"
            "<sisnaker-element-karirhub-vacancy-price>Dirahasiakan</sisnaker-element-karirhub-vacancy-price>"
            "</sisnaker-element-karirhub-domestic-vacancy-card-web>",
            "html.parser",
        )

        self.assertIsNone(_format_salary_range(card, {"show_salary": False}))

    def test_enrich_job_from_detail_replaces_missing_fields(self) -> None:
        session = Mock()
        response = Mock()
        response.text = self.detail_html
        response.raise_for_status.return_value = None
        session.get.return_value = response

        job = Job(
            job_id="job-1",
            title="Backend Engineer",
            company="PT Example Tech",
            location="Indonesia",
            job_type=None,
            salary_range=None,
            url="https://karirhub.kemnaker.go.id/lowongan-dalam-negeri/lowongan/backend-engineer-job-1",
            tags=["Existing"],
            posted_at="2026-03-16T08:00:00Z",
            scraped_at=FIXTURE_SCRAPED_AT,
        )

        _enrich_job_from_detail(session, job)

        self.assertEqual(job.location, "Jakarta Selatan, DKI Jakarta")
        self.assertEqual(job.job_type, "Full Time")
        self.assertEqual(job.salary_range, "Rp 5,000,000 - Rp 8,000,000")
        self.assertEqual(job.tags, ["Python", "Go"])
        self.assertEqual(
            job.description,
            "Deskripsi Pekerjaan\nMembangun dan memelihara layanan.\n\nPersyaratan Khusus\n3+ tahun pengalaman.\n\nPersyaratan Umum\nMampu bekerja dalam tim.\n\nKeterampilan\nPython\nGo",
        )
        session.get.assert_called_once_with(job.url, timeout=30)


class KarirhubScrapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.listing_html = LISTING_FIXTURE_PATH.read_text(encoding="utf-8")
        with open(LISTING_PAYLOAD_PATH, encoding="utf-8") as f:
            self.payload = json.load(f)

        self.browser = Mock()
        self.context = Mock()
        self.page = Mock()
        self.browser.new_context.return_value = self.context
        self.context.new_page.return_value = self.page

        self.playwright_cm = MagicMock()
        self.playwright_cm.__enter__.return_value = Mock()
        self.playwright_cm.__exit__.return_value = None

    def test_scrape_deduplicates_and_stops_after_empty_new_jobs(self) -> None:
        self.page.content.side_effect = [self.listing_html, self.listing_html]

        with (
            patch("lokerbot.scrapers.karirhub.sync_playwright", return_value=self.playwright_cm),
            patch("lokerbot.scrapers.karirhub._launch_browser", return_value=self.browser),
            patch("lokerbot.scrapers.karirhub._load_listing_page"),
            patch("lokerbot.scrapers.karirhub._fetch_listing_page_data", side_effect=[self.payload, self.payload]) as fetch_mock,
            patch("lokerbot.scrapers.karirhub._click_next_listing_page", side_effect=[True]) as click_mock,
        ):
            jobs = scrape(max_pages=2, fetch_details=False, delay=0.0)

        self.assertEqual([job.job_id for job in jobs], ["job-1", "job-2"])
        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(click_mock.call_count, 1)
        self.browser.new_context.assert_called_once_with(locale="id-ID", viewport={"width": 1280, "height": 720})
        self.context.new_page.assert_called_once()

    def test_click_next_listing_page_uses_wait_for_function_arg_keyword(self) -> None:
        pagination_buttons = Mock()
        next_button = Mock()
        button = Mock()
        pagination_buttons.count.return_value = 1
        pagination_buttons.last = next_button
        next_button.get_attribute.side_effect = [None, None]
        next_button.locator.return_value = button
        self.page.locator.return_value = pagination_buttons
        self.page.url = "https://example.com/page-1"

        result = _click_next_listing_page(self.page)

        self.assertTrue(result)
        button.click.assert_called_once_with()
        self.page.wait_for_function.assert_called_once_with(
            "oldUrl => window.location.href !== oldUrl",
            arg="https://example.com/page-1",
            timeout=120000,
        )

    def test_scrape_enriches_each_new_job_when_enabled(self) -> None:
        self.page.content.return_value = self.listing_html
        enrich_mock = Mock()

        def enrich_side_effect(*args: object) -> None:
            job = args[1]
            setattr(job, "description", f"enriched-{job.job_id}")

        enrich_mock.side_effect = enrich_side_effect

        with (
            patch("lokerbot.scrapers.karirhub.sync_playwright", return_value=self.playwright_cm),
            patch("lokerbot.scrapers.karirhub._launch_browser", return_value=self.browser),
            patch("lokerbot.scrapers.karirhub._load_listing_page"),
            patch("lokerbot.scrapers.karirhub._fetch_listing_page_data", return_value=self.payload),
            patch("lokerbot.scrapers.karirhub._click_next_listing_page", return_value=False),
            patch("lokerbot.scrapers.karirhub._enrich_job_from_detail", enrich_mock),
        ):
            jobs = scrape(max_pages=1, fetch_details=True, delay=0.0)

        self.assertEqual([job.description for job in jobs], ["enriched-job-1", "enriched-job-2"])
        self.assertEqual(enrich_mock.call_count, 2)

    def test_scrape_with_all_pages_keeps_pagination_running(self) -> None:
        self.page.content.side_effect = ["page-1-html", "page-2-html", "page-3-html"]
        page_jobs = [
            [
                Job(
                    job_id="job-1",
                    title="Backend Engineer",
                    company="PT Example Tech",
                    location="Jakarta Selatan",
                    job_type="Full Time",
                    salary_range=None,
                    url="https://karirhub.kemnaker.go.id/lowongan-dalam-negeri/lowongan/backend-engineer-job-1",
                    tags=[],
                    posted_at="2026-03-16T08:00:00Z",
                    scraped_at=FIXTURE_SCRAPED_AT,
                )
            ],
            [
                Job(
                    job_id="job-2",
                    title="Frontend Engineer",
                    company="PT Example Studio",
                    location="Bandung",
                    job_type="Contract",
                    salary_range=None,
                    url="https://karirhub.kemnaker.go.id/lowongan-dalam-negeri/lowongan/frontend-engineer-job-2",
                    tags=[],
                    posted_at="2026-03-16T07:00:00Z",
                    scraped_at=FIXTURE_SCRAPED_AT,
                )
            ],
            [
                Job(
                    job_id="job-3",
                    title="QA Engineer",
                    company="PT Example Labs",
                    location="Bogor",
                    job_type="Full Time",
                    salary_range=None,
                    url="https://karirhub.kemnaker.go.id/lowongan-dalam-negeri/lowongan/qa-engineer-job-3",
                    tags=[],
                    posted_at="2026-03-16T06:00:00Z",
                    scraped_at=FIXTURE_SCRAPED_AT,
                )
            ],
        ]

        with (
            patch("lokerbot.scrapers.karirhub.sync_playwright", return_value=self.playwright_cm),
            patch("lokerbot.scrapers.karirhub._launch_browser", return_value=self.browser),
            patch("lokerbot.scrapers.karirhub._load_listing_page"),
            patch("lokerbot.scrapers.karirhub._fetch_listing_page_data", side_effect=[{}, {}, {}]),
            patch("lokerbot.scrapers.karirhub._parse_listing_jobs", side_effect=page_jobs) as parse_mock,
            patch("lokerbot.scrapers.karirhub._click_next_listing_page", side_effect=[True, True, False]) as click_mock,
        ):
            jobs = scrape(max_pages=None, fetch_details=False, delay=0.0)

        self.assertEqual([job.job_id for job in jobs], ["job-1", "job-2", "job-3"])
        self.assertEqual(parse_mock.call_count, 3)
        self.assertEqual(click_mock.call_count, 3)


if __name__ == "__main__":
    unittest.main()
