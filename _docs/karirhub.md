# Karirhub scraper

## Overview
Karirhub is a browser-driven scraper that combines rendered DOM cards with the public listing API to build normalized `Job` records

## Workflow
1. Launch Playwright and open the public Karirhub domestic listings page
2. Read the rendered vacancy cards from the DOM
3. Fetch the matching public listing API payload from the same browser session
4. Combine the DOM and API data so each record keeps a stable job ID, posted timestamp, and detail URL
5. Normalize each listing and keep only jobs posted within the last 30 days
6. Optionally fetch detail pages to enrich missing fields with best-effort plain-text description, location, job type, salary, and tags
7. Paginate until no new jobs are found or the selected page limit is reached

## Notes
- `--all-pages` can take noticeably longer than the other scrapers because Karirhub requires browser-driven navigation and optional detail enrichment
- The scraper depends on the current public DOM structure and listing API shape
- `--fetch-details` is best-effort; failed detail requests do not abort the scrape
- Karirhub requires the Playwright browser to be installed
- Current implementation hogs runtime and memory because it keeps all rendered DOM data in memory instead of using a hybrid browser-HTTP approach like the other scrapers, but this can be optimized in the future if needed


## Relevant code
- `lokerbot/scrapers/karirhub.py`
- `lokerbot/utils.py`
- `lokerbot/http_client.py`
- `lokerbot/models.py`
