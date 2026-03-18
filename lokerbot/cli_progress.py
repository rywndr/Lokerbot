from __future__ import annotations

import sys
import threading
import time
from typing import Any

_SPINNER_FRAMES = "|/-\\"


class _ProgressReporter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._message = ""

    def __call__(self, message: str) -> None:
        with self._lock:
            self._message = message

    def snapshot(self) -> str:
        with self._lock:
            return self._message


def _format_scrape_mode(max_pages: int | None, fetch_details: bool, delay: float) -> str:
    pages_text = "all available pages" if max_pages is None else f"up to {max_pages} page{'s' if max_pages != 1 else ''}"
    detail_text = "detail enrichment on" if fetch_details else "detail enrichment off"
    delay_text = f"{delay:.2f}s delay" if delay else "no delay"
    return f"{pages_text}, {detail_text}, {delay_text}"


def _format_progress_suffix(message: str) -> str:
    return f" • {message}" if message else ""


def _animate_loader(stream, source: str, stop_event: threading.Event, start: float, reporter: _ProgressReporter) -> None:
    if stream.isatty():
        frame_index = 0
        while not stop_event.wait(0.12):
            elapsed = time.perf_counter() - start
            frame = _SPINNER_FRAMES[frame_index % len(_SPINNER_FRAMES)]
            message = reporter.snapshot()
            print(
                f"\r[{source}] {frame} scraping... {elapsed:.1f}s elapsed{_format_progress_suffix(message)}",
                end="",
                file=stream,
                flush=True,
            )
            frame_index += 1
        return

    last_message = None
    last_print_at = start
    while not stop_event.wait(0.25):
        message = reporter.snapshot()
        now = time.perf_counter()
        if message != last_message or now - last_print_at >= 5.0:
            print(
                f"[{source}] still scraping... {now - start:.1f}s elapsed{_format_progress_suffix(message)}",
                file=stream,
                flush=True,
            )
            last_message = message
            last_print_at = now


def run_scraper_with_progress(
    source: str,
    scraper: Any,
    *,
    max_pages: int | None,
    fetch_details: bool,
    delay: float,
):
    stream = sys.stderr
    is_tty = stream.isatty()
    start = time.perf_counter()
    reporter = _ProgressReporter()
    print(f"[{source}] starting scrape ({_format_scrape_mode(max_pages, fetch_details, delay)})", file=stream, flush=True)

    stop_event = threading.Event()
    loader_thread = threading.Thread(
        target=_animate_loader,
        args=(stream, source, stop_event, start, reporter),
        daemon=True,
    )
    loader_thread.start()

    try:
        return scraper(max_pages=max_pages, fetch_details=fetch_details, delay=delay, progress=reporter)
    finally:
        stop_event.set()
        loader_thread.join()
        if is_tty:
            print("\r" + " " * 80 + "\r", end="", file=stream, flush=True)
        elapsed = time.perf_counter() - start
        print(
            f"[{source}] finished scrape in {elapsed:.1f}s{_format_progress_suffix(reporter.snapshot())}",
            file=stream,
            flush=True,
        )
