# Lokerbot

Lokerbot is a scraping engine for Indonesian job boards, mainly built for a hackathon project.

## Current status

Implemented:
- [Dealls](https://dealls.com/loker)
- [Glints](https://glints.com/id)
- [KitaLulus](https://www.kitalulus.com)

Planned:
- [Karirhub Kemnaker](https://karirhub.kemnaker.go.id)
- [Loker.id](https://www.loker.id/cari-lowongan-kerja)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install firefox
```

## Run

The CLI currently supports `dealls`, `glints`, and `kitalulus`, and still defaults to `dealls` for backward compatibility.

`--max-pages` defaults to `1`, so the backward-compatible behavior is still a single results page. Use `--all-pages` to let the selected scraper paginate through every available page it can reach. `--max-pages` and `--all-pages` are mutually exclusive

Use `--fetch-details` to enrich listings with source-owned detail data. When enabled, snapshots can include additional fields such as missing location, job type, salary, tags, and a plain-text `description`. Without `--fetch-details`, `description` stays `null`

Backward-compatible usage:

```bash
python main.py --max-pages 1
```

Explicit source selection:

```bash
python main.py --source dealls --max-pages 1
python main.py --source glints --max-pages 1
python main.py --source kitalulus --max-pages 1
```

More examples:

```bash
python main.py --source dealls --max-pages 3
python main.py --source dealls --all-pages
python main.py --source dealls --max-pages 1 --fetch-details
python main.py --source glints --max-pages 2
python main.py --source glints --max-pages 1 --fetch-details
python main.py --source glints --all-pages
python main.py --source glints --max-pages 1 --output-dir output
python main.py --source kitalulus --max-pages 3
python main.py --source kitalulus --all-pages
python main.py --source kitalulus --max-pages 1 --fetch-details
```

JSON snapshots are written under `output/<source>/`.

All scraper implementations (Dealls, Glints, and KitaLulus) only include listings whose `posted_at` falls between the scrape time and 30 days back, so older or future-dated jobs are excluded from the saved output.

## Output shape

Each saved record follows the shared `Job` model in `lokerbot/models.py` and includes:

- `job_id`
- `title`
- `company`
- `location`
- `job_type`
- `salary_range`
- `url`
- `description`
- `tags`
- `posted_at`
- `scraped_at`

`description` is stored as plain text, it is only populated when the `--fetch-details` flag is included

## How the Dealls scraper works

Workflow:
1. Fetch the Dealls listing page HTML
2. Read the embedded Next.js `__NEXT_DATA__` payload
3. Extract the initial Dealls jobs query and available pagination data from the dehydrated page data
4. Normalize each listing into the shared `Job` model and keep only jobs posted within the last 30 days
5. Optionally request additional detail data for recent listings to fill missing fields, including plain-text descriptions
6. Save the results as a JSON snapshot through `main.py`

## How the Glints scraper works

Workflow:
1. Launch Playwright and open the public listings page at `https://glints.com/id/lowongan-kerja`
2. Read the client-rendered Next.js `__NEXT_DATA__` payload and resolve Apollo cache references when Glints stores the listing data there
3. Extract public job URLs from the rendered DOM so each normalized record keeps the public Glints job link
4. Normalize each listing into the shared `Job` model and keep only jobs posted within the last 30 days
5. Optionally open job detail pages to fill in missing location, salary, job type, tags, and plain-text descriptions without failing the whole scrape if enrichment is unavailable
6. Stop pagination early with a warning if Glints starts rendering login-gated or otherwise unloadable follow-up pages

Current limitations:
- The Glints scraper depends on Playwright browser binaries being installed; `firefox` is the default browser because it has been more reliable than Chromium for this public page in this environment
- It only captures data that Glints exposes on the public listings and detail pages. If those client-rendered payloads or DOM hooks change, the scraper will need to be updated
- `--fetch-details` is best-effort enrichment, so missing fields from a blocked or changed detail page are left as is instead of aborting the full scrape
- `--all-pages` can still stop before the logical end of the listing set if Glints switches later pages to a login prompt or otherwise stops exposing public jobs

## How the KitaLulus scraper works

Workflow:
1. Fires GraphQL API requests to `https://gql.kitalulus.com/graphql` with `vacanciesV3` operation
2. Use persisted query hashes and Apollo CSRF protection headers to fetch job listings from the public API
3. Parse vacancy objects from the GraphQL response and normalize it into shared `Job` model
4. Filter jobs to only include those updated (refreshed) within the last 30 days (KitaLulus uses `updatedAt` to reflect when jobs were last bumped/refreshed, not when they were originally created)
5. Optionally enrich jobs with additional detail data (currently a placeholder as listing data is already comprehensive)
6. Support pagination via the GraphQL `page` parameter and `hasNextPage` indicator

Current limitations:
- The scraper depends on the KitaLulus GraphQL API structure staying stable. If the API schema, operation names, or persisted query hashes change, the scraper will need to be updated
- `--fetch-details` is currently a placeholder since the listing API already provides comprehensive job data including descriptions, tags, location, and salary information
- The scraper uses the default DKI Jakarta province filter

## Shared helpers

- `lokerbot/nextjs.py` for generic `__NEXT_DATA__` extraction
- `lokerbot/utils.py` for shared string normalization, description normalization, ISO datetime parsing, 30-day recency filtering, and stable list deduplication helpers
- `lokerbot/http_client.py` for the shared HTTP session
- `lokerbot/models.py` for the shared `Job` output model