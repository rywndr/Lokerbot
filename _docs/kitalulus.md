# KitaLulus scraper

## Overview
KitaLulus is a GraphQL-driven scraper that calls the public `vacanciesV3` operation, normalizes the results, and keeps them within the shared `Job` model

## Workflow
1. Send persisted-query GraphQL requests to the public KitaLulus endpoint
2. Use the stored query hash and request headers required by the API
3. Parse the `vacanciesV3` response payload
4. Normalize each vacancy and keep only jobs updated within the last 30 days
5. Paginate with the GraphQL `page` parameter and stop when `hasNextPage` is false
6. Save the results through `main.py`

## Notes
- The scraper uses the default DKI Jakarta province filter
- `--fetch-details` is currently a placeholder because the listing API already exposes comprehensive job data
- If the GraphQL schema, operation name, or persisted query hash changes, the scraper will need to be updated

## Relevant code
- `lokerbot/scrapers/kitalulus.py`
- `lokerbot/utils.py`
- `lokerbot/http_client.py`
- `lokerbot/models.py`
