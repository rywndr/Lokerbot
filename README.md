# Lokerbot

Lokerbot is a scraping engine for scraping job listings from Indonesian job boards, it is mainly designed for a hackathon project

## Current status

Implemented:
- [Dealls](https://dealls.com/loker)
- [Glints](https://glints.com/id)

Planned:

Planned:
- [KitaLulus](https://www.kitalulus.com)
- [Karirhub Kemnaker](https://karirhub.kemnaker.go.id)
- [Loker.id](https://www.loker.id/cari-lowongan-kerja)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install firefox
python -m playwright install firefox
```

## Run

The CLI currently supports `dealls` and `glints`, and still defaults to `dealls` for backward compatibility.
The CLI currently supports `dealls` and `glints`, and still defaults to `dealls` for backward compatibility.

`--max-pages` defaults to `1` so the backward-compatible behavior is still a single results page. Use `--all-pages` to let the selected scraper paginate through every available page it can reach. `--max-pages` and `--all-pages` are mutually exclusive.
`--max-pages` defaults to `1` so the backward-compatible behavior is still a single results page. Use `--all-pages` to let the selected scraper paginate through every available page it can reach. `--max-pages` and `--all-pages` are mutually exclusive.

Backward-compatible usage:

```bash
python main.py --max-pages 1
```

Explicit source selection:

```bash
python main.py --source dealls --max-pages 1
python main.py --source glints --max-pages 1
python main.py --source glints --max-pages 1
```

Options:

```bash
python main.py --source dealls --max-pages 3
python main.py --source dealls --all-pages
python main.py --source dealls --max-pages 1 --fetch-details
python main.py --source glints --max-pages 2
python main.py --source glints --max-pages 1 --fetch-details
python main.py --source glints --all-pages
python main.py --source glints --max-pages 1 --output-dir output
python main.py --source glints --max-pages 2
python main.py --source glints --max-pages 1 --fetch-details
python main.py --source glints --all-pages
python main.py --source glints --max-pages 1 --output-dir output
```

JSON snapshots are written under `output/<source>/`.

Dealls and Glints snapshots only include listings whose `posted_at` falls between the scrape time and 30 days back, so older or future-dated jobs are excluded from the saved output.
Dealls and Glints snapshots only include listings whose `posted_at` falls between the scrape time and 30 days back, so older or future-dated jobs are excluded from the saved output.

## How the Dealls scraper works

Workflow:
1. Fetch the Dealls listing page HTML
2. Read the embedded Next.js `__NEXT_DATA__` payload
3. Extract the initial Dealls jobs query and available pagination data from the dehydrated page data
4. Normalize each listing into the shared `Job` model and keep only jobs posted within the last 30 days
5. Optionally request additional detail data for recent listings that are missing key fields
6. Save the results as a JSON snapshot through `main.py`

## How the Glints scraper works

Workflow:
1. Launch Playwright and open the public listings page at `https://glints.com/id/lowongan-kerja`
2. Read the client-rendered Next.js `__NEXT_DATA__` payload and resolve Apollo cache references when Glints stores the listing data there
3. Extract public job URLs from the rendered DOM so each normalized record keeps the public Glints job link
4. Normalize each listing into the shared `Job` model and keep only jobs posted within the last 30 days
5. Optionally open job detail pages to fill in missing location, salary, job type, or tags without failing the whole scrape if enrichment is unavailable
6. Stop pagination early with a warning if Glints starts rendering login-gated or otherwise unloadable follow-up pages

Current limitations:
- The Glints scraper depends on Playwright browser binaries being installed; `firefox` is the default browser because it has been more reliable than Chromium for this public page in this environment
- It only captures data that Glints exposes on the public listings and detail pages, if those client-rendered payloads or DOM hooks change, the scraper will need to be updated
- `fetch_details` is best-effort enrichment, so missing fields from a blocked or changed detail page are left as is instead of aborting the full scrape
- `--all-pages` can still stop before the logical end of the listing set if Glints switches later pages to a login prompt or otherwise stops exposing public jobs

Shared helpers:
- `lokerbot/nextjs.py` for generic `__NEXT_DATA__` extraction
- `lokerbot/utils.py` for generic string cleanup helpers
- `lokerbot/http_client.py` for the shared HTTP session
- `lokerbot/models.py` for the shared `Job` output model