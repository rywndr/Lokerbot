from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import Mock, call, patch

import requests

from lokerbot.nextjs import extract_next_data
from lokerbot.scrapers.dealls import fetch_listing_page, parse_jobs, scrape

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "dealls_listing.html"
FIXTURE_SCRAPED_AT = "2026-03-16T12:00:00Z"


def build_job_doc(job_id: str, posted_at: str | None) -> dict[str, object]:
    return {
        "id": job_id,
        "slug": f"{job_id}-slug",
        "role": f"Role {job_id}",
        "author": {"name": "Example Co"},
        "company": {"name": "Example Co", "slug": "example-co"},
        "employmentTypes": ["fullTime"],
        "city": {"name": "Jakarta"},
        "country": {"name": "Indonesia"},
        "salaryType": "paid",
        "salaryRange": None,
        "skills": [],
        "publishedAt": posted_at,
    }


class DeallsParserTests(unittest.TestCase):
    def test_extract_next_data_and_parse_jobs_from_fixture(self) -> None:
        html = FIXTURE_PATH.read_text(encoding="utf-8")

        next_data = extract_next_data(html)
        jobs = parse_jobs(next_data, scraped_at=FIXTURE_SCRAPED_AT)

        self.assertEqual(len(jobs), 2)

        first_job = jobs[0]
        self.assertEqual(first_job.job_id, "69b3dfa88ab1d30011f83787")
        self.assertEqual(first_job.title, "Presales Darwinbox")
        self.assertEqual(first_job.company, "PT Metrodata Electronics Tbk.")
        self.assertEqual(first_job.location, "Jakarta Barat, Indonesia")
        self.assertEqual(first_job.job_type, "Full Time")
        self.assertIsNone(first_job.salary_range)
        self.assertEqual(
            first_job.url,
            "https://dealls.com/loker/presales-darwinbox~pt-metrodata-electronics-tbk",
        )
        self.assertEqual(first_job.tags, ["On Site"])
        self.assertEqual(first_job.posted_at, "2026-03-13T09:58:00.715Z")
        self.assertEqual(first_job.scraped_at, FIXTURE_SCRAPED_AT)

        second_job = jobs[1]
        self.assertEqual(second_job.company, "Acme Learning")
        self.assertEqual(second_job.location, "Bandung, Indonesia")
        self.assertEqual(second_job.job_type, "Contract")
        self.assertEqual(second_job.salary_range, "IDR 10,500,000 - 13,000,000")
        self.assertEqual(
            second_job.url,
            "https://dealls.com/loker/learning-multimedia-specialist~acme-learning",
        )
        self.assertEqual(second_job.tags, ["Hybrid", "Adobe Premiere Pro", "Storyboarding"])
        self.assertEqual(second_job.scraped_at, FIXTURE_SCRAPED_AT)

    def test_parse_jobs_uses_remote_workplace_as_location_fallback(self) -> None:
        jobs = parse_jobs(
            {
                "docs": [
                    {
                        "id": "remote-job",
                        "slug": "remote-analyst",
                        "role": "Remote Analyst",
                        "author": {"name": "Remote Co"},
                        "company": {"name": "Remote Co", "slug": "remote-co"},
                        "employmentTypes": ["fullTime"],
                        "workplaceType": "remote",
                        "salaryType": "paid",
                        "salaryRange": None,
                        "skills": [],
                        "publishedAt": "2026-03-16T10:00:00.000Z",
                    }
                ]
            },
            scraped_at=FIXTURE_SCRAPED_AT,
        )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "Remote")
        self.assertEqual(jobs[0].tags, ["Remote"])


