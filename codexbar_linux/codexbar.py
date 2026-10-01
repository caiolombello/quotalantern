"""High-level usage fetcher with circuit breaker and last-known-good cache."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from .core.models import ProviderUsage
from .core.resilience import CircuitBreaker, CircuitBreakerOpen
from .providers import PROVIDERS, PROVIDER_SPECS

logger = logging.getLogger("codexbar-linux")


@dataclass
class CachedUsage:
    """Wraps a ProviderUsage with cache metadata."""

    usage: ProviderUsage
    fetched_at: datetime
    stale: bool = False


_CIRCUITS: dict[str, CircuitBreaker] = {
    name: CircuitBreaker(name=name, threshold=3, timeout=300.0) for name in PROVIDERS
}
_CACHE: dict[str, CachedUsage] = {}


def _ensure_circuit(name: str) -> CircuitBreaker:
    circuit = _CIRCUITS.get(name)
    if circuit is None:
        circuit = CircuitBreaker(name=name, threshold=3, timeout=300.0)
        _CIRCUITS[name] = circuit
    return circuit


def _enabled_names(enabled: Optional[Iterable[str]] = None) -> list[str]:
    if enabled is None:
        return [spec.id for spec in PROVIDER_SPECS if spec.default_enabled]
    known = set(PROVIDERS)
    return [name for name in enabled if name in known]


def _fetch_single(name: str) -> ProviderUsage:
    """Fetch one provider with circuit breaker and caching."""
    circuit = _ensure_circuit(name)
    fetcher = PROVIDERS[name]
    start = datetime.now().timestamp()

    try:
        results = circuit.call(fetcher)
    except CircuitBreakerOpen as exc:
        logger.warning("%s", exc)
        return _stale_or_error(name, f"Circuit open: {exc}")
    except RuntimeError as exc:
        elapsed = datetime.now().timestamp() - start
        logger.error("%s provider runtime error after %.1fs: %s", name, elapsed, exc)
        circuit.record_failure()
        return _stale_or_error(name, str(exc))
    except Exception as exc:
        elapsed = datetime.now().timestamp() - start
        logger.exception("%s provider unexpected error after %.1fs", name, elapsed)
        circuit.record_failure()
        return _stale_or_error(name, str(exc))

    if not results:
        circuit.record_failure()
        return _stale_or_error(name, "Empty response from provider")

    usage = results[0]
    if usage.error:
        # Permanent auth/config errors should not open the circuit — they need
        # a user action, not a cooldown. Network-ish failures still trip it.
        if _is_auth_or_config_error(usage.error):
            circuit.record_success()
            return usage
        circuit.record_failure()
        return _stale_or_error(name, usage.error)

    elapsed = datetime.now().timestamp() - start
    logger.debug("%s provider fetched successfully in %.1fs", name, elapsed)
    circuit.record_success()
    _CACHE[name] = CachedUsage(usage=usage, fetched_at=datetime.now(timezone.utc))
    return usage


def _is_auth_or_config_error(message: str) -> bool:
    lower = message.lower()
    needles = (
        "not authenticated",
        "missing",
        "expired",
        "unauthorized",
        "login",
        "cookie",
        "api key",
        "credentials",
        "session",
        "re-login",
        "chmod",
    )
    return any(n in lower for n in needles)


def _stale_or_error(name: str, error_msg: str) -> ProviderUsage:
    """Return cached data if available, otherwise an error ProviderUsage."""
    cached = _CACHE.get(name)
    if cached:
        return ProviderUsage(
            provider=name,
            source=cached.usage.source,
            account_email=cached.usage.account_email,
            login_method=cached.usage.login_method,
            primary=cached.usage.primary,
            secondary=cached.usage.secondary,
            tertiary=cached.usage.tertiary,
            credits_remaining=cached.usage.credits_remaining,
            balance_usd=cached.usage.balance_usd,
            updated_at=cached.fetched_at,
            error=f"{error_msg} (showing stale data)",
        )
    return ProviderUsage(provider=name, source="", error=error_msg)


def fetch_all(
    total_timeout: float = 45.0,
    enabled: Optional[Iterable[str]] = None,
) -> list[ProviderUsage]:
    """Fetch usage for enabled providers with concurrency and resilience."""
    names = _enabled_names(enabled)
    if not names:
        return []

    by_name: dict[str, ProviderUsage] = {}

    for name in names:
        cached = _CACHE.get(name)
        if cached:
            by_name[name] = cached.usage

    worker_count = min(len(names), 5)
    executor = ThreadPoolExecutor(max_workers=worker_count)
    try:
        futures = {executor.submit(_fetch_single, name): name for name in names}
        done, not_done = wait(futures, timeout=total_timeout)

        for future in not_done:
            name = futures[future]
            future.cancel()
            logger.warning("%s fetch exceeded global timeout (%.0fs)", name, total_timeout)
            by_name[name] = _stale_or_error(
                name, f"Fetch exceeded {total_timeout:.0f}s timeout"
            )

        for future in done:
            name = futures[future]
            try:
                by_name[name] = future.result()
            except Exception as exc:
                logger.exception("%s fetch failed fatally", name)
                by_name[name] = _stale_or_error(name, str(exc))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return [by_name[name] for name in names if name in by_name]


def fetch_provider(name: str) -> ProviderUsage:
    """Fetch a single provider with the same circuit breaker/cache path as fetch_all."""
    if name not in PROVIDERS:
        return ProviderUsage(provider=name, source="", error=f"Unknown provider: {name}")
    return _fetch_single(name)


def provider_status(name: str) -> dict:
    """Debug helper: return circuit + cache status for a provider."""
    circuit = _CIRCUITS.get(name)
    cached = _CACHE.get(name)
    return {
        "circuit": circuit.state if circuit else "unknown",
        "cached": cached is not None,
        "cached_at": cached.fetched_at.isoformat() if cached else None,
    }
