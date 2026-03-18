from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from lokerbot.models import Job
from lokerbot.scrapers.kitalulus import (
    _collect_tags,
    _extract_description,
    _format_job_type,
    _format_location,
    _format_salary_range,
    _parse_and_filter_jobs,
    _parse_microsecond_timestamp,
    _parse_vacancy_doc,
    scrape,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_SCRAPED_AT_DT = datetime(2026, 3, 18, 0, 0, 0, tzinfo=timezone.utc)
FIXTURE_SCRAPED_AT = "2026-03-18T00:00:00Z"


def load_api_response_fixture():
    fixture_path = FIXTURES_DIR / "kitalulus_api_response.json"
    with open(fixture_path) as f:
        return json.load(f)


class KitaLulusUtilityTests(unittest.TestCase):
    def test_parse_microsecond_timestamp(self):
        timestamp = 1773720667000000
        result = _parse_microsecond_timestamp(timestamp)
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 3)
        self.assertEqual(result.day, 17)
        self.assertIsNone(_parse_microsecond_timestamp(None))
        self.assertIsNone(_parse_microsecond_timestamp(999999999999999999))

    def test_format_location(self):
        vacancy = {
            "city": {"name": "Jakarta Selatan"},
            "province": {"name": "DKI Jakarta"},
        }
        self.assertEqual(_format_location(vacancy), "Jakarta Selatan, DKI Jakarta")
        vacancy = {"province": {"name": "DKI Jakarta"}}
        self.assertEqual(_format_location(vacancy), "DKI Jakarta")
        vacancy = {"city": {"name": "Jakarta Selatan"}}
        self.assertEqual(_format_location(vacancy), "Jakarta Selatan")
        vacancy = {}
        self.assertIsNone(_format_location(vacancy))

    def test_format_job_type(self):
        vacancy = {"typeStr": "Full-Time"}
        self.assertEqual(_format_job_type(vacancy), "Full-Time")
        vacancy = {"type": "FULL_TIME"}
        self.assertEqual(_format_job_type(vacancy), "Full Time")
        vacancy = {}
        self.assertIsNone(_format_job_type(vacancy))

    def test_format_salary_range(self):
        vacancy = {
            "salaryLowerBound": 5000000,
            "salaryUpperBound": 8000000,
        }
        self.assertEqual(_format_salary_range(vacancy), "Rp 5,000,000 - Rp 8,000,000")
        vacancy = {
            "salaryLowerBoundStr": "Dapat Dinegosiasikan",
            "salaryUpperBoundStr": "Dapat Dinegosiasikan",
            "salaryLowerBound": 0,
            "salaryUpperBound": 0,
        }
        self.assertIsNone(_format_salary_range(vacancy))
        vacancy = {
            "salaryLowerBound": 5000000,
            "salaryUpperBound": 0,
        }
        self.assertEqual(_format_salary_range(vacancy), "Rp 5,000,000+")
        vacancy = {}
        self.assertIsNone(_format_salary_range(vacancy))

    def test_collect_tags(self):
        vacancy = {
            "jobRole": {"displayName": "Software Engineer"},
            "jobSpecialization": {"displayName": "IT & Software"},
            "jobFunction": "Programming",
            "educationLevelStr": "Minimal S1",
        }
        tags = _collect_tags(vacancy)
        self.assertEqual(len(tags), 4)
        self.assertIn("Software Engineer", tags)
        self.assertIn("IT & Software", tags)
        self.assertIn("Programming", tags)
        self.assertIn("Minimal S1", tags)
        vacancy = {
            "jobRole": {"displayName": "Sales"},
            "jobSpecialization": {"displayName": "Sales"},
            "jobFunction": "Sales",
        }
        tags = _collect_tags(vacancy)
        self.assertEqual(len(tags), 1)
        self.assertEqual(tags[0], "Sales")

    def test_extract_description(self):
        vacancy = {
            "formattedDescription": "<p>Job duties:</p><ul><li>Task 1</li><li>Task 2</li></ul>"
        }
        result = _extract_description(vacancy)
        self.assertIsNotNone(result)
        self.assertIn("Job duties:", result)
        self.assertIn("Task 1", result)
        self.assertIn("Task 2", result)
        self.assertNotIn("<p>", result)
        vacancy = {"requirementStr": "Must have 2 years experience"}
        self.assertIn("experience", _extract_description(vacancy))
        vacancy = {}
        self.assertIsNone(_extract_description(vacancy))


