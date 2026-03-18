# Glints scraper

## Overview
Glints uses Playwright to load the public listings page, read the client-rendered Next.js payload, and normalize the results into the shared `Job` model

## Workflow
1. Launch Playwright and open the public listings page
2. Read the page’s `__NEXT_DATA__` payload and resolve Apollo cache references when Glints stores listing data there
3. Extract public job URLs from the rendered DOM so each record keeps the public Glints link
4. Normalize each listing and keep only jobs posted within the last 30 days
5. Optionally open detail pages to fill missing location, salary, job type, tags, and plain-text description fields
6. Stop pagination early when later pages become login-gated or otherwise unloadable

## Notes
- Firefox is the default browser because it has been more reliable than Chromium in this environment
- `--fetch-details` is best-effort enrichment; blocked or changed detail pages do not fail the whole scrape
- `--all-pages` can still stop before the end of the listing set if Glints starts showing a login prompt on follow-up pages

## Relevant code
- `lokerbot/scrapers/glints.py`
- `lokerbot/nextjs.py`
- `lokerbot/utils.py`
- `lokerbot/models.py`
