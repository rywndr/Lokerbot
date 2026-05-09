# KitaLulus scraper

## Overview
KitaLulus is a hybrid scraper. It boots a headless Firefox once to capture a live `Vacancies` GraphQL request from the public listings page, then replays that request via plain HTTP to paginate through the catalogue. The browser is never reopened during a scrape, so per-page cost stays close to a direct API call.

## Workflow
1. Open `https://www.kitalulus.com/lowongan` in headless Firefox (Playwright)
2. Click the "Lebih Banyak" load-more button to trigger the listings GraphQL XHR
3. Intercept the first response that returns `data.vacanciesV4` with no errors and capture the underlying request method, URL, headers, and body
4. Close the browser and reuse the captured template through the shared `requests.Session`, mutating only the page index
5. Parse each `vacanciesV4` page into the shared `Job` model and keep only listings within the 30-day recency window
6. Stop pagination when the API reports `hasNextPage: false`, when a page returns no recent listings, or when the configured page limit is reached

## Notes
- The captured payload contains the full GraphQL `query` text, so the scraper survives Apollo persisted-query hash rotations without manual recapture
- Pagination uses `variables.pagination.page` (1-indexed, limit 30); the legacy `variables.filter.page` shape is still supported as a fallback
- Listing cards now expose a relative `updatedAtStr` ("3 hari yang lalu") rather than an absolute timestamp; the parser converts the common Indonesian relative-time tokens (`menit`, `jam`, `hari`, `minggu`, `bulan`, `tahun`, `kemarin`, `baru saja`, `hari ini`) into UTC datetimes for the recency filter
- `--fetch-details` is currently a no-op because the listing payload covers every `Job` model field except `description`; bringing description back will require fetching individual detail pages
- Bootstrap raises `RuntimeError` if the load-more button is absent or no Vacancies response arrives within 45 seconds, so layout changes fail fast instead of producing empty snapshots
- Playwright Firefox is required (already shared with the Glints and Loker.id scrapers)

## Relevant code
- `src/scrapers/kitalulus.py`
- `src/scrapers/glints.py`
- `src/http_client.py`
- `src/utils.py`
- `src/models.py`
