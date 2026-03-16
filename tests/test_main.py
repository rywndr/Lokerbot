from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import main
from lokerbot.models import Job
from lokerbot.scrapers import DEFAULT_SOURCE


def build_job(job_id: str = "job-1") -> Job:
    return Job(
        job_id=job_id,
        title="Backend Engineer",
        company="Example Co",
        location="Jakarta, Indonesia",
        job_type="Full Time",
        salary_range=None,
        url=f"https://example.com/jobs/{job_id}",
        tags=["Remote"],
        posted_at="2026-03-16T10:00:00Z",
        scraped_at="2026-03-16T12:00:00Z",
    )


class MainCliTests(unittest.TestCase):
    def test_parse_args_defaults_source_to_dealls(self) -> None:
        args = main.parse_args([])

        self.assertEqual(args.source, DEFAULT_SOURCE)
        self.assertEqual(args.max_pages, 1)
        self.assertFalse(args.all_pages)

    def test_main_uses_default_source_when_flag_omitted(self) -> None:
        scraper = Mock(return_value=[build_job("default-job")])

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(main, "SCRAPERS", {DEFAULT_SOURCE: scraper}):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main.main(["--output-dir", tmpdir])

        self.assertEqual(exit_code, 0)
        scraper.assert_called_once_with(max_pages=1, fetch_details=False, delay=0.0)
        self.assertIn(f"from {DEFAULT_SOURCE}", stdout.getvalue())

    def test_main_routes_explicit_source_and_writes_output_under_source_directory(self) -> None:
        scraper = Mock(return_value=[build_job("explicit-job")])

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(main, "SCRAPERS", {DEFAULT_SOURCE: scraper}):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main.main(
                    [
                        "--source",
                        DEFAULT_SOURCE,
                        "--max-pages",
                        "3",
                        "--fetch-details",
                        "--delay",
                        "0.25",
                        "--output-dir",
                        tmpdir,
                    ]
                )

            output_dir = Path(tmpdir) / DEFAULT_SOURCE
            output_paths = list(output_dir.glob(f"{DEFAULT_SOURCE}_*.json"))
            payload = json.loads(output_paths[0].read_text(encoding="utf-8"))
            output_path = output_paths[0]

        self.assertEqual(exit_code, 0)
        scraper.assert_called_once_with(max_pages=3, fetch_details=True, delay=0.25)
        self.assertEqual(len(output_paths), 1)
        self.assertEqual(payload, [build_job("explicit-job").to_dict()])
        self.assertIn(str(output_path), stdout.getvalue())

    def test_main_routes_all_pages_to_scraper(self) -> None:
        scraper = Mock(return_value=[build_job("all-pages-job")])

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(main, "SCRAPERS", {DEFAULT_SOURCE: scraper}):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main.main(["--source", DEFAULT_SOURCE, "--all-pages", "--output-dir", tmpdir])

        self.assertEqual(exit_code, 0)
        scraper.assert_called_once_with(max_pages=None, fetch_details=False, delay=0.0)
        self.assertIn(f"from {DEFAULT_SOURCE}", stdout.getvalue())

    def test_parse_args_rejects_invalid_source(self) -> None:
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as exc:
            main.parse_args(["--source", "invalid-source"])

        self.assertNotEqual(exc.exception.code, 0)
        self.assertIn("invalid choice", stderr.getvalue())

    def test_parse_args_rejects_all_pages_with_max_pages(self) -> None:
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as exc:
            main.parse_args(["--all-pages", "--max-pages", "3"])

        self.assertNotEqual(exc.exception.code, 0)
        self.assertIn("--all-pages", stderr.getvalue())
        self.assertIn("--max-pages", stderr.getvalue())
        self.assertIn("not allowed with argument", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
