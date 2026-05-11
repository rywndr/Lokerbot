# Dealls scraper

## Overview
Dealls is a Next.js-based scraper that reads the public listings page, extracts the dehydrated page data, and normalizes the job listings into the shared `Job` model

## Workflow
1. Fetch the Dealls listing page HTML
2. Extract the embedded `__NEXT_DATA__` payload
3. Read the initial jobs query and pagination metadata from the dehydrated page data
4. Normalize each listing into `Job` fields and keep only jobs posted within the last 30 days
5. Optionally fetch detail data to fill missing fields, including plain-text descriptions.
6. Save the results through `main.py`

## Notes
- `--fetch-details` is best-effort enrichment; missing fields from an unavailable detail page are left as-is
- With `--fetch-details`, per-job detail requests run in parallel on a 10-worker thread pool against a shared `SessionPool`, while pagination remains serial and the 30-day recency filter still applies. Pending futures are drained in ~5-page batches to bound memory, and any individual detail failure emits a `RuntimeWarning` instead of aborting the run. The parallel path only activates when the scraper owns its session (i.e. no external `session=` is injected), so tests that pass a mock session keep using the inline serial path.
- `--all-pages` reuses the first page’s pagination data, but the live API can still reject later pages with HTTP 400. When that happens, pagination stops cleanly at the previous page and reports `api exhausted at page N/M` through the progress callback instead of emitting a Python warning or crashing
- The scraper keeps the shared Dealls output shape aligned with the repo’s `Job` model

## Relevant code
- `src/scrapers/dealls.py`
- `src/nextjs.py`
- `src/utils.py`
- `src/models.py`