class DeallsScrapeTests(unittest.TestCase):
    def test_scrape_uses_total_pages_when_max_pages_is_none(self) -> None:
        session = Mock()
        query_params = {"pageSize": 20, "remoteOnly": True}
        first_page = {"docs": [], "totalPages": 3}
        second_page = {"docs": []}
        third_page = {"docs": []}

        with (
            patch("lokerbot.scrapers.dealls.fetch_listing_page", return_value="<html>"),
            patch("lokerbot.scrapers.dealls.extract_next_data", return_value={"runtimeConfig": {"version": "web-123"}}),
            patch("lokerbot.scrapers.dealls._extract_listing_query", return_value=(query_params, first_page)),
            patch("lokerbot.scrapers.dealls._parse_and_optionally_enrich", side_effect=[["page-1-job"], ["page-2-job"], ["page-3-job"]]) as parse_mock,
            patch("lokerbot.scrapers.dealls._fetch_api_page", side_effect=[second_page, third_page]) as fetch_api_page_mock,
            patch("lokerbot.scrapers.dealls.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            jobs = scrape(max_pages=None, fetch_details=False, delay=0.0, session=session)

        self.assertEqual(jobs, ["page-1-job", "page-2-job", "page-3-job"])
        self.assertEqual(
            parse_mock.call_args_list,
            [
                call(
                    first_page,
                    session=session,
                    fetch_details=False,
                    app_version="web-123",
                    scraped_at=FIXTURE_SCRAPED_AT,
                ),
                call(
                    second_page,
                    session=session,
                    fetch_details=False,
                    app_version="web-123",
                    scraped_at=FIXTURE_SCRAPED_AT,
                ),
                call(
                    third_page,
                    session=session,
                    fetch_details=False,
                    app_version="web-123",
                    scraped_at=FIXTURE_SCRAPED_AT,
                ),
            ],
        )
        self.assertEqual(
            fetch_api_page_mock.call_args_list,
            [
                call(session, page=2, query_params=query_params, app_version="web-123"),
                call(session, page=3, query_params=query_params, app_version="web-123"),
            ],
        )

    def test_scrape_all_pages_skips_api_requests_when_only_one_page_exists(self) -> None:
        session = Mock()
        first_page = {"docs": [], "totalPages": 1}

        with (
            patch("lokerbot.scrapers.dealls.fetch_listing_page", return_value="<html>"),
            patch("lokerbot.scrapers.dealls.extract_next_data", return_value={"runtimeConfig": {"version": "web-123"}}),
            patch("lokerbot.scrapers.dealls._extract_listing_query", return_value=({}, first_page)),
            patch("lokerbot.scrapers.dealls._parse_and_optionally_enrich", return_value=["page-1-job"]) as parse_mock,
            patch("lokerbot.scrapers.dealls._fetch_api_page") as fetch_api_page_mock,
            patch("lokerbot.scrapers.dealls.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            jobs = scrape(max_pages=None, fetch_details=False, delay=0.0, session=session)

        self.assertEqual(jobs, ["page-1-job"])
        parse_mock.assert_called_once_with(
            first_page,
            session=session,
            fetch_details=False,
            app_version="web-123",
            scraped_at=FIXTURE_SCRAPED_AT,
        )
        fetch_api_page_mock.assert_not_called()

    def test_scrape_all_pages_warns_and_falls_back_to_first_page_when_total_pages_missing(self) -> None:
        session = Mock()
        first_page = {"docs": []}

        with (
            patch("lokerbot.scrapers.dealls.fetch_listing_page", return_value="<html>"),
            patch("lokerbot.scrapers.dealls.extract_next_data", return_value={"runtimeConfig": {"version": "web-123"}}),
            patch("lokerbot.scrapers.dealls._extract_listing_query", return_value=({}, first_page)),
            patch("lokerbot.scrapers.dealls._parse_and_optionally_enrich", return_value=["page-1-job"]),
            patch("lokerbot.scrapers.dealls._fetch_api_page") as fetch_api_page_mock,
            patch("lokerbot.scrapers.dealls.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            with self.assertWarnsRegex(RuntimeWarning, "valid totalPages value"):
                jobs = scrape(max_pages=None, fetch_details=False, delay=0.0, session=session)

        self.assertEqual(jobs, ["page-1-job"])
        fetch_api_page_mock.assert_not_called()

    def test_scrape_all_pages_warns_and_stops_when_later_page_is_rejected(self) -> None:
        session = Mock()
        query_params = {"pageSize": 20}
        first_page = {"docs": [], "totalPages": 3}
        second_page = {"docs": []}
        page_error = requests.HTTPError("bad request", response=Mock(status_code=400))

        with (
            patch("lokerbot.scrapers.dealls.fetch_listing_page", return_value="<html>"),
            patch("lokerbot.scrapers.dealls.extract_next_data", return_value={"runtimeConfig": {"version": "web-123"}}),
            patch("lokerbot.scrapers.dealls._extract_listing_query", return_value=(query_params, first_page)),
            patch("lokerbot.scrapers.dealls._parse_and_optionally_enrich", side_effect=[["page-1-job"], ["page-2-job"]]) as parse_mock,
            patch("lokerbot.scrapers.dealls._fetch_api_page", side_effect=[second_page, page_error]) as fetch_api_page_mock,
            patch("lokerbot.scrapers.dealls.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            with self.assertWarnsRegex(RuntimeWarning, "page 3 was rejected, so pagination stopped at page 2"):
                jobs = scrape(max_pages=None, fetch_details=False, delay=0.0, session=session)

        self.assertEqual(jobs, ["page-1-job", "page-2-job"])
        self.assertEqual(parse_mock.call_count, 2)
        self.assertEqual(
            fetch_api_page_mock.call_args_list,
            [
                call(session, page=2, query_params=query_params, app_version="web-123"),
                call(session, page=3, query_params=query_params, app_version="web-123"),
            ],
        )

    def test_scrape_keeps_only_jobs_from_today_back_to_30_days(self) -> None:
        session = Mock()
        first_page = {
            "docs": [
                build_job_doc("today-job", "2026-03-16T08:00:00Z"),
                build_job_doc("boundary-job", "2026-02-14T12:00:00Z"),
                build_job_doc("old-job", "2026-02-14T11:59:59Z"),
                build_job_doc("future-job", "2026-03-16T12:00:01Z"),
            ],
            "totalPages": 1,
        }

        with (
            patch("lokerbot.scrapers.dealls.fetch_listing_page", return_value="<html>"),
            patch("lokerbot.scrapers.dealls.extract_next_data", return_value={"runtimeConfig": {"version": "web-123"}}),
            patch("lokerbot.scrapers.dealls._extract_listing_query", return_value=({}, first_page)),
            patch("lokerbot.scrapers.dealls.utc_now_iso", return_value=FIXTURE_SCRAPED_AT),
        ):
            jobs = scrape(max_pages=1, fetch_details=False, delay=0.0, session=session)

        self.assertEqual([job.job_id for job in jobs], ["today-job", "boundary-job"])


class DeallsLiveSmokeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("LIVE_DEALLS_SMOKE") == "1",
        "Set LIVE_DEALLS_SMOKE=1 to run the live Dealls smoke test.",
    )
    def test_scrape_single_page_live(self) -> None:
        html = fetch_listing_page()
        self.assertIn("__NEXT_DATA__", html)

        jobs = scrape(max_pages=1, fetch_details=False, delay=0.0)
        self.assertGreater(len(jobs), 0)

        sample = jobs[0]
        self.assertTrue(sample.title)
        self.assertTrue(sample.company)
        self.assertTrue(sample.location)
        self.assertTrue(sample.url.startswith("https://dealls.com/loker/"))


if __name__ == "__main__":
    unittest.main()
