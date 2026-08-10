"""Deterministic synthetic ecosystem for development and testing.

This is not a toy. Discovery is a statistical claim, and a statistical claim can
only be verified against ground truth. The world below plants specific wallet
archetypes with known properties, then buries them in noise, so tests can assert
that the engine finds the planted insiders and rejects the planted decoys.

Ground truth is exported as `WORLD_TRUTH` and asserted against in
`tests/test_discovery.py`. If discovery stops finding these, something broke.

Design notes worth preserving:
  * `insider_listing` is deliberately UNPROFITABLE. It predicts listings with a
    high hit rate but round-trips its positions and gets liquidated. Any ranking
    that scores on P&L will miss it — that is the whole point of spec §0.
  * `sniper_bot` has a superb hit rate and must still be excluded, because
    entering in the first seconds of every launch is not information.
  * `lucky_wallet` gets 3-for-3 by chance and must NOT survive the sample-size
    and independence guards.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from alphagraph.core.addresses import Network
from alphagraph.core.events import (
    AssetRef,
    CanonicalEvent,
    EventType,
    Finality,
    Side,
)
from alphagraph.core.idempotency import event_key
from alphagraph.core.outcomes import Outcome, OutcomeClass
from alphagraph.core.timeutil import days, hours

SEED = 20260810
WORLD_START = datetime(2025, 6, 1, tzinfo=__import__("datetime").UTC)
WORLD_END = datetime(2026, 8, 1, tzinfo=__import__("datetime").UTC)

# Base58-safe synthetic Solana-style addresses (no 0, O, I, l).
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _addr(rng: random.Random, prefix: str) -> str:
    body = "".join(rng.choice(_B58) for _ in range(40 - len(prefix)))
    return f"{prefix}{body}"


@dataclass
class WalletSpec:
    """A planted wallet archetype with its expected classification."""

    handle: str
    address: str
    expected_archetype: str
    should_be_discovered: bool
    note: str


@dataclass
class World:
    assets: list[AssetRef] = field(default_factory=list)
    events: list[CanonicalEvent] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)
    wallets: dict[str, WalletSpec] = field(default_factory=dict)
    candles: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    created_at: dict[str, datetime] = field(default_factory=dict)

    def wallet(self, handle: str) -> str:
        return self.wallets[handle].address


class _Builder:
    def __init__(self) -> None:
        self.rng = random.Random(SEED)
        self.world = World()
        self._seq = 0

    # ---------------------------------------------------------------- helpers

    def _tx(self, tag: str) -> str:
        self._seq += 1
        return f"tx{self._seq:07d}{tag[:8]}"

    def _emit(
        self,
        *,
        event_type: EventType,
        actor: str,
        asset: AssetRef | None,
        chain_time: datetime,
        side: Side | None = None,
        usd_value: Decimal | None = None,
        price_usd: Decimal | None = None,
        filled: bool = True,
        limit_price_usd: Decimal | None = None,
        leverage: Decimal | None = None,
        observation_lag: timedelta = timedelta(seconds=8),
        network: Network = Network.SOLANA,
    ) -> CanonicalEvent:
        tx = self._tx(actor)
        event = CanonicalEvent(
            event_id=event_key(
                network=network.value,
                transaction_id=tx,
                event_type=event_type.value,
                event_index=0,
            ),
            event_type=event_type,
            network=network,
            block_height=int((chain_time - WORLD_START).total_seconds() // 2),
            transaction_id=tx,
            chain_time=chain_time,
            observed_at=chain_time + observation_lag,
            finality=Finality.FINALIZED,
            actor=actor,
            asset=asset,
            side=side,
            usd_value=usd_value,
            price_usd=price_usd,
            filled=filled,
            limit_price_usd=limit_price_usd,
            leverage=leverage,
            provider="fixture",
        )
        self.world.events.append(event)
        return event

    def _asset(self, idx: int, symbol: str) -> AssetRef:
        asset = AssetRef(
            network=Network.SOLANA,
            address=_addr(self.rng, f"As{idx:02d}"),
            symbol=symbol,
            decimals=6,
        )
        self.world.assets.append(asset)
        return asset

    def _outcome(
        self,
        asset: AssetRef,
        cls: OutcomeClass,
        t0: datetime,
        magnitude: Decimal | None = None,
        venue: str | None = None,
        observation_lag: timedelta = timedelta(minutes=3),
    ) -> Outcome:
        outcome = Outcome(
            outcome_id=f"out_{cls.value}_{asset.address[:10]}_{int(t0.timestamp())}",
            outcome_class=cls,
            definition_version="v1",
            asset=asset,
            event_time=t0,
            first_observed_at=t0 + observation_lag,
            t0=t0,
            magnitude=magnitude,
            venue=venue,
        )
        self.world.outcomes.append(outcome)
        return outcome

    def _register(self, spec: WalletSpec) -> str:
        self.world.wallets[spec.handle] = spec
        return spec.address

    def _create_token(self, asset: AssetRef, at: datetime) -> datetime:
        """Emit the token's creation event and record when it happened.

        Needed so that "how fast did this wallet enter after launch" is
        answerable. Without a creation event, entry latency can only be measured
        against the first event we happened to see, which is our own ingestion
        artifact rather than anything about the wallet.
        """
        self.world.created_at[asset.address] = at
        self._emit(
            event_type=EventType.TOKEN_CREATED,
            actor=_addr(self.rng, "Dply"),
            asset=asset,
            chain_time=at,
        )
        return at

    def _after_creation(self, asset: AssetRef, latest: datetime) -> datetime:
        """A random time between this asset's creation and `latest`."""
        created = self.world.created_at[asset.address]
        if latest <= created:
            latest = created + days(1)
        span = (latest - created).total_seconds()
        return created + timedelta(seconds=self.rng.uniform(60, span))

    # ------------------------------------------------------------ price paths

    def _price_path(
        self, asset: AssetRef, t0: datetime, peak_multiple: float, collapse: bool
    ) -> None:
        """Generate hourly candles: flat accumulation, then the move, then decay."""
        rows: list[dict[str, Any]] = []
        base = 0.0004 * (1 + self.rng.random())
        start = t0 - days(45)
        cursor = start
        price = base
        while cursor < t0:
            price = base * (1 + self.rng.uniform(-0.04, 0.04))
            rows.append(self._candle(cursor, price, volume=self.rng.uniform(200, 3000)))
            cursor += hours(6)
        # The move.
        steps = 40
        for i in range(steps):
            frac = (i + 1) / steps
            price = base * (1 + (peak_multiple - 1) * frac)
            rows.append(self._candle(cursor, price, volume=self.rng.uniform(50_000, 400_000)))
            cursor += hours(1)
        # After.
        peak = price
        for i in range(120):
            if collapse:
                price = peak * max(0.01, (1 - (i / 120) * 0.97))
            else:
                price = peak * self.rng.uniform(0.55, 1.05)
            rows.append(self._candle(cursor, price, volume=self.rng.uniform(10_000, 120_000)))
            cursor += hours(3)
        self.world.candles[asset.address] = rows

    def _candle(self, start: datetime, price: float, volume: float) -> dict[str, Any]:
        return {
            "start": start.isoformat(),
            "open": f"{price:.10f}",
            "high": f"{price * 1.03:.10f}",
            "low": f"{price * 0.97:.10f}",
            "close": f"{price:.10f}",
            "volume_usd": f"{volume:.2f}",
        }

    def _dormant_then_revival(self, asset: AssetRef, t0: datetime) -> None:
        rows: list[dict[str, Any]] = []
        base = 0.00008
        cursor = t0 - days(150)
        while cursor < t0 - days(30):
            rows.append(self._candle(cursor, base * self.rng.uniform(0.9, 1.1), volume=40))
            cursor += hours(12)
        # Quiet accumulation: volume creeps up, price does not move. This is the
        # silent-accumulation fingerprint the signal engine looks for.
        while cursor < t0:
            rows.append(
                self._candle(
                    cursor, base * self.rng.uniform(0.98, 1.06), volume=self.rng.uniform(800, 2500)
                )
            )
            cursor += hours(6)
        for i in range(60):
            rows.append(
                self._candle(
                    cursor, base * (1 + i * 0.32), volume=self.rng.uniform(60_000, 300_000)
                )
            )
            cursor += hours(2)
        self.world.candles[asset.address] = rows

    # ------------------------------------------------------------------ build

    def build(self) -> World:
        rng = self.rng

        # ---- Cast -------------------------------------------------------
        insider_listing = self._register(
            WalletSpec(
                "insider_listing",
                _addr(rng, "Ins1"),
                "listing_predictor",
                True,
                "High listing hit rate, LOSES money. Must survive P&L-blind ranking.",
            )
        )
        side_wallet = self._register(
            WalletSpec(
                "side_wallet",
                _addr(rng, "Side"),
                "listing_predictor",
                True,
                "Acts ~18h after insider_listing. Sequence confirmation.",
            )
        )
        insider_quiet = self._register(
            WalletSpec(
                "insider_quiet",
                _addr(rng, "Ins2"),
                "informed_accumulator",
                True,
                "Six trades in a year, all early and quiet, all ran hard.",
            )
        )
        revival_specialist = self._register(
            WalletSpec(
                "revival_hunter",
                _addr(rng, "Rev1"),
                "revival_specialist",
                True,
                "Buys dead tokens weeks before they wake.",
            )
        )
        pump_operator = self._register(
            WalletSpec(
                "pump_operator",
                _addr(rng, "Pump"),
                "pump_operator",
                True,
                "Initiates pumps that collapse. Discovered, but classified as operator.",
            )
        )
        sniper_bot = self._register(
            WalletSpec(
                "sniper_bot",
                _addr(rng, "Snip"),
                "bot",
                False,
                "Great hit rate, first-block entries. Must be excluded.",
            )
        )
        churner = self._register(
            WalletSpec(
                "churner",
                _addr(rng, "Chrn"),
                "high_frequency",
                False,
                "Trades everything constantly. Hits by volume, not skill.",
            )
        )
        lucky = self._register(
            WalletSpec(
                "lucky_wallet",
                _addr(rng, "Luck"),
                "unclassified",
                False,
                "3-for-3 by chance. Must fail the sample-size guard.",
            )
        )
        noise_wallets = [_addr(rng, "Nz") for _ in range(60)]

        # ---- Assets and their fates -------------------------------------
        listing_assets = [self._asset(i, f"LIST{i}") for i in range(8)]
        run_assets = [self._asset(20 + i, f"RUN{i}") for i in range(10)]
        revival_assets = [self._asset(40 + i, f"REVIVE{i}") for i in range(6)]
        pump_assets = [self._asset(60 + i, f"PUMP{i}") for i in range(5)]
        # Duds dominate by count, as they do in reality. If the universe is
        # mostly winners the population base rate approaches 50%, no wallet can
        # show an edge over it, and the discovery guards suppress everything —
        # which is the correct behaviour, but makes for a useless fixture.
        dud_assets = [self._asset(80 + i, f"DUD{i}") for i in range(400)]

        # Duds: nothing ever happens. They exist so the population base rate is
        # honest. Removing them would inflate every hit rate in the system.
        for asset in dud_assets:
            t = WORLD_START + days(rng.uniform(45, 300))
            self._create_token(asset, t - days(30))
            self._price_path(asset, t, peak_multiple=rng.uniform(0.3, 1.6), collapse=False)
            for _ in range(rng.randint(3, 12)):
                self._emit(
                    event_type=EventType.SWAP,
                    actor=rng.choice(noise_wallets),
                    asset=asset,
                    chain_time=t - days(rng.uniform(0, 20)),
                    side=Side.BUY,
                    usd_value=Decimal(str(round(rng.uniform(100, 8000), 2))),
                )

        # ---- Listing storyline (the CASHCAT shape) ----------------------
        for i, asset in enumerate(listing_assets):
            t0 = WORLD_START + days(40 + i * 42)
            self._create_token(asset, t0 - days(38))
            self._price_path(asset, t0, peak_multiple=rng.uniform(3.5, 9.0), collapse=False)
            self._outcome(asset, OutcomeClass.CEX_LISTING, t0, venue="robinhood")
            self._outcome(asset, OutcomeClass.PRICE_RUN, t0 + hours(6), magnitude=Decimal("3"))

            # Probe: tiny, early, gets liquidated. Everyone dismisses it.
            self._emit(
                event_type=EventType.POSITION_OPENED,
                actor=insider_listing,
                asset=asset,
                chain_time=t0 - days(21),
                side=Side.LONG,
                usd_value=Decimal("500"),
                leverage=Decimal("5"),
                network=Network.HYPERLIQUID,
            )
            self._emit(
                event_type=EventType.LIQUIDATION,
                actor=insider_listing,
                asset=asset,
                chain_time=t0 - days(21) + hours(16),
                usd_value=Decimal("500"),
                network=Network.HYPERLIQUID,
            )
            # Resting bid well below market that never fills. Loudest signal.
            self._emit(
                event_type=EventType.ORDER_PLACED,
                actor=insider_listing,
                asset=asset,
                chain_time=t0 - days(9),
                side=Side.LONG,
                usd_value=Decimal("40000"),
                limit_price_usd=Decimal("0.00042"),
                filled=False,
                network=Network.HYPERLIQUID,
            )
            # Real entry.
            self._emit(
                event_type=EventType.SWAP,
                actor=insider_listing,
                asset=asset,
                chain_time=t0 - days(6),
                side=Side.BUY,
                usd_value=Decimal("55000"),
            )
            # Side wallet confirms ~18h later — the actual tell.
            self._emit(
                event_type=EventType.SWAP,
                actor=side_wallet,
                asset=asset,
                chain_time=t0 - days(6) + hours(18),
                side=Side.BUY,
                usd_value=Decimal("120000"),
            )
            # And then sells into the panic, at a loss, before the listing.
            self._emit(
                event_type=EventType.SWAP,
                actor=insider_listing,
                asset=asset,
                chain_time=t0 - days(2),
                side=Side.SELL,
                usd_value=Decimal("38000"),
            )

        # ---- Quiet accumulator ------------------------------------------
        for i, asset in enumerate(run_assets):
            t0 = WORLD_START + days(50 + i * 34)
            created = self._create_token(asset, t0 - days(44))
            self._price_path(asset, t0, peak_multiple=rng.uniform(4.0, 14.0), collapse=False)
            self._outcome(asset, OutcomeClass.PRICE_RUN, t0, magnitude=Decimal("5"))
            if i < 6:
                self._emit(
                    event_type=EventType.SWAP,
                    actor=insider_quiet,
                    asset=asset,
                    chain_time=t0 - days(rng.uniform(12, 26)),
                    side=Side.BUY,
                    usd_value=Decimal(str(round(rng.uniform(20_000, 60_000), 2))),
                )
            # Sniper is in everything, 1.2 seconds after the token exists.
            self._emit(
                event_type=EventType.SWAP,
                actor=sniper_bot,
                asset=asset,
                chain_time=created + timedelta(seconds=1.2),
                side=Side.BUY,
                usd_value=Decimal("900"),
            )

        # ---- Revival specialist -----------------------------------------
        for i, asset in enumerate(revival_assets):
            t0 = WORLD_START + days(160 + i * 40)
            self._create_token(asset, t0 - days(150))
            self._dormant_then_revival(asset, t0)
            self._outcome(asset, OutcomeClass.REVIVAL, t0, magnitude=Decimal("8"))
            for k in range(4):
                self._emit(
                    event_type=EventType.SWAP,
                    actor=revival_specialist,
                    asset=asset,
                    chain_time=t0 - days(24 - k * 5),
                    side=Side.BUY,
                    usd_value=Decimal(str(round(rng.uniform(4_000, 12_000), 2))),
                )

        # ---- Pump operator ----------------------------------------------
        for i, asset in enumerate(pump_assets):
            t0 = WORLD_START + days(70 + i * 50)
            self._create_token(asset, t0 - days(40))
            self._price_path(asset, t0, peak_multiple=rng.uniform(6.0, 20.0), collapse=True)
            self._outcome(asset, OutcomeClass.PUMP_EVENT, t0, magnitude=Decimal("10"))
            self._outcome(asset, OutcomeClass.RUG_OR_COLLAPSE, t0 + days(3))
            self._emit(
                event_type=EventType.SWAP,
                actor=pump_operator,
                asset=asset,
                chain_time=t0 - days(4),
                side=Side.BUY,
                usd_value=Decimal("80000"),
            )
            self._emit(
                event_type=EventType.SWAP,
                actor=pump_operator,
                asset=asset,
                chain_time=t0 + hours(20),
                side=Side.SELL,
                usd_value=Decimal("310000"),
            )

        # ---- Decoys ------------------------------------------------------
        # Lucky: 3 hits, zero misses, tiny sample, all in one 3-week window.
        for asset in run_assets[:3]:
            self._emit(
                event_type=EventType.SWAP,
                actor=lucky,
                asset=asset,
                chain_time=self._after_creation(asset, WORLD_START + days(31)),
                side=Side.BUY,
                usd_value=Decimal("2500"),
            )
        # Churner: buys nearly everything, so it hits by construction.
        for asset in self.world.assets:
            for _ in range(rng.randint(4, 9)):
                self._emit(
                    event_type=EventType.SWAP,
                    actor=churner,
                    asset=asset,
                    chain_time=self._after_creation(asset, WORLD_END),
                    side=Side.BUY if rng.random() > 0.4 else Side.SELL,
                    usd_value=Decimal(str(round(rng.uniform(200, 4000), 2))),
                )
        # Noise: random participation across everything.
        for asset in self.world.assets:
            for _ in range(rng.randint(5, 20)):
                self._emit(
                    event_type=EventType.SWAP,
                    actor=rng.choice(noise_wallets),
                    asset=asset,
                    chain_time=self._after_creation(asset, WORLD_END),
                    side=Side.BUY,
                    usd_value=Decimal(str(round(rng.uniform(50, 15_000), 2))),
                )

        self.world.events.sort(key=lambda e: e.chain_time)
        return self.world