class KitaLulusParserTests(unittest.TestCase):
    def setUp(self):
        self.api_response = load_api_response_fixture()

    def test_parse_vacancy_doc_valid(self):
        vacancies = self.api_response["data"]["vacanciesV3"]["list"]
        vacancy = vacancies[0]
        job = _parse_vacancy_doc(vacancy, FIXTURE_SCRAPED_AT, FIXTURE_SCRAPED_AT_DT)
        self.assertIsNotNone(job)
        self.assertEqual(job.job_id, vacancy["code"])
        self.assertEqual(job.title, vacancy["positionName"])
        self.assertEqual(job.company, vacancy["company"]["name"])
        self.assertTrue(job.url.startswith("https://www.kitalulus.com/lowongan/detail/"))
        self.assertIn(vacancy["slug"], job.url)
        self.assertIsNotNone(job.posted_at)
        self.assertEqual(job.scraped_at, FIXTURE_SCRAPED_AT)

    def test_parse_vacancy_doc_missing_required_fields(self):
        vacancy = {
            "positionName": "Test Job",
            "slug": "test-job",
            "company": {"name": "Test Company"},
        }
        self.assertIsNone(
            _parse_vacancy_doc(vacancy, FIXTURE_SCRAPED_AT, FIXTURE_SCRAPED_AT_DT)
        )
        vacancy = {
            "code": "J123",
            "slug": "test-job",
            "company": {"name": "Test Company"},
        }
        self.assertIsNone(
            _parse_vacancy_doc(vacancy, FIXTURE_SCRAPED_AT, FIXTURE_SCRAPED_AT_DT)
        )
        vacancy = {
            "code": "J123",
            "positionName": "Test Job",
            "company": {"name": "Test Company"},
        }
        self.assertIsNone(
            _parse_vacancy_doc(vacancy, FIXTURE_SCRAPED_AT, FIXTURE_SCRAPED_AT_DT)
        )
        vacancy = {
            "code": "J123",
            "positionName": "Test Job",
            "slug": "test-job",
        }
        self.assertIsNone(
            _parse_vacancy_doc(vacancy, FIXTURE_SCRAPED_AT, FIXTURE_SCRAPED_AT_DT)
        )

    def test_parse_and_filter_jobs_recency(self):
        vacancies = self.api_response["data"]["vacanciesV3"]["list"]
        jobs = _parse_and_filter_jobs(
            vacancies, FIXTURE_SCRAPED_AT, FIXTURE_SCRAPED_AT_DT
        )
        self.assertGreater(len(jobs), 0)
        future_scraped_at_dt = FIXTURE_SCRAPED_AT_DT + timedelta(days=40)
        future_scraped_at = future_scraped_at_dt.isoformat().replace("+00:00", "Z")
        jobs = _parse_and_filter_jobs(vacancies, future_scraped_at, future_scraped_at_dt)
        self.assertEqual(len(jobs), 0)

    def test_parse_and_filter_jobs_handles_invalid_jobs(self):
        vacancies = self.api_response["data"]["vacanciesV3"]["list"]
        invalid_vacancy = {"code": "J999", "positionName": "Invalid Job"}
        vacancies_with_invalid = vacancies + [invalid_vacancy]
        jobs = _parse_and_filter_jobs(
            vacancies_with_invalid, FIXTURE_SCRAPED_AT, FIXTURE_SCRAPED_AT_DT
        )
        valid_jobs = _parse_and_filter_jobs(
            vacancies, FIXTURE_SCRAPED_AT, FIXTURE_SCRAPED_AT_DT
        )
        self.assertEqual(len(jobs), len(valid_jobs))


