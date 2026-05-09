from __future__ import annotations

import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_TIMEOUT = 30

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
}


class TimeoutSession(requests.Session):
    def __init__(self, timeout: int = DEFAULT_TIMEOUT) -> None:
        super().__init__()
        self.default_timeout = timeout

    def request(self, method: str, url: str, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", self.default_timeout)
        return super().request(method, url, **kwargs)


def build_session(
    *,
    timeout: int = DEFAULT_TIMEOUT,
    total_retries: int = 3,
    backoff_factor: float = 0.5,
) -> requests.Session:
    session = TimeoutSession(timeout=timeout)
    session.headers.update(DEFAULT_HEADERS)

    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        status=total_retries,
        allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
        status_forcelist=(429, 500, 502, 503, 504),
        backoff_factor=backoff_factor,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class SessionPool:
    """Thread-safe pool of requests.Session objects with optimized connection pooling."""

    def __init__(self, size: int = 10):
        self._size = size
        self._pool: list[requests.Session] = []
        self._lock = threading.Lock()
        self._local = threading.local()

        for _ in range(size):
            self._pool.append(self._create_optimized_session())

    def _create_optimized_session(self) -> requests.Session:
        session = build_session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=20,
            pool_maxsize=20,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def acquire(self) -> requests.Session:
        if hasattr(self._local, "session") and self._local.session is not None:
            return self._local.session

        with self._lock:
            if self._pool:
                session = self._pool.pop()
                self._local.session = session
                return session

        session = self._create_optimized_session()
        self._local.session = session
        return session

    def release(self, session: requests.Session) -> None:
        if hasattr(self._local, "session") and self._local.session is session:
            self._local.session = None
        with self._lock:
            if len(self._pool) < self._size:
                self._pool.append(session)
            else:
                session.close()

    def close_all(self) -> None:
        with self._lock:
            for session in self._pool:
                session.close()
            self._pool.clear()
