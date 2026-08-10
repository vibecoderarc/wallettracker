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
        from alphagraph.entities.graph import DossierService, GraphBuilder
        from alphagraph.pipeline import bootstrap
        from alphagraph.playbooks.engine import PlaybookEngine
        from alphagraph.providers.fixture import (
            FixtureMarketDataProvider,
            RecordingNotificationProvider,
        )
        from alphagraph.signals.policy import AlertDispatcher

        with session_scope() as session:
            console.print("[bold]1. Ingest + outcomes + discovery[/bold]")
            result = await bootstrap(session, world)
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
            from sqlalchemy import select

            from alphagraph.db.models import Candidate

            for candidate in session.execute(
                select(Candidate).order_by(Candidate.edge_vs_base.desc())
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

            console.print("\n[bold]2. Promote to tracked, build graph and dossiers[/bold]")
            from alphagraph.discovery.engine import CandidateStatus, DiscoveryEngine

            engine = DiscoveryEngine(session)
            engine.advance_lifecycle(WORLD_END)
            # Fast-forward past the shadow period so the demo shows live signals.
            engine.advance_lifecycle(WORLD_END + timedelta(days=31))

            tracked = [
                c.wallet
                for c in session.execute(select(Candidate)).scalars()
                if c.status == CandidateStatus.TRACKED
            ]
            builder = GraphBuilder(session)
            edges = builder.build_edges(tracked, WORLD_END)
            console.print(f"graph edges: {builder.persist_edges(edges)}")

            dossiers = DossierService(session)
            primary = world.wallet("insider_listing")
            side = world.wallet("side_wallet")
            entity = dossiers.create(
                "ent_listing_ring",
                "Listing ring (primary + side wallet)",
                [primary, side],
                archetype="listing_predictor",
                summary="Probes small, aborts, rests a bid, then sizes in. Side wallet confirms.",
                method="co_acquisition_sequence",
            )
            dossiers.add_note(
                entity.id,
                "Unprofitable overall but 8/8 on listings. Track the sequence, not the P&L.",
                kind="hypothesis",
            )

            playbooks = PlaybookEngine(session)
            sequences = playbooks.mine(entity.id, [primary, side], WORLD_END)
            playbook = playbooks.build_playbook(entity.id, sequences)
            if playbook:
                console.print(
                    f"playbook: {' → '.join(playbook.stages)} "
                    f"({playbook.supporting_sequences} sequences, "
                    f"{playbook.aborted_sequences} aborted)"
                )
            else:
                console.print("[yellow]playbook withheld: too few completed sequences[/yellow]")

            console.print("\n[bold]3. Signals (point-in-time replay)[/bold]")
            from alphagraph.backtest.engine import Backtester, replay_signals
            from alphagraph.signals.policy import AlertPolicy

            # Replay the clock rather than computing signals once at WORLD_END,
            # which would use the whole history to decide what fired in the past.
            signals = replay_signals(session, WORLD_START, WORLD_END, timedelta(days=5))
            console.print(f"signals fired across the window: {len(signals)}")

            notifier = RecordingNotificationProvider()
            # A historical backfill legitimately persists everything; the small
            # per-run cap exists to stop live alert floods, not replays.
            dispatcher = AlertDispatcher(
                session, notifier, AlertPolicy(max_per_run=len(signals) + 10)
            )
            dispatch = await dispatcher.dispatch(signals)
            console.print(dispatch.as_dict())
            for signal in sorted(signals, key=lambda s: s.significance, reverse=True)[:5]:
                console.print(f"  • [{signal.family}] {signal.title}")
                for fact in signal.supporting_facts[:2]:
                    console.print(f"      - {fact}")

            console.print("\n[bold]4. Backtest with baselines[/bold]")
            market = FixtureMarketDataProvider(world)
            backtester = Backtester(session, market)
            entries = [(s.asset_id, s.triggered_at) for s in signals if s.asset_id]
            run = await backtester.run("demo_signals", entries, WORLD_START, WORLD_END)
            baseline = await backtester.random_baseline(len(entries) or 20, WORLD_START, WORLD_END)
            console.print(f"strategy: {run.metrics}")
            console.print(f"baseline: {baseline.as_dict()}")
            console.print(
                f"leakage audit: {'PASSED' if run.leakage_audit_passed else 'FAILED'} "
                f"{run.leakage_audit_detail}"
            )

            console.print("\n[bold]5. Nightly loop[/bold]")
            from alphagraph.nightly.loop import NightlyLoop, render_digest

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
    """Start the API server."""
    import uvicorn

    settings = get_settings()
    create_all()
    uvicorn.run(
        "alphagraph.api.app:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    app()