class KitaLulusScrapeTests(unittest.TestCase):
    def setUp(self):
        self.api_response = load_api_response_fixture()

    @patch("lokerbot.scrapers.kitalulus._build_session")
    @patch("lokerbot.scrapers.kitalulus._fetch_vacancies_page")
    def test_scrape_single_page(self, mock_fetch, mock_build_session):
        mock_session = MagicMock()
        mock_build_session.return_value = mock_session
        vacancies_data = self.api_response["data"]["vacanciesV3"]
        mock_fetch.return_value = vacancies_data
        jobs = scrape(max_pages=1, session=mock_session)
        self.assertGreater(len(jobs), 0)
        self.assertTrue(all(isinstance(job, Job) for job in jobs))
        mock_fetch.assert_called_once()

    @patch("lokerbot.scrapers.kitalulus._build_session")
    @patch("lokerbot.scrapers.kitalulus._fetch_vacancies_page")
    def test_scrape_multiple_pages(self, mock_fetch, mock_build_session):
        mock_session = MagicMock()
        mock_build_session.return_value = mock_session
        vacancies_data = self.api_response["data"]["vacanciesV3"]
        page1_data = vacancies_data.copy()
        page1_data["hasNextPage"] = True
        page2_data = vacancies_data.copy()
        page2_data["hasNextPage"] = False
        mock_fetch.side_effect = [page1_data, page2_data]
        jobs = scrape(max_pages=2, session=mock_session)
        self.assertGreater(len(jobs), 0)
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("lokerbot.scrapers.kitalulus._build_session")
    @patch("lokerbot.scrapers.kitalulus._fetch_vacancies_page")
    def test_scrape_stops_when_no_recent_jobs(self, mock_fetch, mock_build_session):
        mock_session = MagicMock()
        mock_build_session.return_value = mock_session
        old_timestamp = int(
            (datetime.now(tz=timezone.utc) - timedelta(days=40)).timestamp() * 1_000_000
        )
        vacancies_data = self.api_response["data"]["vacanciesV3"]
        for vacancy in vacancies_data["list"]:
            vacancy["updatedAt"] = old_timestamp
        mock_fetch.return_value = vacancies_data
        jobs = scrape(max_pages=5, session=mock_session)
        self.assertEqual(len(jobs), 0)
        mock_fetch.assert_called_once()

    @patch("lokerbot.scrapers.kitalulus._build_session")
    @patch("lokerbot.scrapers.kitalulus._fetch_vacancies_page")
    def test_scrape_handles_api_error_on_first_page(
        self, mock_fetch, mock_build_session
    ):
        mock_session = MagicMock()
        mock_build_session.return_value = mock_session
        mock_fetch.side_effect = Exception("API error")
        with self.assertRaises(ValueError) as context:
            scrape(max_pages=1, session=mock_session)
        self.assertIn("Failed to fetch first page", str(context.exception))

    @patch("lokerbot.scrapers.kitalulus._build_session")
    @patch("lokerbot.scrapers.kitalulus._fetch_vacancies_page")
    def test_scrape_handles_api_error_on_subsequent_page(
        self, mock_fetch, mock_build_session
    ):
        mock_session = MagicMock()
        mock_build_session.return_value = mock_session
        vacancies_data = self.api_response["data"]["vacanciesV3"]
        vacancies_data["hasNextPage"] = True
        mock_fetch.side_effect = [vacancies_data, Exception("API error")]
        jobs = scrape(max_pages=2, session=mock_session)
        self.assertGreater(len(jobs), 0)
        self.assertEqual(mock_fetch.call_count, 2)
