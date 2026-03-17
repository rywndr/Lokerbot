from __future__ import annotations

import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, call, patch

from lokerbot.nextjs import extract_next_data
from lokerbot.scrapers.glints import (
    LOGIN_GATE_TEXT,
    _extract_job_urls,
    _scrape_with_context,
    fetch_listing_page,
    parse_jobs,
    scrape,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
LISTING_FIXTURE_PATH = FIXTURE_DIR / "glints_listing.html"
DETAIL_FIXTURE_PATH = FIXTURE_DIR / "glints_job_detail.html"
FIXTURE_SCRAPED_AT = "2026-03-16T12:00:00Z"
FIXTURE_DETAIL_JOB_ID = "479cdeee-5dbf-4dbb-8138-0f7634d7eeae"
FIXTURE_DETAIL_DESCRIPTION = (
    "Kami sedang mencari kandidat profesional yang berpengalaman di bidang Procurement dan Contract Management untuk bergabung bersama tim kami.\n"
    "📌 Tanggung jawab utama:\n"
    "Mengelola proses PR ke PO.\n"
    "Memastikan akurasi dokumen procurement."
)


def build_raw_job(
    job_id: str,
    posted_at: str | None,
    *,
    title: str | None = None,
    company_name: str = "Example Co",
    job_type: str = "FULL_TIME",
    work_arrangement: str = "ONSITE",
    salary_min: int | None = 5_000_000,
    salary_max: int | None = 7_000_000,
    skill_names: list[str] | None = None,
    city_name: str = "Jakarta Selatan",
    province_name: str = "DKI Jakarta",
    include_location: bool = True,
) -> dict[str, object]:
    skills = skill_names or []
    raw_job: dict[str, object] = {
        "id": job_id,
        "title": title or f"Role {job_id}",
        "workArrangementOption": work_arrangement,
        "status": "OPEN",
        "createdAt": posted_at,
        "updatedAt": posted_at,
        "type": job_type,
        "company": {"id": f"company-{job_id}", "name": company_name},
        "country": {"code": "ID", "name": "Indonesia"},
        "salaries": [],
        "salaryEstimate": None,
        "skills": [
            {"skill": {"id": f"skill-{index}-{job_id}", "name": skill_name}, "mustHave": True}
            for index, skill_name in enumerate(skills)
        ],
        "hierarchicalJobCategory": {"id": f"category-{job_id}", "name": "Operations", "level": 3},
    }

    if salary_min is not None or salary_max is not None:
        raw_job["salaries"] = [
            {
                "id": f"salary-{job_id}",
                "salaryType": "BASIC",
                "salaryMode": "MONTH",
                "minAmount": salary_min,
                "maxAmount": salary_max,
                "CurrencyCode": "IDR",
            }
        ]

    if include_location:
        raw_job["location"] = {
            "id": f"location-{job_id}",
            "name": city_name,
            "formattedName": city_name,
            "level": 4,
            "parents": [
                {
                    "id": f"city-{job_id}",
                    "name": city_name,
                    "formattedName": city_name,
                    "level": 3,
                    "CountryCode": "ID",
                    "parents": [
                        {"level": 2, "formattedName": province_name, "slug": province_name.lower().replace(' ', '-')},
                        {"level": 1, "formattedName": "Indonesia", "slug": "indonesia"},
                    ],
                },
                {
                    "id": f"province-{job_id}",
                    "name": province_name,
                    "formattedName": province_name,
                    "level": 2,
                    "CountryCode": "ID",
                    "parents": [{"level": 1, "formattedName": "Indonesia", "slug": "indonesia"}],
                },
                {
                    "id": "country-id",
                    "name": "Indonesia",
                    "formattedName": "Indonesia",
                    "level": 1,
                    "CountryCode": "ID",
                    "parents": None,
                },
            ],
        }

    return raw_job


def build_listing_html(
    raw_jobs: list[dict[str, object]],
    *,
    has_more: bool,
    url_overrides: dict[str, str] | None = None,
) -> str:
    next_data = {
        "props": {
            "pageProps": {
                "initialJobs": {
                    "__typename": "JobSearchResults",
                    "expInfo": "fixture-exp",
                    "hasMore": has_more,
                    "jobsInPage": raw_jobs,
                }
            }
        }
    }
    url_overrides = url_overrides or {}
    cards = []
    for raw_job in raw_jobs:
        job_id = raw_job["id"]
        title = raw_job["title"]
        href = url_overrides.get(str(job_id), f"/id/opportunities/jobs/role-{job_id}/{job_id}")
        cards.append(
            f'<div class="job-search-results_job-card_link" data-gtm-job-id="{job_id}">'
            f'<a href="{href}">{title}</a>'
            "</div>"
        )
    return (
        "<!DOCTYPE html><html><body>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'
        f"{''.join(cards)}"
        "</body></html>"
    )


class GlintsParserTests(unittest.TestCase):
    def test_extract_next_data_and_parse_jobs_from_fixture(self) -> None:
        html = LISTING_FIXTURE_PATH.read_text(encoding="utf-8")

        next_data = extract_next_data(html)
        jobs = parse_jobs(next_data, job_urls=_extract_job_urls(html), scraped_at=FIXTURE_SCRAPED_AT)

        self.assertEqual(len(jobs), 2)

        first_job = jobs[0]
        self.assertEqual(first_job.job_id, "479cdeee-5dbf-4dbb-8138-0f7634d7eeae")
        self.assertEqual(first_job.title, "Ahli Gizi")
        self.assertEqual(first_job.company, "PT BINAJASA SUMBER SARANA")
        self.assertEqual(first_job.location, "Batam, Kepulauan Riau")
        self.assertEqual(first_job.job_type, "Full Time, On Site")
        self.assertEqual(first_job.salary_range, "IDR 5,300,000 - 5,500,000 / Month")
        self.assertEqual(
            first_job.url,
            "https://glints.com/id/opportunities/jobs/ahli-gizi/479cdeee-5dbf-4dbb-8138-0f7634d7eeae",
        )
        self.assertEqual(
            first_job.tags,
            ["On Site", "Clinical Nutrition", "Nutrition Education", "Food Nutrition", "Nutritionists"],
        )
        self.assertEqual(first_job.posted_at, "2026-03-16T09:05:54.423Z")
        self.assertEqual(first_job.scraped_at, FIXTURE_SCRAPED_AT)
        self.assertIsNone(first_job.description)

        second_job = jobs[1]
        self.assertEqual(second_job.location, "Jakarta Selatan, DKI Jakarta")
        self.assertEqual(second_job.job_type, "Part Time, On Site")
        self.assertEqual(second_job.salary_range, "IDR 4,000,000 - 5,500,000 / Month")
        self.assertEqual(
            second_job.url,
            "https://glints.com/id/opportunities/jobs/sales-promotion-girl-spg-event/a89a3a20-83d0-402e-8880-022aa1676513",
        )
        self.assertEqual(
            second_job.tags,
            [
                "On Site",
                "Store Display",
                "Sales Strategy",
                "Sales Management",
                "Customer Service",
                "B2C Sales",
                "Sales Analysis",
                "Sales and Marketing",
                "Communication Skills",
            ],
        )
        self.assertEqual(second_job.scraped_at, FIXTURE_SCRAPED_AT)
        self.assertIsNone(second_job.description)

    def test_parse_jobs_formats_machine_labels_for_job_type_and_tags(self) -> None:
        html = build_listing_html(
            [
                build_raw_job(
                    "hybrid-job",
                    "2026-03-16T09:00:00Z",
                    job_type="PROJECT_BASED",
                    work_arrangement="HYBRID",
                    skill_names=["Stakeholder Management"],
                )
            ],
            has_more=False,
        )

        jobs = parse_jobs(extract_next_data(html), job_urls=_extract_job_urls(html), scraped_at=FIXTURE_SCRAPED_AT)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_type, "Project Based, Hybrid")
        self.assertEqual(jobs[0].tags, ["Hybrid", "Stakeholder Management"])


