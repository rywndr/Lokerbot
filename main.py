from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from src.cli_progress import run_scraper_with_progress
from src.models import Job
from src.scrapers import DEFAULT_SOURCE, SCRAPERS


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape job listings from supported sources.")
    parser.add_argument(
        "--source",
        choices=tuple(SCRAPERS),
        default=DEFAULT_SOURCE,
        help=f"Job source to scrape (default: {DEFAULT_SOURCE}).",
    )
    parser.add_argument(
        "--all-sources",
        action="store_true",
        help="Scrape every supported source and write a single combined snapshot.",
    )
    pagination_group = parser.add_mutually_exclusive_group()
    pagination_group.add_argument("--max-pages", type=int, default=1, help="Number of result pages to scrape.")
    pagination_group.add_argument(
        "--all-pages",
        action="store_true",
        help="Scrape every available result page.",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Cap each scraper's output to the first N jobs. Implies --all-pages when no pagination flag is set.",
    )
    parser.add_argument(
        "--fetch-details",
        action="store_true",
        help="Fetch additional job-detail data for listings missing key fields.",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run scraper(s) in performance-test mode. Prints a JSON perf summary to stdout and writes no snapshot.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Delay in seconds between paginated API requests.",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Root directory where source-specific JSON snapshots should be written.",
    )
    return parser.parse_args(argv)


def _run_one(
    source: str,
    *,
    max_pages: int | None,
    fetch_details: bool,
    delay: float,
    max_jobs: int | None,
) -> tuple[list[Job], float]:
    scraper = SCRAPERS[source]
    start = time.perf_counter()
    jobs = run_scraper_with_progress(
        source,
        scraper,
        max_pages=max_pages,
        fetch_details=fetch_details,
        delay=delay,
        max_jobs=max_jobs,
    )
    elapsed = time.perf_counter() - start
    for job in jobs:
        job.source = source
    return jobs, elapsed


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    source_explicit = any(tok == "--source" or tok.startswith("--source=") for tok in raw_argv)
    if args.all_sources and source_explicit:
        print("error: --source and --all-sources are mutually exclusive", file=sys.stderr)
        return 2

    sources = list(SCRAPERS) if args.all_sources else [args.source]

    if args.max_jobs is not None and args.max_jobs <= 0:
        print("error: --max-jobs must be a positive integer", file=sys.stderr)
        return 2

    max_pages_explicit = any(tok == "--max-pages" or tok.startswith("--max-pages=") for tok in raw_argv)
    if args.all_pages or (args.max_jobs is not None and not max_pages_explicit):
        max_pages: int | None = None
    else:
        max_pages = args.max_pages

    per_source: list[tuple[str, list[Job], float]] = []
    for source in sources:
        jobs, elapsed = _run_one(
            source,
            max_pages=max_pages,
            fetch_details=args.fetch_details,
            delay=args.delay,
            max_jobs=args.max_jobs,
        )
        per_source.append((source, jobs, elapsed))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if args.benchmark:
        results = [
            {
                "source": source,
                "elapsed_seconds": round(elapsed, 3),
                "jobs": len(jobs),
                "jobs_per_second": round(len(jobs) / elapsed, 3) if elapsed > 0 else 0.0,
            }
            for source, jobs, elapsed in per_source
        ]
        total_elapsed = sum(elapsed for _, _, elapsed in per_source)
        total_jobs = sum(len(jobs) for _, jobs, _ in per_source)
        summary = {
            "scraped_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "config": {
                "max_pages": max_pages,
                "all_pages": max_pages is None,
                "fetch_details": args.fetch_details,
                "delay": args.delay,
                "max_jobs": args.max_jobs,
            },
            "results": results,
            "total_elapsed_seconds": round(total_elapsed, 3),
            "total_jobs": total_jobs,
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    output_root = Path(args.output_dir)

    if args.all_sources:
        combined: list[dict[str, object]] = []
        for _, jobs, _ in per_source:
            combined.extend(job.to_dict() for job in jobs)
        output_dir = output_root / "all"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"all_{timestamp}.json"
        output_path.write_text(
            json.dumps(combined, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        breakdown = ", ".join(f"{source}={len(jobs)}" for source, jobs, _ in per_source)
        print(f"Saved {len(combined)} jobs ({breakdown}) to {output_path}")
        return 0

    source, jobs, _ = per_source[0]
    output_dir = output_root / source
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{source}_{timestamp}.json"
    output_path.write_text(
        json.dumps([job.to_dict() for job in jobs], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Saved {len(jobs)} jobs from {source} to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
