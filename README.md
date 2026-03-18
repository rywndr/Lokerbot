# Lokerbot

Lokerbot is a scraping engine for Indonesian job boards, mainly built for a hackathon project.

## Supported sources

| Source | Status | Docs |
| --- | --- | --- |
| Dealls | Implemented | [dealls](./_docs/dealls.md) |
| Glints | Implemented | [glints](./_docs/glints.md) |
| KitaLulus | Implemented | [kitalulus](./_docs/kitalulus.md) |
| Karirhub Kemnaker | Implemented | [karirhub](./_docs/karirhub.md) |
| Loker.id | Implemented | [lokerid](./_docs/lokerid.md) |

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install firefox
```

## Run

The CLI supports `dealls`, `glints`, `kitalulus`, `karirhub`, and `lokerid`, and defaults to `dealls` for backward compatibility.

`--max-pages` defaults to `1`, so the backward-compatible behavior is still a single results page. Use `--all-pages` to let the selected scraper paginate through every available page it can reach. `--max-pages` and `--all-pages` are mutually exclusive.

Use `--fetch-details` to enrich listings with source-owned detail data. When enabled, snapshots can include missing location, job type, salary, tags, and a plain-text `description`. Without `--fetch-details`, `description` stays `null`.

`lokerid` is a browser-driven scraper. It reads the public listings page, prefers the embedded Remix loader payload, falls back to rendered job cards when needed, and opens detail pages only when the listing data is incomplete.

### Examples

```bash
python main.py --max-pages 1
python main.py --source dealls --max-pages 3
python main.py --source glints --all-pages
python main.py --source kitalulus --max-pages 1 --fetch-details
python main.py --source karirhub --max-pages 3
python main.py --source karirhub --all-pages
python main.py --source lokerid --max-pages 1
```

## Output

JSON snapshots are written under `output/<source>/`.

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

`description` is stored as plain text and is only populated when `--fetch-details` is included.

All scraper implementations only include listings whose `posted_at` falls between the scrape time and 30 days back, so older or future-dated jobs are excluded from the saved output.

## Source docs

- [Dealls](./_docs/dealls.md)
- [Glints](./_docs/glints.md)
- [KitaLulus](./_docs/kitalulus.md)
- [Karirhub](./_docs/karirhub.md)
- [Loker.id](./_docs/lokerid.md)

## Shared helpers

- `lokerbot/nextjs.py` for generic `__NEXT_DATA__` extraction
- `lokerbot/utils.py` for shared string normalization, description normalization, ISO datetime parsing, 30-day recency filtering, and stable list deduplication helpers
- `lokerbot/http_client.py` for the shared HTTP session
- `lokerbot/models.py` for the shared `Job` output model