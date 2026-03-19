# Karirhub scraper

## Overview
Karirhub is a requests-driven scraper that paginates the public listing API directly and builds normalized `Job` records from the API payload

## Workflow
1. Create a reusable `requests.Session`
2. Fetch each Karirhub listing page from `KARIRHUB_LISTING_API_URL` with `page` and `limit=18`
3. Parse the API payload into normalized jobs, keeping the stable job ID, posted timestamp, detail URL, location, salary, and tags
4. Keep only jobs posted within the last 30 days
5. Deduplicate repeated `job_id` values across pages and stop when a page returns no new jobs or the requested page limit is reached
6. Optionally fetch detail pages only for jobs that are still missing fields after listing parsing, enriching them with best-effort plain-text description, location, job type, salary, and tags
7. Sleep between page requests and detail requests when `--delay` is enabled

## Notes
- `--all-pages` is now much cheaper than the previous browser-driven implementation because listing pagination is direct HTTP instead of Playwright navigation
- The scraper still depends on the current public API payload shape
- `--fetch-details` is best-effort and now uses a small worker pool, failed detail requests do not abort the scrape
- The listing path no longer requires Playwright startup or rendered DOM cards

## Relevant code
- `lokerbot/scrapers/karirhub.py`
- `lokerbot/utils.py`
- `lokerbot/http_client.py`
- `lokerbot/models.py`