class GlintsScrapeTests(unittest.TestCase):
    def test_scrape_keeps_only_jobs_from_today_back_to_30_days(self) -> None:
        context = Mock()
        listing_page = Mock()
        context.new_page.side_effect = [listing_page]
        first_page_html = build_listing_html(
            [
                build_raw_job("today-job", "2026-03-16T08:00:00Z"),
                build_raw_job("boundary-job", "2026-02-14T12:00:00Z"),
                build_raw_job("old-job", "2026-02-14T11:59:59Z"),
                build_raw_job("future-job", "2026-03-16T12:00:01Z"),
            ],
            has_more=False,
        )

        with (
            patch(
                "lokerbot.scrapers.glints._fetch_listing_snapshot",
                return_value={"html": first_page_html, "body_text": ""},
            ),
            patch("lokerbot.scrapers.glints.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            jobs = _scrape_with_context(context, max_pages=1, fetch_details=False, delay=0.0)

        self.assertEqual([job.job_id for job in jobs], ["today-job", "boundary-job"])

    def test_scrape_uses_requested_number_of_pages(self) -> None:
        context = Mock()
        listing_page = Mock()
        context.new_page.side_effect = [listing_page]
        first_page_html = build_listing_html([build_raw_job("page-1-job", "2026-03-16T08:00:00Z")], has_more=True)
        second_page_html = build_listing_html([build_raw_job("page-2-job", "2026-03-15T08:00:00Z")], has_more=False)

        with (
            patch(
                "lokerbot.scrapers.glints._fetch_listing_snapshot",
                side_effect=[
                    {"html": first_page_html, "body_text": ""},
                    {"html": second_page_html, "body_text": ""},
                ],
            ) as fetch_listing_snapshot_mock,
            patch("lokerbot.scrapers.glints.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            jobs = _scrape_with_context(context, max_pages=2, fetch_details=False, delay=0.0)

        self.assertEqual([job.job_id for job in jobs], ["page-1-job", "page-2-job"])
        self.assertEqual(
            fetch_listing_snapshot_mock.call_args_list,
            [call(listing_page, 1), call(listing_page, 2)],
        )

    def test_scrape_all_pages_warns_and_stops_when_follow_up_page_is_login_gated(self) -> None:
        context = Mock()
        listing_page = Mock()
        context.new_page.side_effect = [listing_page]
        first_page_html = build_listing_html([build_raw_job("page-1-job", "2026-03-16T08:00:00Z")], has_more=True)
        second_page_html = build_listing_html([], has_more=False)

        with (
            patch(
                "lokerbot.scrapers.glints._fetch_listing_snapshot",
                side_effect=[
                    {"html": first_page_html, "body_text": ""},
                    {"html": second_page_html, "body_text": f"{LOGIN_GATE_TEXT}\nDaftar"},
                ],
            ),
            patch("lokerbot.scrapers.glints.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            with self.assertWarnsRegex(RuntimeWarning, "login prompt"):
                jobs = _scrape_with_context(context, max_pages=None, fetch_details=False, delay=0.0)

        self.assertEqual([job.job_id for job in jobs], ["page-1-job"])

    def test_scrape_listing_only_best_effort_enrichment_keeps_description_empty(self) -> None:
        context = Mock()
        listing_page = Mock()
        detail_page = Mock()
        context.new_page.side_effect = [listing_page, detail_page]
        detail_html = DETAIL_FIXTURE_PATH.read_text(encoding="utf-8")
        base_job = build_raw_job(
            FIXTURE_DETAIL_JOB_ID,
            "2026-03-16T09:05:54.423Z",
            title="Ahli Gizi",
            company_name="PT BINAJASA SUMBER SARANA",
            salary_min=None,
            salary_max=None,
            skill_names=[],
            include_location=False,
        )
        base_job["country"] = {}
        base_job["type"] = None
        base_job["workArrangementOption"] = None
        listing_html = build_listing_html(
            [base_job],
            has_more=False,
            url_overrides={
                FIXTURE_DETAIL_JOB_ID: "/id/opportunities/jobs/ahli-gizi/479cdeee-5dbf-4dbb-8138-0f7634d7eeae"
            },
        )

        with (
            patch(
                "lokerbot.scrapers.glints._fetch_listing_snapshot",
                return_value={"html": listing_html, "body_text": ""},
            ),
            patch("lokerbot.scrapers.glints._fetch_detail_page_html", return_value=detail_html),
            patch("lokerbot.scrapers.glints.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            jobs = _scrape_with_context(context, max_pages=1, fetch_details=False, delay=0.0)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "Batam, Kepulauan Riau")
        self.assertIsNone(jobs[0].description)

    def test_scrape_best_effort_detail_enrichment_fills_missing_fields(self) -> None:
        context = Mock()
        listing_page = Mock()
        detail_page = Mock()
        context.new_page.side_effect = [listing_page, detail_page]
        detail_html = DETAIL_FIXTURE_PATH.read_text(encoding="utf-8")
        base_job = build_raw_job(
            FIXTURE_DETAIL_JOB_ID,
            "2026-03-16T09:05:54.423Z",
            title="Ahli Gizi",
            company_name="PT BINAJASA SUMBER SARANA",
            salary_min=None,
            salary_max=None,
            skill_names=[],
            include_location=False,
        )
        listing_html = build_listing_html(
            [base_job],
            has_more=False,
            url_overrides={
                FIXTURE_DETAIL_JOB_ID: "/id/opportunities/jobs/ahli-gizi/479cdeee-5dbf-4dbb-8138-0f7634d7eeae"
            },
        )

        with (
            patch(
                "lokerbot.scrapers.glints._fetch_listing_snapshot",
                return_value={"html": listing_html, "body_text": ""},
            ),
            patch("lokerbot.scrapers.glints._fetch_detail_page_html", return_value=detail_html),
            patch("lokerbot.scrapers.glints.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            jobs = _scrape_with_context(context, max_pages=1, fetch_details=True, delay=0.0)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "Batam, Kepulauan Riau")
        self.assertEqual(jobs[0].salary_range, "IDR 5,300,000 - 5,500,000 / Month")
        self.assertEqual(
            jobs[0].tags,
            ["On Site", "Clinical Nutrition", "Nutrition Education", "Food Nutrition", "Nutritionists"],
        )
        self.assertEqual(jobs[0].description, FIXTURE_DETAIL_DESCRIPTION)


class GlintsLiveSmokeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("LIVE_GLINTS_SMOKE") == "1",
        "Set LIVE_GLINTS_SMOKE=1 to run the live Glints smoke test.",
    )
    def test_scrape_single_page_live(self) -> None:
        html = fetch_listing_page()
        self.assertIn("__NEXT_DATA__", html)

        jobs = scrape(max_pages=1, fetch_details=False, delay=0.0)
        self.assertGreater(len(jobs), 0)

        sample = jobs[0]
        self.assertTrue(sample.title)
        self.assertTrue(sample.company)
        self.assertTrue(sample.url.startswith("https://glints.com/id/opportunities/jobs/"))
        self.assertTrue(sample.posted_at)


if __name__ == "__main__":
    unittest.main()
