from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, call

from bs4 import BeautifulSoup

from lokerbot.models import Job
from lokerbot.scrapers.karirhub import (
    KARIRHUB_LISTING_API_URL,
    LISTING_PAGE_SIZE,
    _enrich_job_from_detail,
    _format_salary_range,
    _parse_detail_page,
    fetch_listing_page,
    parse_jobs,
    scrape,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
LISTING_PAYLOAD_PATH = FIXTURE_DIR / "karirhub_listing_payload.json"
DETAIL_FIXTURE_PATH = FIXTURE_DIR / "karirhub_detail.html"
FIXTURE_SCRAPED_AT = "2026-03-16T12:00:00Z"
DETAIL_LOCATION = "Jakarta Selatan, DKI Jakarta"
DETAIL_SALARY = "Rp 5,000,000 - Rp 8,000,000"
DETAIL_DESCRIPTION = (
    "Deskripsi Pekerjaan\n"
    "Membangun dan memelihara layanan.\n\n"
    "Persyaratan Khusus\n"
    "3+ tahun pengalaman.\n\n"
    "Persyaratan Umum\n"
    "Mampu bekerja dalam tim.\n\n"
    "Keterampilan\n"
    "Python\n"
    "Go"
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


def _response_with_json(payload: dict[str, Any]) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def _response_with_text(text: str) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.text = text
    return response


class KarirhubParserTests(unittest.TestCase):
    def setUp(self) -> None:
        with open(LISTING_PAYLOAD_PATH, encoding="utf-8") as f:
            self.payload = json.load(f)
        self.detail_html = DETAIL_FIXTURE_PATH.read_text(encoding="utf-8")

    def test_fetch_listing_page_uses_requests_session(self) -> None:
        session = Mock()
        session.get.return_value = _response_with_json(self.payload)

        payload = fetch_listing_page(page_number=2, session=session)

        self.assertEqual(payload, self.payload)
        session.get.assert_called_once_with(
            KARIRHUB_LISTING_API_URL,
            params={"page": 2, "limit": LISTING_PAGE_SIZE},
            timeout=30,
        )

    def test_parse_jobs_from_fixture_returns_normalized_jobs(self) -> None:
        jobs = parse_jobs(self.payload, scraped_at=FIXTURE_SCRAPED_AT)

        self.assertEqual(len(jobs), 2)

        first_job = jobs[0]
        self.assertEqual(first_job.job_id, "job-1")
        self.assertEqual(first_job.title, "Backend Engineer")
        self.assertEqual(first_job.company, "PT Example Tech")
        self.assertEqual(first_job.location, "Jakarta Selatan")
        self.assertEqual(first_job.job_type, "Full Time")
        self.assertEqual(first_job.salary_range, DETAIL_SALARY)
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

    def test_parse_jobs_keeps_only_recent_items(self) -> None:
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

        jobs = parse_jobs({"data": payload_items}, scraped_at=FIXTURE_SCRAPED_AT)

        self.assertEqual([job.job_id for job in jobs], ["job-1", "job-2"])

    def test_parse_detail_page_extracts_plain_text_sections(self) -> None:
        detail = _parse_detail_page(self.detail_html)

        self.assertEqual(detail["location"], DETAIL_LOCATION)
        self.assertEqual(detail["job_type"], "Full Time")
        self.assertEqual(detail["salary_range"], DETAIL_SALARY)
        self.assertEqual(detail["tags"], ["Python", "Go"])
        self.assertEqual(detail["description"], DETAIL_DESCRIPTION)

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

        self.assertEqual(job.location, DETAIL_LOCATION)
        self.assertEqual(job.job_type, "Full Time")
        self.assertEqual(job.salary_range, DETAIL_SALARY)
        self.assertEqual(job.tags, ["Python", "Go"])
        self.assertEqual(job.description, DETAIL_DESCRIPTION)
        session.get.assert_called_once_with(job.url, timeout=30)


class KarirhubScrapeTests(unittest.TestCase):
    def setUp(self) -> None:
        with open(LISTING_PAYLOAD_PATH, encoding="utf-8") as f:
            self.payload = json.load(f)
        self.detail_html = DETAIL_FIXTURE_PATH.read_text(encoding="utf-8")

    def test_scrape_deduplicates_and_stops_after_empty_new_jobs(self) -> None:
        session = Mock()
        session.get.side_effect = [
            _response_with_json(self.payload),
            _response_with_json(self.payload),
        ]

        jobs = scrape(max_pages=2, fetch_details=False, delay=0.0, session=session)

        self.assertEqual([job.job_id for job in jobs], ["job-1", "job-2"])
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(
            session.get.call_args_list,
            [
                call(
                    KARIRHUB_LISTING_API_URL,
                    params={"page": 1, "limit": LISTING_PAGE_SIZE},
                    timeout=30,
                ),
                call(
                    KARIRHUB_LISTING_API_URL,
                    params={"page": 2, "limit": LISTING_PAGE_SIZE},
                    timeout=30,
                ),
            ],
        )

    def test_scrape_enriches_each_new_job_when_enabled(self) -> None:
        session = Mock()
        session.get.side_effect = [
            _response_with_json(self.payload),
            _response_with_text(self.detail_html),
            _response_with_text(self.detail_html),
        ]

        jobs = scrape(max_pages=1, fetch_details=True, delay=0.0, session=session)

        self.assertEqual([job.description for job in jobs], [DETAIL_DESCRIPTION, DETAIL_DESCRIPTION])
        self.assertEqual([job.location for job in jobs], [DETAIL_LOCATION, DETAIL_LOCATION])
        self.assertEqual(
            session.get.call_args_list[0],
            call(
                KARIRHUB_LISTING_API_URL,
                params={"page": 1, "limit": LISTING_PAGE_SIZE},
                timeout=30,
            ),
        )
        self.assertCountEqual(
            session.get.call_args_list[1:],
            [
                call(jobs[0].url, timeout=30),
                call(jobs[1].url, timeout=30),
            ],
        )

    def test_scrape_with_all_pages_keeps_pagination_running(self) -> None:
        page1_items = [
            build_item(
                "job-1",
                "Backend Engineer",
                "PT Example Tech",
                "Jakarta Selatan",
                1773648000,
                skills=["Python", "SQL", "Python"],
            )
        ]
        page2_items = [
            build_item(
                "job-2",
                "Frontend Engineer",
                "PT Example Studio",
                "Bandung",
                1773644400,
                show_salary=False,
                min_salary_amount=None,
                max_salary_amount=None,
                skills=[],
                job_function_name="Web Development",
                job_type_name="Contract",
            )
        ]
        page3_items = [
            build_item(
                "job-3",
                "QA Engineer",
                "PT Example Labs",
                "Bogor",
                1773640800,
                show_salary=False,
                min_salary_amount=None,
                max_salary_amount=None,
                skills=[],
                job_function_name="Quality Assurance",
            )
        ]

        session = Mock()
        session.get.side_effect = [
            _response_with_json({"data": page1_items}),
            _response_with_json({"data": page2_items}),
            _response_with_json({"data": page3_items}),
            _response_with_json({"data": []}),
        ]

        jobs = scrape(max_pages=None, fetch_details=False, delay=0.0, session=session)

        self.assertEqual([job.job_id for job in jobs], ["job-1", "job-2", "job-3"])
        self.assertEqual(session.get.call_count, 4)


if __name__ == "__main__":
    unittest.main()
