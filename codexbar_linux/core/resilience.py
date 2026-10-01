"""Resilience utilities for network calls: retry with exponential backoff and circuit breaker."""

import functools
import time
import urllib.error
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_tries: int = 3,
        backoff: float = 1.0,
        exceptions: tuple[type[BaseException], ...] = (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            RuntimeError,
        ),
    ):
        self.max_tries = max(max_tries, 1)
        self.backoff = max(backoff, 0.0)
        self.exceptions = exceptions


def retry(config: Optional[RetryConfig] = None) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator that retries a function with exponential backoff.

    Example::
        @retry(RetryConfig(max_tries=3, backoff=1.0))
        def fetch_data() -> dict:
            ...
    """
    cfg = config or RetryConfig()

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            for attempt in range(cfg.max_tries):
                try:
                    return func(*args, **kwargs)
                except cfg.exceptions as exc:
                    if attempt == cfg.max_tries - 1:
                        raise
                    delay = cfg.backoff * (2 ** attempt)
                    time.sleep(delay)

        return wrapper

    return decorator


class CircuitBreaker:
    """Simple circuit breaker for unstable external APIs.

    After *threshold* consecutive failures, the breaker opens and rejects
    calls for *timeout* seconds. A single success closes the circuit again.
    """

    def __init__(self, name: str, threshold: int = 3, timeout: float = 600.0):
        self.name = name
        self.threshold = max(threshold, 1)
        self.timeout = max(timeout, 0.0)
        self._failures = 0
        self._last_failure = 0.0
        self._open = False

    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Execute *func* if the circuit is closed, otherwise raise immediately."""
        if self._open:
            elapsed = time.monotonic() - self._last_failure
            if elapsed < self.timeout:
                raise CircuitBreakerOpen(self.name, self.timeout - elapsed)
            # Half-open: allow one probe through
            self._open = False
            self._failures = 0

        try:
            result = func(*args, **kwargs)
        except Exception:
            self._failures += 1
            self._last_failure = time.monotonic()
            if self._failures >= self.threshold:
                self._open = True
            raise
        else:
            # Success resets the counter
            if self._failures > 0:
                self._failures = 0
            return result

    def record_success(self) -> None:
        """Manually record a success (used when the call is outside *call*)."""
        if self._failures > 0:
            self._failures = 0
        if self._open:
            self._open = False

    def record_failure(self) -> None:
        """Manually record a failure (used when the call is outside *call*)."""
        self._failures += 1
        self._last_failure = time.monotonic()
        if self._failures >= self.threshold:
            self._open = True

    @property
    def is_open(self) -> bool:
        if self._open:
            elapsed = time.monotonic() - self._last_failure
            if elapsed >= self.timeout:
                self._open = False
                self._failures = 0
        return self._open

    @property
    def state(self) -> str:
        if self.is_open:
            return "open"
        return "closed"


class CircuitBreakerOpen(Exception):
    """Raised when a call is attempted while the circuit breaker is open."""

    def __init__(self, name: str, retry_after: float):
        self.name = name
        self.retry_after = retry_after
        super().__init__(f"Circuit breaker '{name}' is open. Retry after {retry_after:.0f}s")
