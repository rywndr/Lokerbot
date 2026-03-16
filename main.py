from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from lokerbot.scrapers.dealls import scrape


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape job listings from Dealls.")
    parser.add_argument("--max-pages", type=int, default=1, help="Number of Dealls result pages to scrape.")
    parser.add_argument(
        "--fetch-details",
        action="store_true",
        help="Fetch job-detail API data for listings missing key fields.",
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
        help="Directory where JSON snapshots should be written.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    jobs = scrape(max_pages=args.max_pages, fetch_details=args.fetch_details, delay=args.delay)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"dealls_{timestamp}.json"
    output_path.write_text(
        json.dumps([job.to_dict() for job in jobs], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Saved {len(jobs)} jobs to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
