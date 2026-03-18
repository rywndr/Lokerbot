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
- `--all-pages` reuses the first page’s pagination data, but the live API can still reject later pages with HTTP 400, so pagination stops early with a warning instead of crashing
- The scraper keeps the shared Dealls output shape aligned with the repo’s `Job` model

## Relevant code
- `lokerbot/scrapers/dealls.py`
- `lokerbot/nextjs.py`
- `lokerbot/utils.py`
- `lokerbot/models.py`
