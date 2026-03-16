# Lokerbot

Lokerbot is a scraping engine for scraping job listings from Indonesian job boards, it is mainly designed for a hackathon project

## Current status

Implemented:
- [Dealls](https://dealls.com/loker)

Planned:
- [Glints](https://glints.com/id)
- [KitaLulus](https://www.kitalulus.com)
- [Karirhub Kemnaker](https://karirhub.kemnaker.go.id)
- [Loker.id](https://www.loker.id/cari-lowongan-kerja)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

Scrape one page from Dealls:

```bash
python main.py --max-pages 1
```

Options:

```bash
python main.py --max-pages 3
python main.py --max-pages 1 --fetch-details
python main.py --max-pages 1 --output-dir output
```

JSON snapshots are written to `output/`

## How the Dealls scraper works

Workflow:
1. Fetch the Dealls listing page HTML
2. Read the embedded Next.js `__NEXT_DATA__` payload
3. Extract the initial Dealls jobs query from the dehydrated page data
4. Normalize each listing into the shared `Job` model
5. Optionally request additional detail data for listings that are missing key fields
6. Save the results as a JSON snapshot through `main.py`

Shared helpers:
- `lokerbot/nextjs.py` for generic `__NEXT_DATA__` extraction
- `lokerbot/utils.py` for generic string cleanup helpers
- `lokerbot/http_client.py` for the shared HTTP session
- `lokerbot/models.py` for the shared `Job` output model
