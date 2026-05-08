from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import ANY, Mock, patch

import main
from lokerbot.models import Job
from lokerbot.scrapers import DEFAULT_SOURCE


def build_job(job_id: str = "job-1", source: str = "") -> Job:
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
        source=source,
    )


class MainCliTests(unittest.TestCase):
    def test_parse_args_defaults_source_to_dealls(self) -> None:
        args = main.parse_args([])

        self.assertEqual(args.source, DEFAULT_SOURCE)
        self.assertEqual(args.max_pages, 1)
        self.assertFalse(args.all_pages)

    def test_parse_args_accepts_karirhub_source(self) -> None:
        args = main.parse_args(["--source", "karirhub"])

        self.assertIn("karirhub", main.SCRAPERS)
        self.assertEqual(args.source, "karirhub")

    def test_main_routes_all_pages_to_karirhub_source(self) -> None:
        scraper = Mock(return_value=[build_job("karirhub-job")])

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(main, "SCRAPERS", {"karirhub": scraper}):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main.main(["--source", "karirhub", "--all-pages", "--output-dir", tmpdir])

            output_dir = Path(tmpdir) / "karirhub"
            output_paths = list(output_dir.glob("karirhub_*.json"))
            payload = json.loads(output_paths[0].read_text(encoding="utf-8"))
            output_path = output_paths[0]

        self.assertEqual(exit_code, 0)
        scraper.assert_called_once_with(max_pages=None, fetch_details=False, delay=0.0, max_jobs=None, progress=ANY)
        self.assertEqual(len(output_paths), 1)
        self.assertEqual(payload, [build_job("karirhub-job", source="karirhub").to_dict()])
        self.assertIn(str(output_path), stdout.getvalue())
        self.assertIn("from karirhub", stdout.getvalue())
        self.assertIn("[karirhub] starting scrape", stderr.getvalue())
        self.assertIn("[karirhub] finished scrape", stderr.getvalue())

    def test_main_uses_default_source_when_flag_omitted(self) -> None:
        scraper = Mock(return_value=[build_job("default-job")])

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(main, "SCRAPERS", {DEFAULT_SOURCE: scraper}):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main.main(["--output-dir", tmpdir])

        self.assertEqual(exit_code, 0)
        scraper.assert_called_once_with(max_pages=1, fetch_details=False, delay=0.0, max_jobs=None, progress=ANY)
        self.assertIn(f"from {DEFAULT_SOURCE}", stdout.getvalue())

    def test_main_routes_explicit_source_and_writes_output_under_source_directory(self) -> None:
        scraper = Mock(return_value=[build_job("explicit-job")])

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(main, "SCRAPERS", {DEFAULT_SOURCE: scraper}):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
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
        scraper.assert_called_once_with(max_pages=3, fetch_details=True, delay=0.25, max_jobs=None, progress=ANY)
        self.assertEqual(len(output_paths), 1)
        self.assertEqual(payload, [build_job("explicit-job", source=DEFAULT_SOURCE).to_dict()])
        self.assertIn(str(output_path), stdout.getvalue())

    def test_main_routes_all_pages_to_scraper(self) -> None:
        scraper = Mock(return_value=[build_job("all-pages-job")])

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(main, "SCRAPERS", {DEFAULT_SOURCE: scraper}):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main.main(["--source", DEFAULT_SOURCE, "--all-pages", "--output-dir", tmpdir])

        self.assertEqual(exit_code, 0)
        scraper.assert_called_once_with(max_pages=None, fetch_details=False, delay=0.0, max_jobs=None, progress=ANY)
        self.assertIn(f"from {DEFAULT_SOURCE}", stdout.getvalue())

    def test_main_routes_glints_source_and_writes_output_under_glints_directory(self) -> None:
        default_scraper = Mock(return_value=[build_job("default-job")])
        glints_scraper = Mock(return_value=[build_job("glints-job")])

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            main,
            "SCRAPERS",
            {DEFAULT_SOURCE: default_scraper, "glints": glints_scraper},
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main.main(["--source", "glints", "--output-dir", tmpdir])

            output_dir = Path(tmpdir) / "glints"
            output_paths = list(output_dir.glob("glints_*.json"))
            payload = json.loads(output_paths[0].read_text(encoding="utf-8"))
            output_path = output_paths[0]

        self.assertEqual(exit_code, 0)
        default_scraper.assert_not_called()
        glints_scraper.assert_called_once_with(max_pages=1, fetch_details=False, delay=0.0, max_jobs=None, progress=ANY)
        self.assertEqual(len(output_paths), 1)
        self.assertEqual(payload, [build_job("glints-job", source="glints").to_dict()])
        self.assertIn(str(output_path), stdout.getvalue())
        self.assertIn("from glints", stdout.getvalue())
        self.assertIn("[glints] starting scrape", stderr.getvalue())
        self.assertIn("[glints] finished scrape", stderr.getvalue())

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

    def test_main_rejects_source_combined_with_all_sources(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = main.main(["--source", "dealls", "--all-sources"])

        self.assertEqual(exit_code, 2)
        self.assertIn("mutually exclusive", stderr.getvalue())

    def test_main_all_sources_writes_combined_output_with_source_tags(self) -> None:
        dealls_scraper = Mock(return_value=[build_job("dealls-1"), build_job("dealls-2")])
        glints_scraper = Mock(return_value=[build_job("glints-1")])

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            main,
            "SCRAPERS",
            {"dealls": dealls_scraper, "glints": glints_scraper},
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main.main(["--all-sources", "--output-dir", tmpdir])

            combined_dir = Path(tmpdir) / "all"
            output_paths = list(combined_dir.glob("all_*.json"))
            payload = json.loads(output_paths[0].read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(output_paths), 1)
        self.assertEqual(len(payload), 3)
        self.assertEqual(
            sorted({entry["source"] for entry in payload}),
            ["dealls", "glints"],
        )
        self.assertIn("dealls=2", stdout.getvalue())
        self.assertIn("glints=1", stdout.getvalue())

    def test_main_max_jobs_passes_through_and_implies_all_pages(self) -> None:
        scraper = Mock(return_value=[build_job(f"job-{i}") for i in range(2)])

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(main, "SCRAPERS", {DEFAULT_SOURCE: scraper}):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = main.main(["--max-jobs", "2", "--output-dir", tmpdir])

        self.assertEqual(exit_code, 0)
        scraper.assert_called_once_with(max_pages=None, fetch_details=False, delay=0.0, max_jobs=2, progress=ANY)

    def test_main_benchmark_prints_json_and_writes_no_snapshot(self) -> None:
        scraper = Mock(return_value=[build_job("a"), build_job("b")])

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(main, "SCRAPERS", {DEFAULT_SOURCE: scraper}):
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                exit_code = main.main(["--source", DEFAULT_SOURCE, "--benchmark", "--output-dir", tmpdir])

            output_dir = Path(tmpdir) / DEFAULT_SOURCE

        self.assertEqual(exit_code, 0)
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["total_jobs"], 2)
        self.assertEqual(len(summary["results"]), 1)
        self.assertEqual(summary["results"][0]["source"], DEFAULT_SOURCE)
        self.assertEqual(summary["results"][0]["jobs"], 2)
        self.assertFalse(output_dir.exists())


if __name__ == "__main__":
    unittest.main()
