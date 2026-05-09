from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.models import Job
from src.scrapers.kitalulus import (
    _bump_page,
    _collect_tags,
    _extract_description,
    _fetch_vacancies_page,
    _format_job_type,
    _format_location,
    _format_salary_range,
    _normalize_template,
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


_FAKE_REQUEST_TEMPLATE = {
    "method": "POST",
    "endpoint": "https://gql.kitalulus.com/graphql",
    "headers": {"content-type": "application/json"},
    "params": None,
    "body": {
        "operationName": "Vacancies",
        "variables": {"pagination": {"page": 1, "limit": 30}},
        "query": "query Vacancies($pagination: CommonFilter) { vacanciesV4 { list { id } } }",
    },
}


class KitaLulusScrapeTests(unittest.TestCase):
    def setUp(self):
        self.api_response = load_api_response_fixture()
        recent_us_timestamp = int(
            datetime.now(tz=timezone.utc).timestamp() * 1_000_000
        )
        for vacancy in self.api_response["data"]["vacanciesV3"]["list"]:
            vacancy["updatedAt"] = recent_us_timestamp
        bootstrap_patcher = patch(
            "src.scrapers.kitalulus._bootstrap_request_template",
            return_value=_FAKE_REQUEST_TEMPLATE,
        )
        self.mock_bootstrap = bootstrap_patcher.start()
        self.addCleanup(bootstrap_patcher.stop)

    @patch("src.scrapers.kitalulus._build_session")
    @patch("src.scrapers.kitalulus._fetch_vacancies_page")
    def test_scrape_single_page(self, mock_fetch, mock_build_session):
        mock_session = MagicMock()
        mock_build_session.return_value = mock_session
        vacancies_data = self.api_response["data"]["vacanciesV3"]
        mock_fetch.return_value = vacancies_data
        jobs = scrape(max_pages=1, session=mock_session)
        self.assertGreater(len(jobs), 0)
        self.assertTrue(all(isinstance(job, Job) for job in jobs))
        mock_fetch.assert_called_once()

    @patch("src.scrapers.kitalulus._build_session")
    @patch("src.scrapers.kitalulus._fetch_vacancies_page")
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

    @patch("src.scrapers.kitalulus._build_session")
    @patch("src.scrapers.kitalulus._fetch_vacancies_page")
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

    @patch("src.scrapers.kitalulus._build_session")
    @patch("src.scrapers.kitalulus._fetch_vacancies_page")
    def test_scrape_handles_api_error_on_first_page(
        self, mock_fetch, mock_build_session
    ):
        mock_session = MagicMock()
        mock_build_session.return_value = mock_session
        mock_fetch.side_effect = Exception("API error")
        with self.assertRaises(ValueError) as context:
            scrape(max_pages=1, session=mock_session)
        self.assertIn("Failed to fetch first page", str(context.exception))

    @patch("src.scrapers.kitalulus._build_session")
    @patch("src.scrapers.kitalulus._fetch_vacancies_page")
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

    @patch("src.scrapers.kitalulus._parse_and_filter_jobs")
    @patch("src.scrapers.kitalulus._build_session")
    @patch("src.scrapers.kitalulus._fetch_vacancies_page")
    def test_scrape_bumps_page_between_calls(
        self, mock_fetch, mock_build_session, mock_parse
    ):
        mock_session = MagicMock()
        mock_build_session.return_value = mock_session
        page1 = {"list": [], "hasNextPage": True, "elements": 60}
        page2 = {"list": [], "hasNextPage": True}
        page3 = {"list": [], "hasNextPage": False}
        mock_fetch.side_effect = [page1, page2, page3]
        sentinel_job = MagicMock(spec=Job)
        mock_parse.return_value = [sentinel_job]

        scrape(max_pages=3, session=mock_session)

        page_args = [call.kwargs.get("page") for call in mock_fetch.call_args_list]
        self.assertEqual(page_args, [1, 2, 3])

    @patch("src.scrapers.kitalulus._parse_and_filter_jobs")
    @patch("src.scrapers.kitalulus._fetch_vacancies_page")
    def test_scrape_runs_bootstrap_once(self, mock_fetch, mock_parse):
        mock_fetch.return_value = {"list": [], "hasNextPage": False}
        mock_parse.return_value = [MagicMock(spec=Job)]
        scrape(max_pages=1, session=MagicMock())

        self.assertEqual(self.mock_bootstrap.call_count, 1)


class KitaLulusRequestTemplateTests(unittest.TestCase):
    def test_bump_page_updates_pagination_slot(self):
        variables = {"pagination": {"page": 1, "limit": 30}, "keyword": ""}
        _bump_page(variables, 7)
        self.assertEqual(variables["pagination"]["page"], 7)
        self.assertEqual(variables["pagination"]["limit"], 30)

    def test_bump_page_updates_legacy_filter_slot(self):
        variables = {"filter": {"page": 0, "limit": 20}, "keyword": ""}
        _bump_page(variables, 4)
        self.assertEqual(variables["filter"]["page"], 4)

    def test_bump_page_updates_both_slots_when_present(self):
        variables = {
            "pagination": {"page": 0, "limit": 30},
            "filter": {"page": 0, "limit": 20},
        }
        _bump_page(variables, 9)
        self.assertEqual(variables["pagination"]["page"], 9)
        self.assertEqual(variables["filter"]["page"], 9)

    def test_bump_page_creates_pagination_if_missing(self):
        variables = {"keyword": ""}
        _bump_page(variables, 3)
        self.assertEqual(variables["pagination"], {"page": 3})

    def test_normalize_template_get_request(self):
        captured = {
            "method": "GET",
            "url": "https://gql.kitalulus.com/graphql?operationName=Vacancies&variables=%7B%22pagination%22%3A%7B%22page%22%3A1%2C%22limit%22%3A30%7D%7D",
            "headers": {
                "x-apollo-operation-name": "Vacancies",
                "Host": "gql.kitalulus.com",
                "Content-Length": "0",
            },
            "post_data": None,
        }
        template = _normalize_template(captured)
        self.assertEqual(template["method"], "GET")
        self.assertEqual(template["endpoint"], "https://gql.kitalulus.com/graphql")
        self.assertEqual(template["params"]["operationName"], "Vacancies")
        self.assertEqual(
            json.loads(template["params"]["variables"])["pagination"]["page"], 1
        )
        self.assertNotIn("Host", template["headers"])
        self.assertNotIn("Content-Length", template["headers"])

    def test_normalize_template_post_request(self):
        body = {
            "operationName": "Vacancies",
            "variables": {"pagination": {"page": 1, "limit": 30}},
            "query": "query Vacancies ...",
        }
        captured = {
            "method": "POST",
            "url": "https://gql.kitalulus.com/graphql",
            "headers": {"content-type": "application/json"},
            "post_data": json.dumps(body),
        }
        template = _normalize_template(captured)
        self.assertEqual(template["method"], "POST")
        self.assertEqual(template["body"], body)

    def test_fetch_vacancies_page_post_replays_with_bumped_pagination(self):
        template = {
            "method": "POST",
            "endpoint": "https://gql.kitalulus.com/graphql",
            "headers": {"content-type": "application/json"},
            "params": None,
            "body": {
                "operationName": "Vacancies",
                "variables": {"pagination": {"page": 1, "limit": 30}},
                "query": "...",
            },
        }
        session = MagicMock()
        session.post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={"data": {"vacanciesV4": {"list": [], "hasNextPage": False}}}
            ),
        )
        _fetch_vacancies_page(session=session, template=template, page=4)

        sent_payload = json.loads(session.post.call_args.kwargs["data"])
        self.assertEqual(sent_payload["variables"]["pagination"]["page"], 4)

    def test_fetch_vacancies_page_get_replays_with_bumped_filter(self):
        template = {
            "method": "GET",
            "endpoint": "https://gql.kitalulus.com/graphql",
            "headers": {"x-apollo-operation-name": "vacanciesV3"},
            "params": {
                "operationName": "vacanciesV3",
                "variables": json.dumps({"filter": {"page": 0, "limit": 20}}, separators=(",", ":")),
            },
            "body": None,
        }
        session = MagicMock()
        session.get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={"data": {"vacanciesV3": {"list": [], "hasNextPage": False}}}
            ),
        )
        _fetch_vacancies_page(session=session, template=template, page=2)

        sent_params = session.get.call_args.kwargs["params"]
        self.assertEqual(json.loads(sent_params["variables"])["filter"]["page"], 2)

    def test_fetch_vacancies_page_raises_on_graphql_errors(self):
        template = {
            "method": "POST",
            "endpoint": "https://gql.kitalulus.com/graphql",
            "headers": {"content-type": "application/json"},
            "params": None,
            "body": {"variables": {"pagination": {"page": 1}}},
        }
        session = MagicMock()
        session.post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"errors": [{"message": "boom"}]}),
        )
        with self.assertRaises(ValueError):
            _fetch_vacancies_page(session=session, template=template, page=1)


class KitaLulusRelativeTimeTests(unittest.TestCase):
    def setUp(self):
        from src.scrapers.kitalulus import _parse_indonesian_relative_time
        self.parse = _parse_indonesian_relative_time
        self.now = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)

    def test_minutes(self):
        result = self.parse("Terakhir diperbarui 5 menit yang lalu", self.now)
        self.assertEqual(result, self.now - timedelta(minutes=5))

    def test_hari(self):
        result = self.parse("3 hari yang lalu", self.now)
        self.assertEqual(result, self.now - timedelta(days=3))

    def test_minggu(self):
        result = self.parse("2 minggu yang lalu", self.now)
        self.assertEqual(result, self.now - timedelta(weeks=2))

    def test_baru_saja(self):
        self.assertEqual(self.parse("baru saja", self.now), self.now)

    def test_kemarin(self):
        self.assertEqual(self.parse("kemarin", self.now), self.now - timedelta(days=1))

    def test_unknown_returns_none(self):
        self.assertIsNone(self.parse("invalid format", self.now))
        self.assertIsNone(self.parse(None, self.now))
        self.assertIsNone(self.parse("", self.now))
