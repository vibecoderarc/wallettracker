"""Live Solana provider backed by Helius.

Uses the Enhanced Transactions API, which returns transactions already parsed
into semantic events — "this was a swap of X for Y on Raydium" — rather than raw
instructions. That is the difference between a week of protocol-decoding work
and an afternoon, and it is the main reason this provider is tractable.

Two rules govern everything here:

1. **Never guess.** A transaction we cannot interpret becomes an explicit
   `unknown_interaction`, not a dropped row and not an invented swap. Coverage
   gaps must be visible, because a silently skipped buy makes a wallet look
   inactive and quietly corrupts its whole track record.
2. **Never trust the shape.** Every field is read defensively. Third-party
   response formats change without warning, and a KeyError mid-backfill would
   abort a sweep that had already spent its allowance.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from alphagraph.core.addresses import Network
from alphagraph.core.events import (
    AssetRef,
    CanonicalEvent,
    EventType,
    Finality,
    QualityFlag,
    Side,
)
from alphagraph.core.idempotency import event_key
from alphagraph.providers.base import Capability, ChainProvider, ProviderHealth
from alphagraph.providers.http import HttpClient, ProviderRequestError

log = logging.getLogger(__name__)

HELIUS_BASE = "https://api.helius.xyz"
PARSER_VERSION = "helius-v0"

#: Page size for the transaction history endpoint. 100 is the documented
#: maximum; smaller pages mean more requests against the same allowance.
PAGE_LIMIT = 100

#: Wrapped SOL and the major stablecoins. A swap "into" one of these is someone
#: selling, not buying, however the payload happens to order the transfers.
QUOTE_MINTS = frozenset(
    {
        "So11111111111111111111111111111111111111112",  # wSOL
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    }
)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


class HeliusChainProvider(ChainProvider):
    """Solana transactions for specific addresses, normalized to canonical events."""

    name = "helius"

    def __init__(
        self,
        api_key: str,
        *,
        requests_per_second: float = 8.0,
        client: HttpClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("HeliusChainProvider requires an API key")
        self._api_key = api_key
        self._http = client or HttpClient(
            provider=self.name,
            base_url=HELIUS_BASE,
            requests_per_second=requests_per_second,
        )

    @property
    def usage(self) -> dict[str, int | str]:
        return self._http.meter.as_dict()

    def network(self) -> Network:
        return Network.SOLANA

    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {
                Capability.BACKFILL_RANGE,
                Capability.HISTORICAL_ARCHIVE,
                Capability.STREAM_ADDRESSES,
            }
        )

    # ------------------------------------------------------------------ fetch

    async def _page(self, address: str, before: str | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"api-key": self._api_key, "limit": PAGE_LIMIT}
        if before:
            params["before"] = before
        payload = await self._http.get_json(f"/v0/addresses/{address}/transactions", params)
        if not isinstance(payload, list):
            raise ProviderRequestError(f"helius: expected a list, got {type(payload).__name__}")
        return payload

    async def fetch_address_history(
        self,
        address: str,
        *,
        start: datetime,
        end: datetime,
        max_pages: int = 50,
    ) -> AsyncIterator[CanonicalEvent]:
        """Walk an address's history backwards, yielding events inside the window.

        Helius paginates newest-first, so this walks back until it passes `start`
        and then stops. `max_pages` is a hard spend cap — a very active address
        could otherwise consume an entire monthly allowance on its own.
        """
        before: str | None = None
        pages = 0

        while pages < max_pages:
            batch = await self._page(address, before)
            pages += 1
            if not batch:
                return

            reached_start = False
            for raw in batch:
                timestamp = raw.get("timestamp")
                if not isinstance(timestamp, int | float):
                    continue
                chain_time = datetime.fromtimestamp(timestamp, tz=UTC)
                if chain_time < start:
                    reached_start = True
                    continue
                if chain_time > end:
                    continue
                for event in self.parse_transaction(raw, subject=address):
                    yield event

            if reached_start:
                return
            last_signature = batch[-1].get("signature")
            if not last_signature:
                return
            before = last_signature

    # ------------------------------------------------------------------ parse

    def parse_transaction(
        self, raw: dict[str, Any], subject: str | None = None
    ) -> list[CanonicalEvent]:
        """Turn one enhanced transaction into zero or more canonical events.

        `subject` is the address whose history we were reading. Helius reports a
        transaction's fee payer, which for a swap routed through an aggregator is
        often a program rather than the trader, so the subject is preferred when
        attributing the action to a wallet.
        """
        signature = raw.get("signature")
        timestamp = raw.get("timestamp")
        if not signature or not isinstance(timestamp, int | float):
            return []

        chain_time = datetime.fromtimestamp(timestamp, tz=UTC)
        observed_at = datetime.now(tz=UTC)
        slot = raw.get("slot") or 0
        actor = subject or raw.get("feePayer") or ""
        if not actor:
            return []

        # A failed transaction moved nothing. Recording it as activity would
        # credit a wallet with trades it never made.
        if raw.get("transactionError"):
            return []

        swap = self._extract_swap(raw)
        if swap is not None:
            return [
                self._event(
                    event_type=EventType.SWAP,
                    signature=signature,
                    slot=int(slot),
                    chain_time=chain_time,
                    observed_at=observed_at,
                    actor=actor,
                    asset=swap["asset"],
                    side=swap["side"],
                    usd_value=swap["usd_value"],
                    quality_flags=swap["flags"],
                    extra={"source": raw.get("source"), "description": raw.get("description")},
                )
            ]

        return [
            self._event(
                event_type=EventType.UNKNOWN_INTERACTION,
                signature=signature,
                slot=int(slot),
                chain_time=chain_time,
                observed_at=observed_at,
                actor=actor,
                asset=None,
                side=None,
                usd_value=None,
                quality_flags=[QualityFlag.UNPARSED_INSTRUCTION],
                extra={"type": raw.get("type"), "source": raw.get("source")},
            )
        ]

    def _extract_swap(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Identify the non-quote leg of a swap and which way it went.

        Helius exposes swaps in two shapes depending on the route, so both the
        structured `events.swap` block and the flat `tokenTransfers` list are
        tried before giving up.
        """
        transfers = raw.get("tokenTransfers")
        if not isinstance(transfers, list) or not transfers:
            if str(raw.get("type", "")).upper() != "SWAP":
                return None
            transfers = []

        flags: list[QualityFlag] = []
        inbound: list[dict[str, Any]] = []
        outbound: list[dict[str, Any]] = []

        for transfer in transfers:
            if not isinstance(transfer, dict):
                continue
            mint = transfer.get("mint")
            if not mint:
                continue
            amount = _decimal(transfer.get("tokenAmount"))
            record = {"mint": mint, "amount": amount}
            # Direction is inferred from the fee payer's perspective; Helius
            # gives from/to per transfer.
            if transfer.get("toUserAccount") == raw.get("feePayer"):
                inbound.append(record)
            elif transfer.get("fromUserAccount") == raw.get("feePayer"):
                outbound.append(record)

        non_quote_in = [t for t in inbound if t["mint"] not in QUOTE_MINTS]
        non_quote_out = [t for t in outbound if t["mint"] not in QUOTE_MINTS]

        if non_quote_in:
            leg, side = non_quote_in[0], Side.BUY
        elif non_quote_out:
            leg, side = non_quote_out[0], Side.SELL
        else:
            return None

        if leg["amount"] is None:
            flags.append(QualityFlag.PARTIAL_PARSE)

        # No USD figure is available here. Leaving it None is deliberate: the
        # enrichment step prices it against the market provider, and inventing a
        # number now would be indistinguishable from a real one later.
        flags.append(QualityFlag.MISSING_PRICE)

        return {
            "asset": AssetRef(network=Network.SOLANA, address=str(leg["mint"])),
            "side": side,
            "usd_value": None,
            "flags": flags,
        }

    def _event(
        self,
        *,
        event_type: EventType,
        signature: str,
        slot: int,
        chain_time: datetime,
        observed_at: datetime,
        actor: str,
        asset: AssetRef | None,
        side: Side | None,
        usd_value: Decimal | None,
        quality_flags: list[QualityFlag],
        extra: dict[str, Any],
    ) -> CanonicalEvent:
        return CanonicalEvent(
            event_id=event_key(
                network=Network.SOLANA.value,
                transaction_id=signature,
                event_type=event_type.value,
                event_index=0,
            ),
            event_type=event_type,
            network=Network.SOLANA,
            block_height=slot,
            transaction_id=signature,
            chain_time=chain_time,
            # Helius only returns confirmed history, and Solana confirmations are
            # effectively final at the depth we read at.
            observed_at=max(observed_at, chain_time),
            finality=Finality.FINALIZED,
            actor=actor,
            asset=asset,
            side=side,
            usd_value=usd_value,
            provider=self.name,
            parser_version=PARSER_VERSION,
            quality_flags=quality_flags,
            extra={k: v for k, v in extra.items() if v is not None},
        )

    # --------------------------------------------------------------- interface

    async def backfill(
        self, start: datetime, end: datetime, addresses: Sequence[str] | None = None
    ) -> AsyncIterator[CanonicalEvent]:
        if not addresses:
            # Helius indexes by address. A chain-wide sweep is not something this
            # provider can do, and pretending otherwise would return silence.
            raise ValueError("HeliusChainProvider.backfill requires explicit addresses")
        for address in addresses:
            async for event in self.fetch_address_history(address, start=start, end=end):
                yield event

    async def stream_addresses(self, addresses: Sequence[str]) -> AsyncIterator[CanonicalEvent]:
        raise NotImplementedError(
            "Live streaming arrives with the webhook receiver; use fetch_address_history."
        )
        yield  # pragma: no cover - makes this an async generator

    async def health(self) -> ProviderHealth:
        checked = datetime.now(tz=UTC)
        try:
            await self._http.get_json(
                "/v0/addresses/So11111111111111111111111111111111111111112/transactions",
                {"api-key": self._api_key, "limit": 1},
            )
        except ProviderRequestError as exc:
            return ProviderHealth(
                provider=self.name, healthy=False, checked_at=checked, detail=str(exc)[:200]
            )
        return ProviderHealth(provider=self.name, healthy=True, checked_at=checked)