def build_world() -> World:
    return _Builder().build()


WORLD_TRUTH: dict[str, dict[str, Any]] = {
    "insider_listing": {"discover": True, "archetype": "listing_predictor", "profitable": False},
    "side_wallet": {"discover": True, "archetype": "listing_predictor", "profitable": True},
    "insider_quiet": {"discover": True, "archetype": "informed_accumulator", "profitable": True},
    "revival_hunter": {"discover": True, "archetype": "revival_specialist", "profitable": True},
    "pump_operator": {"discover": True, "archetype": "pump_operator", "profitable": True},
    "sniper_bot": {"discover": False, "archetype": "bot", "profitable": True},
    "churner": {"discover": False, "archetype": "high_frequency", "profitable": False},
    "lucky_wallet": {"discover": False, "archetype": "unclassified", "profitable": True},
}


def write_fixtures(target: Path) -> World:
    """Materialize the world to JSON so fixtures are inspectable and diffable."""
    world = build_world()
    target.mkdir(parents=True, exist_ok=True)
    (target / "events.json").write_text(
        json.dumps([json.loads(e.model_dump_json()) for e in world.events], indent=1)
    )
    (target / "outcomes.json").write_text(
        json.dumps([json.loads(o.model_dump_json()) for o in world.outcomes], indent=1)
    )
    (target / "assets.json").write_text(
        json.dumps([json.loads(a.model_dump_json()) for a in world.assets], indent=1)
    )
    (target / "candles.json").write_text(json.dumps(world.candles))
    (target / "wallets.json").write_text(
        json.dumps({k: v.__dict__ for k, v in world.wallets.items()}, indent=1)
    )
    return world
