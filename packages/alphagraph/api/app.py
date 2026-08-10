"""FastAPI surface (spec §11 screens, v1 §17 endpoint shape).

Read-only for analytics; the few mutations are watchlist/dossier/paper actions
and proposal approval. There is no endpoint anywhere that constructs, signs, or
submits a transaction, and there must never be one.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from alphagraph.config import get_settings
from alphagraph.core.outcomes import OutcomeClass
from alphagraph.db.models import (
    Asset,
    Candidate,
    Digest,
    Entity,
    Event,
    OutcomeRow,
    PaperPosition,
    Proposal,
    ProviderHealthSample,
    SignalRow,
)
from alphagraph.db.session import get_session_factory
from alphagraph.discovery.engine import CandidateStatus
from alphagraph.entities.graph import DossierService, GraphBuilder
from alphagraph.outcomes.registry import OutcomeRegistry
from alphagraph.wallets.metrics import WalletMetricsCalculator


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Fill an empty database in the background on first boot.

    Threaded rather than awaited: the health check must pass immediately or the
    platform considers the deploy failed while seeding is still running. The
    dashboard stays reachable throughout and shows empty states until data
    lands.
    """
    import threading

    from alphagraph.seed import seed_if_empty_blocking

    threading.Thread(target=seed_if_empty_blocking, name="auto-seed", daemon=True).start()
    yield


