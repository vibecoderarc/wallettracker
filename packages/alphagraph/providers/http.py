"""Shared HTTP client for live providers.

Every outbound call is retried, rate-limited, and metered. The metering is not
bookkeeping for its own sake: free tiers are the plan, and knowing the request
count is what turns "are we about to exceed the allowance" into a number rather
than a surprise invoice.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass
class UsageMeter:
    """Counts requests so free-tier allowances stay visible."""

    provider: str
    requests: int = 0
    retries: int = 0
    rate_limited: int = 0
    errors: int = 0
    bytes_received: int = 0

    def as_dict(self) -> dict[str, int | str]:
        return {
            "provider": self.provider,
            "requests": self.requests,
            "retries": self.retries,
            "rate_limited": self.rate_limited,
            "errors": self.errors,
            "bytes_received": self.bytes_received,
        }


@dataclass
class RateLimiter:
    """Simple requests-per-second cap.

    Free tiers publish a per-second limit and enforce it with 429s. Pacing
    ourselves is cheaper than retrying after being refused, and it keeps a long
    backfill from burning the allowance on rejected calls.
    """

    per_second: float
    _last: float = field(default=0.0, repr=False)

    async def wait(self) -> None:
        if self.per_second <= 0:
            return
        interval = 1.0 / self.per_second
        elapsed = time.monotonic() - self._last
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
        self._last = time.monotonic()


class HttpClient:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str = "",
        requests_per_second: float = 8.0,
        timeout: float = 30.0,
        max_retries: int = 4,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.meter = UsageMeter(provider=provider)
        self.limiter = RateLimiter(per_second=requests_per_second)
        self.max_retries = max_retries
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET returning parsed JSON, with backoff on transient failures.

        Raises on non-retryable errors rather than returning a partial result:
        a silently truncated backfill would look like "this wallet did nothing"
        and quietly corrupt every metric derived from it.
        """
        url = f"{self._base_url}{path}" if self._base_url else path
        delay = 1.0

        for attempt in range(self.max_retries + 1):
            await self.limiter.wait()
            self.meter.requests += 1
            try:
                response = await self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                self.meter.errors += 1
                if attempt >= self.max_retries:
                    raise ProviderRequestError(f"{self.meter.provider}: {exc}") from exc
                await self._backoff(delay)
                delay *= 2
                continue

            if response.status_code in RETRY_STATUS:
                if response.status_code == 429:
                    self.meter.rate_limited += 1
                if attempt >= self.max_retries:
                    raise ProviderRequestError(
                        f"{self.meter.provider}: {response.status_code} after "
                        f"{attempt + 1} attempts"
                    )
                self.meter.retries += 1
                # Honour Retry-After when the server sends one; guessing shorter
                # than instructed is how an allowance gets burned on refusals.
                await self._backoff(self._retry_after(response) or delay)
                delay *= 2
                continue

            if response.status_code >= 400:
                self.meter.errors += 1
                raise ProviderRequestError(
                    f"{self.meter.provider}: {response.status_code} {response.text[:200]}"
                )

            self.meter.bytes_received += len(response.content)
            return response.json()

        raise ProviderRequestError(f"{self.meter.provider}: exhausted retries")

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("retry-after")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    @staticmethod
    async def _backoff(seconds: float) -> None:
        await asyncio.sleep(min(seconds, 30.0))


class ProviderRequestError(RuntimeError):
    """A provider call failed in a way the caller must not treat as empty data."""
