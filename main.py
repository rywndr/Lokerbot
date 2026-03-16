from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from lokerbot.scrapers import DEFAULT_SOURCE, SCRAPERS


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape job listings from supported sources.")
    parser.add_argument(
        "--source",
        choices=tuple(SCRAPERS),
        default=DEFAULT_SOURCE,
        help=f"Job source to scrape (default: {DEFAULT_SOURCE}).",
    )
    parser.add_argument("--max-pages", type=int, default=1, help="Number of result pages to scrape.")
    parser.add_argument(
        "--fetch-details",
        action="store_true",
        help="Fetch additional job-detail data for listings missing key fields.",
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


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    scraper = SCRAPERS[args.source]
    jobs = scraper(max_pages=args.max_pages, fetch_details=args.fetch_details, delay=args.delay)

    output_dir = Path(args.output_dir) / args.source
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"{args.source}_{timestamp}.json"
    output_path.write_text(
        json.dumps([job.to_dict() for job in jobs], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Saved {len(jobs)} jobs from {args.source} to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
