# Loker.id scraper

## Overview
Loker.id is a browser-driven scraper that starts from the public listings page, prefers the embedded Remix loader payload when it is available, and falls back to rendered cards when the page data is incomplete. It normalizes records into the shared `Job` model used across the project

## Workflow
1. Open public job listings with Playwright
2. Parse HTML with BeautifulSoup
3. Read the Remix `window.__remixContext` payload because it contains the richest job data and pagination metadata
4. Fall back to rendered cards and DOM URL mappings when the loader payload is missing or incomplete
5. Normalize each record into shared `Job` fields and keep only jobs inside the 30-day recency window
6. Optionally open detail pages to enrich missing location, job type, salary, tags, and plain-text description
7. Stop pagination when the metadata says there are no more results, when the configured page limit is reached, or when no usable jobs are found

## Notes
- Detail enrichment is best-effort; failures are converted into warnings and do not abort the scrape
- The scraper depends on the current listings HTML, Remix loader shape, and rendered card selectors
- Playwright is required because the scraper relies on browser rendering for the listings page
- Older and future-dated jobs are dropped by the shared recency filter
- Pagination can stop early if later pages expose no usable jobs or inconsistent metadata

## Relevant code
- `lokerbot/scrapers/lokerid.py`
- `lokerbot/utils.py`
- `lokerbot/models.py`
- `lokerbot/http_client.py`
