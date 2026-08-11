"""AlphaGraph CLI.

alphagraph demo          # build the whole thing from fixtures, end to end
alphagraph nightly       # one nightly cycle
alphagraph digest        # print the latest morning digest
alphagraph discover      # run a discovery sweep and show what passed
alphagraph backtest      # run a backtest with baselines and leakage audit
alphagraph serve         # start the API
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import typer
from rich.console import Console
from rich.table import Table

from alphagraph.config import get_settings
from alphagraph.db.session import create_all, session_scope
from alphagraph.providers.universe import UniverseSource
from alphagraph.providers.world import WORLD_END, WORLD_START, build_world

app = typer.Typer(help="AlphaGraph — insider footprint intelligence", no_args_is_help=True)
console = Console()


def _fmt_pct(value: float) -> str:
    return f"{value:.1%}"


@app.command()
def demo(database: str = typer.Option("", help="SQLAlchemy URL; defaults to configured")) -> None:
    """Build the full system from fixtures and report what it found."""
    url = database or get_settings().database_url
    create_all(url)
    world = build_world()

    async def run() -> None:
        from sqlalchemy import select

        from alphagraph.backtest.engine import Backtester
        from alphagraph.db.models import Candidate
        from alphagraph.nightly.loop import NightlyLoop, render_digest
        from alphagraph.providers.fixture import (
            FixtureMarketDataProvider,
            RecordingNotificationProvider,
        )
        from alphagraph.seed import seed_demo
        from alphagraph.signals.policy import AlertDispatcher, AlertPolicy

        with session_scope() as session:
            console.print("[bold]1. Ingest, outcomes, discovery, graph, signals[/bold]")
            result = await seed_demo(session, world)
            console.print(result.summary())

            handles = {spec.address: name for name, spec in world.wallets.items()}
            table = Table(title="Discovered candidates")
            for column in (
                "wallet",
                "planted as",
                "archetype",
                "hits",
                "indep",
                "hit rate",
                "base",
                "edge",
            ):
                table.add_column(column)
            for candidate in session.execute(
                select(Candidate).order_by(Candidate.edge_vs_base.desc()).limit(12)
            ).scalars():
                table.add_row(
                    candidate.wallet[:10] + "…",
                    handles.get(candidate.wallet, "[dim]noise[/dim]"),
                    candidate.archetype,
                    str(candidate.sample_size),
                    str(candidate.independent_events),
                    _fmt_pct(float(candidate.hit_rate)),
                    _fmt_pct(float(candidate.base_rate)),
                    f"{float(candidate.edge_vs_base):.1f}x",
                )
            console.print(table)
            if result.playbook_stages:
                console.print(f"playbook: {' → '.join(result.playbook_stages)}")

            console.print("\n[bold]2. Backtest with baselines[/bold]")
            market = FixtureMarketDataProvider(world)
            backtester = Backtester(session, market)
            from alphagraph.backtest.engine import replay_signals

            replayed = replay_signals(session, WORLD_START, WORLD_END, timedelta(days=5))
            entries = [(s.asset_id, s.triggered_at) for s in replayed if s.asset_id]
            run_row = await backtester.run("demo_signals", entries, WORLD_START, WORLD_END)
            baseline = await backtester.random_baseline(len(entries) or 20, WORLD_START, WORLD_END)
            console.print(f"strategy: {run_row.metrics}")
            console.print(f"baseline: {baseline.as_dict()}")
            console.print(
                f"leakage audit: {'PASSED' if run_row.leakage_audit_passed else 'FAILED'}"
            )

            console.print("\n[bold]3. Nightly loop[/bold]")
            dispatcher = AlertDispatcher(
                session, RecordingNotificationProvider(), AlertPolicy(max_per_run=500)
            )
            loop = NightlyLoop(session, market, dispatcher)
            report = await loop.run(WORLD_END + timedelta(days=31))
            console.print(render_digest(report, session))

    asyncio.run(run())
    console.print("\n[dim]Research tool. Public data only. Not investment advice.[/dim]")


@app.command()
def discover(as_of_days_ago: int = 0) -> None:
    """Run a discovery sweep against the current database."""
    from sqlalchemy import func, select

    from alphagraph.db.models import Event
    from alphagraph.discovery.engine import DiscoveryEngine

    create_all()
    with session_scope() as session:
        as_of = session.execute(select(func.max(Event.observed_at))).scalar_one_or_none()
        if as_of is None:
            console.print("[red]No events ingested. Run `alphagraph demo` first.[/red]")
            raise typer.Exit(1)
        as_of -= timedelta(days=as_of_days_ago)
        engine = DiscoveryEngine(session)
        scores = engine.run_sweep(as_of)
        passed = [s for s in scores if s.passed]
        console.print(
            f"as_of={as_of:%Y-%m-%d}  passed={len(passed)}  rejected={len(scores) - len(passed)}"
        )
        for score in passed:
            console.print(
                f"  {score.wallet[:12]}… {score.archetype} "
                f"hits={score.hits} indep={score.independent_events} "
                f"edge={score.edge_ratio:.1f}x base={score.base_rate:.1%}"
            )
        for score in scores:
            if not score.passed and score.hits >= 2:
                console.print(
                    f"  [dim]rejected {score.wallet[:12]}… {score.rejection_reason}[/dim]"
                )


@app.command()
def nightly() -> None:
    """Run one nightly cycle against the current database."""
    from sqlalchemy import func, select

    from alphagraph.db.models import Event
    from alphagraph.nightly.loop import NightlyLoop, render_digest
    from alphagraph.providers.fixture import (
        FixtureMarketDataProvider,
        RecordingNotificationProvider,
    )
    from alphagraph.signals.policy import AlertDispatcher

    create_all()

    async def run() -> None:
        with session_scope() as session:
            as_of = session.execute(select(func.max(Event.observed_at))).scalar_one_or_none()
            if as_of is None:
                console.print("[red]No events ingested. Run `alphagraph demo` first.[/red]")
                raise typer.Exit(1)
            market = FixtureMarketDataProvider(build_world())
            dispatcher = AlertDispatcher(session, RecordingNotificationProvider())
            loop = NightlyLoop(session, market, dispatcher)
            report = await loop.run(as_of)
            console.print(render_digest(report, session))

    asyncio.run(run())


@app.command()
def digest() -> None:
    """Print the most recent stored digest."""
    from sqlalchemy import desc, select

    from alphagraph.db.models import Digest

    create_all()
    with session_scope() as session:
        row = session.execute(
            select(Digest).order_by(desc(Digest.run_date)).limit(1)
        ).scalar_one_or_none()
        if row is None:
            console.print("[yellow]No digests yet. Run `alphagraph nightly`.[/yellow]")
            return
        console.print_json(data=row.body)


def build_market_provider() -> UniverseSource:
    """The universe source, chosen once so no two commands can disagree.

    Birdeye when a key is configured, GeckoTerminal otherwise. The fallback is
    kept because it needs no key and still works, but it is a worse universe:
    it cannot be asked for tokens above a volume threshold, so the floor has to
    be applied after the fact to whatever the pool listings happened to return.
    """
    import os

    from alphagraph.providers.geckoterminal import GeckoTerminalProvider

    settings = get_settings()
    key = settings.market_data_key or os.environ.get("BIRDEYE_API_KEY", "")
    if key:
        from alphagraph.providers.birdeye import BirdeyeProvider

        return BirdeyeProvider(key)
    return GeckoTerminalProvider()


@app.command()
def smoke() -> None:
    """Check the live providers actually work, before spending a sweep.

    The development sandbox blocks outbound traffic, so neither live provider
    has ever been exercised against its real API — only against recorded
    response shapes. Run this first, from somewhere with network access, so a
    changed payload format is a five-request discovery instead of a failed
    sweep that already spent its allowance.
    """
    import os

    from alphagraph.providers.helius import HeliusChainProvider

    settings = get_settings()
    api_key = settings.solana_indexer_key or os.environ.get("HELIUS_API_KEY", "")

    async def run() -> None:
        ok = True

        # Whatever the sweep would use, and the same call the sweep makes.
        # An earlier version showed `top_pools` instead, which meant it passed
        # while displaying a universe the sweep never sees.
        market = build_market_provider()
        console.print(f"[bold]1. Market data — {market.name}[/bold]")
        try:
            pools = await market.universe(pages=1)
            console.print(f"   universe entries parsed: {len(pools)}")
            if not pools:
                ok = False
                console.print("   [red]empty universe — the payload shape has changed[/red]")
            else:
                for pool in pools[:5]:
                    console.print(
                        f"   - {(pool.symbol or '?')[:12]:12} {pool.token_address[:18]}… "
                        f"liq={pool.reserve_usd} vol24h={pool.volume_24h_usd}"
                    )
                candles = await market.pool_candles(pools[0].pool_address)
                console.print(f"   candles for first entry: {len(candles)}")
                if candles:
                    span = (candles[-1].start - candles[0].start).days
                    console.print(
                        f"   range {candles[0].start.date()} -> {candles[-1].start.date()} "
                        f"({span} days)"
                    )
                    if span < 30:
                        ok = False
                        console.print(
                            "   [red]under a month of history — the sweep looks back six "
                            "months, so most outcomes would be invisible[/red]"
                        )
                else:
                    ok = False
                    console.print("   [red]no candles — OHLCV shape has changed[/red]")
                console.print(f"   usage: {market.usage}")
        except Exception as exc:
            ok = False
            console.print(f"   [red]FAILED: {type(exc).__name__}: {str(exc)[:200]}[/red]")

        console.print("\n[bold]2. Helius[/bold]")
        if not api_key:
            console.print("   [yellow]skipped: ALPHAGRAPH_SOLANA_INDEXER_KEY not set[/yellow]")
        else:
            chain = HeliusChainProvider(api_key)
            try:
                health = await chain.health()
                console.print(f"   health: {'ok' if health.healthy else health.detail}")
                # A high-traffic account, used only to confirm parsing works.
                probe = "So11111111111111111111111111111111111111112"
                raw = await chain._page(probe, None)
                console.print(f"   transactions returned: {len(raw)}")
                parsed = [e for tx in raw for e in chain.parse_transaction(tx, subject=probe)]
                swaps = sum(1 for e in parsed if e.event_type.value == "swap")
                unknown = sum(1 for e in parsed if e.event_type.value == "unknown_interaction")
                console.print(f"   parsed: {swaps} swaps, {unknown} uninterpretable")
                if parsed:
                    coverage = swaps / len(parsed)
                    console.print(f"   parse coverage: {coverage:.0%}")
                    if coverage < 0.3:
                        console.print(
                            "   [yellow]low coverage on this sample — expected for a "
                            "wrapped-SOL account, worth rechecking on a trader wallet[/yellow]"
                        )
                console.print(f"   usage: {chain.usage}")
            except Exception as exc:
                ok = False
                console.print(f"   [red]FAILED: {type(exc).__name__}: {str(exc)[:200]}[/red]")

        console.print()
        if ok:
            console.print(
                "[green]Providers respond and parse. "
                "Safe to run `alphagraph sweep --estimate`.[/green]"
            )
        else:
            console.print(
                "[red]Something is wrong above. Do not run a sweep until it is fixed.[/red]"
            )

    asyncio.run(run())


@app.command()
def sweep(
    pages: int = typer.Option(5, help="Pages of the token universe to examine"),
    window_days: int = typer.Option(180, help="How far back to look"),
    estimate_only: bool = typer.Option(
        False, "--estimate", help="Report the request cost without spending the Helius allowance"
    ),
) -> None:
    """Phase A: find candidate wallets from real Solana data, starting from none."""
    import os

    from alphagraph.bootstrap import BootstrapSweep, SweepBudget
    from alphagraph.providers.helius import HeliusChainProvider

    settings = get_settings()
    api_key = settings.solana_indexer_key or os.environ.get("HELIUS_API_KEY", "")
    if not api_key and not estimate_only:
        console.print(
            "[red]No Helius key.[/red] Set ALPHAGRAPH_SOLANA_INDEXER_KEY, "
            "or use --estimate to price the sweep without one."
        )
        raise typer.Exit(1)

    create_all()

    async def run() -> None:
        market = build_market_provider()
        # Estimation never touches Helius, so a placeholder key is fine there.
        chain = HeliusChainProvider(api_key or "estimate-only")
        with session_scope() as session:
            sweep = BootstrapSweep(session, chain, market, SweepBudget())
            if estimate_only:
                console.print("[bold]Estimating cost (no Helius requests)[/bold]")
                console.print_json(data=await sweep.estimate(pages=pages))
                return
            report = await sweep.run(pages=pages, window_days=window_days)
            console.print_json(data=report.as_dict())
            if not report.candidates_passed:
                console.print(
                    "\n[yellow]No candidates passed.[/yellow] That is a real result, not a "
                    "failure: the guards require independent events above the base rate, and "
                    "six months of history limits how much evidence any wallet can accumulate."
                )
            if report.parse_coverage < 0.8 and report.events_written:
                console.print(
                    f"\n[yellow]Parse coverage {report.parse_coverage:.0%}.[/yellow] "
                    "A large share of activity could not be interpreted, so these results "
                    "rest on partial history."
                )

    asyncio.run(run())


@app.command()
def collect(
    pages: int = typer.Option(3, help="Pages of the universe listing to pull"),
    max_observations: int = typer.Option(400, help="Hard cap on price fetches this run"),
) -> None:
    """Record today's market state for everything on the watchlist.

    Run this daily. It is the only part of the system that gets better purely
    by being left alone: each run adds a day of point-in-time history that no
    provider sells and nobody can revoke.

    The watchlist is sticky on purpose — an asset that cleared the traction
    floor once keeps being recorded after it goes quiet, because its dead days
    are the collapse, and a record that only covers the good days is the same
    survivorship bias that emptied the universe of collapses in the first place.
    """
    from alphagraph.collector import CollectorConfig, DailyCollector, archive_span

    create_all()

    async def run() -> None:
        market = build_market_provider()
        config = CollectorConfig(universe_pages=pages, max_observations=max_observations)
        with session_scope() as session:
            collector = DailyCollector(session, market, config)
            report = await collector.run()
            console.print_json(data=report.as_dict())

            span = archive_span(session)
            console.print(f"\n[bold]archive span: {span.days} days[/bold]")
            if span.days < 30:
                console.print(
                    "[yellow]The archive is still shallow.[/yellow] Discovery needs weeks "
                    "of accumulated history before a wallet can build enough independent "
                    "evidence to pass the guards. This grows on its own."
                )
            if report.skipped_for_budget:
                console.print(
                    f"[yellow]{report.skipped_for_budget} watched assets went unobserved "
                    "today.[/yellow] Raise --max-observations, or these become gaps that "
                    "will look like real quiet periods later."
                )

    asyncio.run(run())


@app.command()
def serve(host: str = "", port: int = 0) -> None:
    """Start the API server.

    Honours $PORT, which PaaS platforms assign at runtime, and binds 0.0.0.0
    when one is set — a platform-assigned port always implies the process must
    be reachable from outside its own container.
    """
    import os

    import uvicorn

    settings = get_settings()
    platform_port = os.environ.get("PORT")
    resolved_port = port or (int(platform_port) if platform_port else settings.api_port)
    resolved_host = host or ("0.0.0.0" if platform_port else settings.api_host)

    create_all()
    if not settings.auth_required:
        console.print(
            "[yellow]No API token configured — every endpoint is open. "
            "Acceptable locally; Settings refuses to start this way anywhere else.[/yellow]"
        )
    uvicorn.run(
        "alphagraph.api.app:app",
        host=resolved_host,
        port=resolved_port,
        reload=False,
    )


if __name__ == "__main__":
    app()
