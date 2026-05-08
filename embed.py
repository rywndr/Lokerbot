from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ClientError
from pgvector.psycopg2 import register_vector

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768
DEFAULT_BATCH_SIZE = 100
DEFAULT_MAX_RETRIES = 6
DEFAULT_RETRY_SECONDS = 30.0
MAX_INPUT_CHARS = 18000

UPSERT_SQL = """
insert into jobs (
    job_id, source, title, company, location, job_type,
    salary_range, url, description, tags, posted_at, scraped_at,
    embedding, updated_at
) values (
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s,
    %s, now()
)
on conflict (job_id) do update set
    source       = excluded.source,
    title        = excluded.title,
    company      = excluded.company,
    location     = excluded.location,
    job_type     = excluded.job_type,
    salary_range = excluded.salary_range,
    url          = excluded.url,
    description  = excluded.description,
    tags         = excluded.tags,
    posted_at    = excluded.posted_at,
    scraped_at   = excluded.scraped_at,
    embedding    = excluded.embedding,
    updated_at   = now();
"""


def build_document(job: dict) -> str:
    parts: list[str] = []
    if job.get("title"):
        parts.append(f"Title: {job['title']}")
    if job.get("company"):
        parts.append(f"Company: {job['company']}")
    if job.get("location"):
        parts.append(f"Location: {job['location']}")
    if job.get("job_type"):
        parts.append(f"Type: {job['job_type']}")
    if job.get("salary_range"):
        parts.append(f"Salary: {job['salary_range']}")
    tags = job.get("tags") or []
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    if job.get("description"):
        parts.append(f"\nDescription:\n{job['description']}")
    text = "\n".join(parts).strip()
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]
    return text or (job.get("title") or job.get("job_id") or "untitled job")


def chunked(items: Sequence, size: int) -> Iterable[Sequence]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _l2_normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0.0:
        return values
    return [v / norm for v in values]


def _retry_seconds_from_error(err: Exception) -> float:
    msg = str(err)
    m = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+(?:\.\d+)?)s", msg)
    if m:
        return float(m.group(1)) + 1.0
    m = re.search(r"retry in (\d+(?:\.\d+)?)\s*s", msg, flags=re.IGNORECASE)
    if m:
        return float(m.group(1)) + 1.0
    return DEFAULT_RETRY_SECONDS


def embed_batch(client: genai.Client, texts: list[str]) -> list[list[float]]:
    result = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=EMBEDDING_DIM,
        ),
    )
    return [_l2_normalize(list(emb.values)) for emb in result.embeddings]


def embed_batch_with_retry(
    client: genai.Client,
    texts: list[str],
    *,
    max_retries: int,
) -> list[list[float]]:
    for attempt in range(max_retries + 1):
        try:
            return embed_batch(client, texts)
        except ClientError as err:
            status = getattr(err, "code", None) or getattr(err, "status_code", None)
            if status == 429 and attempt < max_retries:
                wait = _retry_seconds_from_error(err)
                print(
                    f"  rate-limited (429); sleeping {wait:.1f}s before retry {attempt + 1}/{max_retries}...",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"embed_batch failed after {max_retries} retries")


def latest_all_snapshot(output_root: Path) -> Path | None:
    candidates = sorted((output_root / "all").glob("all_*.json"))
    return candidates[-1] if candidates else None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Embed a Lokerbot snapshot with Gemini text-embedding-004 and upsert into Supabase pgvector (for now).",
    )
    parser.add_argument(
        "snapshot",
        nargs="?",
        type=Path,
        help="Path to Lokerbot output JSON, defaults to the newest output/all/all_*.json.",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Number of jobs per Gemini batch request.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Embed only skip Supabase upsert.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Embed at most N jobs from the snapshot.",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.0,
        help="Seconds to sleep between successful batches (solution for free-tier RPM).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Maximum 429-retry attempts per batch (default: {DEFAULT_MAX_RETRIES}).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)

    snapshot = args.snapshot or latest_all_snapshot(Path("output"))
    if snapshot is None:
        print("error: no snapshot path given and no output/all/all_*.json found", file=sys.stderr)
        return 2
    if not snapshot.exists():
        print(f"error: snapshot not found: {snapshot}", file=sys.stderr)
        return 2

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("error: GOOGLE_API_KEY is not set", file=sys.stderr)
        return 2
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url and not args.dry_run:
        print("error: SUPABASE_DB_URL is not set (use --dry-run to skip upsert)", file=sys.stderr)
        return 2

    jobs = json.loads(snapshot.read_text(encoding="utf-8"))
    if args.limit is not None:
        jobs = jobs[: args.limit]
    if not jobs:
        print(f"warning: {snapshot} contained zero jobs nothing to embed", file=sys.stderr)
        return 0
    print(f"Loaded {len(jobs)} jobs from {snapshot}", file=sys.stderr)

    client = genai.Client(api_key=api_key)

    embeddings: list[list[float]] = []
    embed_start = time.perf_counter()
    total_batches = (len(jobs) + args.batch_size - 1) // args.batch_size
    for batch_idx, batch in enumerate(chunked(jobs, args.batch_size), start=1):
        texts = [build_document(j) for j in batch]
        start = time.perf_counter()
        batch_embeddings = embed_batch_with_retry(client, texts, max_retries=args.max_retries)
        elapsed = time.perf_counter() - start
        if len(batch_embeddings) != len(batch):
            print(
                f"error: Gemini returned {len(batch_embeddings)} embeddings for {len(batch)} jobs in batch {batch_idx}",
                file=sys.stderr,
            )
            return 1
        embeddings.extend(batch_embeddings)
        print(
            f"  batch {batch_idx}/{total_batches}: embedded {len(batch)} jobs in {elapsed:.1f}s ({len(embeddings)}/{len(jobs)})",
            file=sys.stderr,
        )
        if args.pause > 0 and batch_idx < total_batches:
            time.sleep(args.pause)
    print(
        f"Embedded {len(embeddings)} jobs in {time.perf_counter() - embed_start:.1f}s",
        file=sys.stderr,
    )

    if args.dry_run:
        print(f"Dry run: skipping Supabase upsert (vector dim={len(embeddings[0])})", file=sys.stderr)
        return 0

    upsert_start = time.perf_counter()
    conn = psycopg2.connect(db_url)
    register_vector(conn)
    inserted = 0
    try:
        with conn.cursor() as cur:
            for job, embedding in zip(jobs, embeddings):
                cur.execute(
                    UPSERT_SQL,
                    (
                        job["job_id"],
                        job.get("source") or "",
                        job.get("title"),
                        job.get("company"),
                        job.get("location"),
                        job.get("job_type"),
                        job.get("salary_range"),
                        job.get("url"),
                        job.get("description"),
                        job.get("tags") or [],
                        job.get("posted_at"),
                        job.get("scraped_at"),
                        embedding,
                    ),
                )
                inserted += 1
        conn.commit()
    finally:
        conn.close()

    print(
        f"Upserted {inserted} jobs into Supabase in {time.perf_counter() - upsert_start:.1f}s",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
