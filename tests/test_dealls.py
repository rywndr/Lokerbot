from __future__ import annotations

import os
from pathlib import Path
import unittest

from lokerbot.nextjs import extract_next_data
from lokerbot.scrapers.dealls import fetch_listing_page, parse_jobs, scrape

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "dealls_listing.html"
FIXTURE_SCRAPED_AT = "2026-03-16T12:00:00Z"


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