app = FastAPI(
    title="AlphaGraph",
    version="0.1.0",
    description="Insider footprint intelligence. Research only — not investment advice.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def require_token(authorization: str = Header(default="")) -> None:
    """Bearer-token gate on every endpoint except /v1/health.

    Compared with `secrets.compare_digest` so a wrong token cannot be recovered
    by timing the response.

    When no token is configured the gate is open — that is only reachable in
    local development, because `Settings` refuses to start without one in any
    other environment.
    """
    settings = get_settings()
    if not settings.auth_required:
        return
    scheme, _, presented = authorization.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise HTTPException(
            status_code=401,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not secrets.compare_digest(presented, settings.api_token):
        raise HTTPException(status_code=403, detail="Invalid token")


#: Applied to every route below. Health is declared before this is used and is
#: deliberately left open so platform health checks work without a secret.
protected = [Depends(require_token)]


DISCLAIMER = (
    "Research tool. Public data only. Not investment advice, and no guarantee of "
    "any outcome. Every position is your own decision."
)


def get_db() -> Session:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _serialize(row: Any, fields: list[str]) -> dict:
    out: dict[str, Any] = {}
    for field_name in fields:
        value = getattr(row, field_name, None)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif hasattr(value, "quantize"):  # Decimal
            value = float(value)
        out[field_name] = value
    return out


# ------------------------------------------------------------------- system


@app.get("/")
def root() -> dict:
    """Signpost for anyone who opens the engine's URL expecting the dashboard.

    Left unauthenticated and deliberately free of any detail about the system's
    state — it says what this service is and where the UI lives, nothing more.
    Without it the root returns a bare "Not Found", which reads like a broken
    deploy when the service is in fact working correctly.
    """
    return {
        "service": "alphagraph-api",
        "status": "ok",
        "message": (
            "This is the AlphaGraph engine, not the dashboard. It has no web UI. "
            "Open the alphagraph-web service URL to view the dashboard."
        ),
        "health": "/v1/health",
        "docs": "/docs",
    }


@app.get("/v1/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Deliberately reveals no configuration detail."""
    return {"status": "ok", "run_mode": get_settings().run_mode.value}


@app.get("/v1/system/coverage", dependencies=protected)
def coverage(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    latest = (
        db.execute(select(ProviderHealthSample).order_by(desc(ProviderHealthSample.at)).limit(10))
        .scalars()
        .all()
    )
    newest_event = db.execute(select(func.max(Event.observed_at))).scalar_one_or_none()
    return {
        "run_mode": settings.run_mode.value,
        "provider_mode": settings.provider_mode.value,
        "notifications_enabled": settings.notifications_enabled,
        "events": db.execute(select(func.count(Event.event_id))).scalar_one(),
        "assets": db.execute(select(func.count(Asset.id))).scalar_one(),
        "outcomes": db.execute(select(func.count(OutcomeRow.outcome_id))).scalar_one(),
        "latest_event_observed_at": newest_event.isoformat() if newest_event else None,
        "providers": [
            {
                "provider": s.provider,
                "healthy": s.healthy,
                "at": s.at.isoformat(),
                "detail": s.detail,
            }
            for s in latest
        ],
        "disclaimer": DISCLAIMER,
    }


@app.get("/v1/system/base-rates", dependencies=protected)
def base_rates(db: Session = Depends(get_db)) -> dict:
    """Population base rates — the denominator for every claim in the UI."""
    registry = OutcomeRegistry(db)
    now = db.execute(select(func.max(OutcomeRow.first_observed_at))).scalar_one_or_none()
    now = now or datetime.now().astimezone()
    result = {}
    for cls in OutcomeClass:
        rate, hits, total = registry.base_rate(cls, now)
        result[cls.value] = {"rate": rate, "hits": hits, "total_assets": total}
    return {"as_of": now.isoformat(), "base_rates": result}


# ------------------------------------------------------------------ signals

SIGNAL_FIELDS = [
    "id",
    "dedupe_key",
    "family",
    "triggered_at",
    "evidence_cutoff",
    "asset_id",
    "wallet",
    "entity_id",
    "title",
    "significance",
    "confidence",
    "risk_band",
    "supporting_facts",
    "risks",
    "unknowns",
    "base_rate_context",
    "feature_vector",
    "evidence_ids",
    "delivered",
    "shadow_mode",
    "grade",
    "grade_detail",
]


@app.get("/v1/signals", dependencies=protected)
def list_signals(
    db: Session = Depends(get_db),
    limit: int = Query(50, le=200),
    family: str | None = None,
    min_significance: float = 0.0,
) -> dict:
    stmt = select(SignalRow).where(SignalRow.significance >= min_significance)
    if family:
        stmt = stmt.where(SignalRow.family == family)
    rows = db.execute(stmt.order_by(desc(SignalRow.triggered_at)).limit(limit)).scalars().all()
    return {"signals": [_serialize(r, SIGNAL_FIELDS) for r in rows], "disclaimer": DISCLAIMER}


@app.get("/v1/signals/{signal_id}", dependencies=protected)
def get_signal(signal_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(SignalRow, signal_id)
    if row is None:
        raise HTTPException(404, "signal not found")
    payload = _serialize(row, SIGNAL_FIELDS)
    payload["evidence"] = [
        _serialize(
            e,
            [
                "event_id",
                "event_type",
                "actor",
                "asset_id",
                "chain_time",
                "observed_at",
                "usd_value",
                "filled",
                "side",
            ],
        )
        for e in db.execute(
            select(Event).where(Event.event_id.in_(row.evidence_ids or []))
        ).scalars()
    ]
    return payload


# --------------------------------------------------------------- candidates

CANDIDATE_FIELDS = [
    "id",
    "wallet",
    "network",
    "status",
    "surfaced_at",
    "shadow_started_at",
    "promoted_at",
    "archetype",
    "hit_rate",
    "shrunk_hit_rate",
    "base_rate",
    "sample_size",
    "independent_events",
    "edge_vs_base",
    "discovery_provenance",
    "rejection_reason",
]


@app.get("/v1/candidates", dependencies=protected)
def list_candidates(
    db: Session = Depends(get_db),
    status: str | None = None,
    limit: int = Query(100, le=500),
) -> dict:
    stmt = select(Candidate)
    if status:
        stmt = stmt.where(Candidate.status == status)
    rows = db.execute(stmt.order_by(desc(Candidate.edge_vs_base)).limit(limit)).scalars().all()
    return {"candidates": [_serialize(r, CANDIDATE_FIELDS) for r in rows]}


@app.get("/v1/wallets/{wallet}", dependencies=protected)
def wallet_profile(wallet: str, db: Session = Depends(get_db)) -> dict:
    as_of = db.execute(select(func.max(Event.observed_at))).scalar_one_or_none()
    if as_of is None:
        raise HTTPException(404, "no data ingested")
    metrics = WalletMetricsCalculator(db).compute(wallet, as_of)
    candidate = db.execute(select(Candidate).where(Candidate.wallet == wallet)).scalar_one_or_none()
    events = (
        db.execute(
            select(Event).where(Event.actor == wallet).order_by(desc(Event.observed_at)).limit(100)
        )
        .scalars()
        .all()
    )
    return {
        "wallet": wallet,
        "metrics": metrics.as_dict(),
        "candidate": _serialize(candidate, CANDIDATE_FIELDS) if candidate else None,
        "timeline": [
            _serialize(
                e,
                [
                    "event_id",
                    "event_type",
                    "asset_id",
                    "side",
                    "usd_value",
                    "observed_at",
                    "filled",
                    "limit_price_usd",
                ],
            )
            for e in events
        ],
        # Displayed but never used for ranking. Spec §1.1: predictive skill and
        # profitability are different measurements.
        "profitability_note": (
            "Realized P&L is context only. A wallet can predict reliably and still "
            "lose money; ranking never uses this figure."
        ),
    }


# ------------------------------------------------------------------ assets


@app.get("/v1/assets/{asset_id}", dependencies=protected)
def asset_page(asset_id: str, db: Session = Depends(get_db)) -> dict:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "asset not found")
    outcomes = (
        db.execute(
            select(OutcomeRow).where(OutcomeRow.asset_id == asset_id).order_by(OutcomeRow.t0)
        )
        .scalars()
        .all()
    )
    early = (
        db.execute(
            select(Event)
            .where(Event.asset_id == asset_id, Event.side.in_(["buy", "long"]))
            .order_by(Event.observed_at)
            .limit(50)
        )
        .scalars()
        .all()
    )
    tracked = {
        c.wallet: c.archetype
        for c in db.execute(
            select(Candidate).where(
                Candidate.status.in_([CandidateStatus.TRACKED, CandidateStatus.SHADOW])
            )
        ).scalars()
    }
    return {
        "asset": _serialize(
            asset, ["id", "network", "address", "symbol", "decimals", "first_seen_at"]
        ),
        "outcomes": [
            _serialize(
                o,
                [
                    "outcome_id",
                    "outcome_class",
                    "t0",
                    "first_observed_at",
                    "magnitude",
                    "venue",
                    "definition_version",
                ],
            )
            for o in outcomes
        ],
        "early_buyers": [
            {
                **_serialize(e, ["event_id", "actor", "observed_at", "usd_value", "side"]),
                "tracked_archetype": tracked.get(e.actor),
            }
            for e in early
        ],
    }


# ---------------------------------------------------------------- entities


@app.get("/v1/entities", dependencies=protected)
def list_entities(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(Entity)).scalars().all()
    return {
        "entities": [
            {
                **_serialize(
                    e, ["id", "display_name", "entity_type", "status", "archetype", "summary"]
                ),
                "member_count": len([m for m in e.members if m.valid_to is None]),
            }
            for e in rows
        ]
    }


@app.get("/v1/entities/{entity_id}", dependencies=protected)
def entity_dossier(entity_id: str, db: Session = Depends(get_db)) -> dict:
    entity = db.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(404, "entity not found")
    as_of = db.execute(select(func.max(Event.observed_at))).scalar_one_or_none()
    members = [m for m in entity.members if m.valid_to is None]
    calculator = WalletMetricsCalculator(db)
    return {
        **_serialize(
            entity, ["id", "display_name", "entity_type", "status", "archetype", "summary"]
        ),
        "members": [
            {
                **_serialize(m, ["wallet", "method", "confidence", "valid_from", "version"]),
                "metrics": calculator.compute(m.wallet, as_of).as_dict() if as_of else None,
            }
            for m in members
        ],
        "notes": [
            _serialize(n, ["id", "author", "kind", "body", "created_at"]) for n in entity.notes
        ],
    }


class NoteIn(BaseModel):
    body: str
    kind: str = "note"


@app.post("/v1/entities/{entity_id}/notes", dependencies=protected)
def add_note(entity_id: str, note: NoteIn, db: Session = Depends(get_db)) -> dict:
    if db.get(Entity, entity_id) is None:
        raise HTTPException(404, "entity not found")
    created = DossierService(db).add_note(entity_id, note.body, note.kind)
    db.commit()
    return _serialize(created, ["id", "author", "kind", "body", "created_at"])


@app.get("/v1/graph/neighborhood", dependencies=protected)
def neighborhood(
    wallet: str,
    depth: int = Query(1, ge=1, le=3),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
) -> dict:
    edges = GraphBuilder(db).neighborhood(wallet, depth=depth, limit=limit)
    return {
        "root": wallet,
        "depth": depth,
        "edges": [
            _serialize(
                e,
                [
                    "source",
                    "target",
                    "edge_type",
                    "method",
                    "confidence",
                    "observations",
                    "median_lag_seconds",
                    "ordering_consistency",
                    "false_positive_modes",
                ],
            )
            for e in edges
        ],
    }


# ------------------------------------------------------- proposals / digest


@app.get("/v1/proposals", dependencies=protected)
def list_proposals(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(Proposal).order_by(desc(Proposal.created_at))).scalars().all()
    return {
        "proposals": [
            _serialize(
                p,
                [
                    "id",
                    "kind",
                    "target",
                    "current_value",
                    "proposed_value",
                    "justification",
                    "backtest_summary",
                    "sample_size",
                    "variants_tested",
                    "status",
                    "decided_at",
                    "decided_by",
                ],
            )
            for p in rows
        ],
        "note": "Proposals never auto-apply. Approval is an explicit operator action.",
    }


@app.post("/v1/proposals/{proposal_id}/approve", dependencies=protected)
def approve_proposal(proposal_id: int, db: Session = Depends(get_db)) -> dict:
    proposal = db.get(Proposal, proposal_id)
    if proposal is None:
        raise HTTPException(404, "proposal not found")
    if proposal.status != "pending":
        raise HTTPException(409, f"proposal already {proposal.status}")
    proposal.status = "approved"
    proposal.decided_at = datetime.now().astimezone()
    proposal.decided_by = "operator"
    db.commit()
    return {"status": "approved", "id": proposal_id}


@app.get("/v1/digests", dependencies=protected)
def digests(db: Session = Depends(get_db), limit: int = Query(14, le=90)) -> dict:
    rows = db.execute(select(Digest).order_by(desc(Digest.run_date)).limit(limit)).scalars().all()
    return {"digests": [{"run_date": d.run_date.isoformat(), "body": d.body} for d in rows]}


# ------------------------------------------------------------ paper trading


class PaperOrderIn(BaseModel):
    asset_id: str
    size_usd: float
    entry_price_usd: float
    signal_id: int | None = None
    slippage_bps: int = 150
    entry_delay_seconds: int = 300
    notes: str = ""


@app.post("/v1/paper/orders", dependencies=protected)
def create_paper_position(order: PaperOrderIn, db: Session = Depends(get_db)) -> dict:
    """Simulated only. This endpoint cannot and must not reach an exchange."""
    from decimal import Decimal

    position = PaperPosition(
        asset_id=order.asset_id,
        signal_id=order.signal_id,
        opened_at=datetime.now().astimezone(),
        entry_price_usd=Decimal(str(order.entry_price_usd)),
        size_usd=Decimal(str(order.size_usd)),
        fees_usd=Decimal(str(order.size_usd)) * Decimal("0.003"),
        slippage_bps=order.slippage_bps,
        entry_delay_seconds=order.entry_delay_seconds,
        simulated=True,
        notes=order.notes,
    )
    db.add(position)
    db.commit()
    return {
        "id": position.id,
        "simulated": True,
        "warning": "Paper position. No order was placed and no funds moved.",
    }


@app.get("/v1/paper/portfolio", dependencies=protected)
def paper_portfolio(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(select(PaperPosition).order_by(desc(PaperPosition.opened_at))).scalars().all()
    return {
        "positions": [
            _serialize(
                p,
                [
                    "id",
                    "asset_id",
                    "opened_at",
                    "closed_at",
                    "entry_price_usd",
                    "exit_price_usd",
                    "size_usd",
                    "fees_usd",
                    "slippage_bps",
                    "simulated",
                    "notes",
                ],
            )
            for p in rows
        ],
        "warning": "All positions are simulated. Nothing here is an executable fill.",
    }
