# Loker.id scraper

## Overview
Loker.id is an HTTP-based scraper that fetches the public listings page over `requests.Session`, parses the embedded Remix `__remixContext` loader payload, and falls back to rendered cards when the loader payload is incomplete. It normalizes records into the shared `Job` model used across the project.

## Workflow
1. Fetch listings via `requests.Session.get` against `https://www.loker.id/cari-lowongan-kerja[/page/N]` — the page is server-rendered, so plain HTTP returns the same HTML the browser would render
2. Parse HTML with BeautifulSoup
3. Read the Remix `window.__remixContext` payload because it contains the richest job data and pagination metadata
4. Fall back to rendered cards and DOM URL mappings when the loader payload is missing or incomplete
5. Normalize each record into shared `Job` fields and keep only jobs inside the 30-day recency window
6. When a job is missing key fields (or `--fetch-details` is set), enqueue a detail fetch onto a scrape-scoped `ThreadPoolExecutor`; pagination keeps moving while detail requests run in parallel
7. Stop pagination when the metadata says there are no more results, when the configured page limit is reached, or when no usable jobs are found
8. Drain any pending detail futures before returning

## Performance
- Listing and detail pages are fetched with `requests.Session` (built via `lokerbot.http_client.build_session`); no headless browser is involved
- Detail enrichment runs on a `ThreadPoolExecutor` with `DETAIL_WORKER_COUNT=10` workers backed by a shared `SessionPool` (also from `lokerbot.http_client`), so up to 10 detail requests run concurrently
- Futures are submitted page-by-page and drained in batches of `5 * LISTING_PAGE_SIZE` to bound memory; remaining futures are drained in `finally`
- The previous Playwright-based implementation routinely took >5 minutes for 6 pages with details because every detail page was loaded sequentially with `wait_until="networkidle"`; the HTTP-based version finishes the same workload in tens of seconds

## Notes
- Detail enrichment is best-effort; per-job failures inside a future become `RuntimeWarning`s and do not abort the scrape
- The scraper depends on the current listings HTML, Remix loader shape, and rendered card selectors
- Older and future-dated jobs are dropped by the shared recency filter both inside the parser and again at the scrape level (defensive against custom parser overrides used by tests)
- Pagination can stop early if later pages expose no usable jobs or inconsistent metadata
- A caller-supplied `session=` kwarg disables the internal `SessionPool` and routes all detail fetches through the supplied session, which makes the scraper trivially mockable in tests

## Relevant code
- `lokerbot/scrapers/lokerid.py`
- `lokerbot/http_client.py` (provides `build_session` and `SessionPool`)
- `lokerbot/utils.py`
- `lokerbot/models.py`
