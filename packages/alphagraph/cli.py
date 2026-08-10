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
