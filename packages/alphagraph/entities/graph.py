"""Entity dossiers, clustering, and the sequence-aware graph (spec §6).

The distinguishing feature versus a conventional wallet-clustering module is
ordering. Knowing two wallets are related is trivia. Knowing that B reliably
moves ~18 hours after A, in 7 of 8 observed sequences, is a confirmation you can
act on — and it is what the CASHCAT side-wallet tell actually was.

Clusters are versioned hypotheses. Nothing here merges addresses irreversibly.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphagraph.db.models import Entity, EntityMember, EntityNote, Event, GraphEdge

CLUSTER_VERSION = "v1"


class EdgeType:
    CO_ACQUIRED = "co_acquired"
    LEADS = "leads"  # source consistently acts before target
    FUNDED_BY = "funded_by"
    SAME_ENTITY_AS = "same_entity_as"


@dataclass(frozen=True, slots=True)
class SequenceConfig:
    #: Two wallets acquiring the same asset within this window are "co-acquiring".
    #: Deliberately wide. These entities operate on multi-week procedures — a
    #: probe three weeks before the real entry is part of the same sequence, and
    #: a tight window would sever exactly the lead/follow relationships worth
    #: finding. Matches the playbook sequence window.
    max_lag: timedelta = timedelta(days=21)
    min_shared_assets: int = 3
    #: Fraction of shared assets where the ordering must hold in the same
    #: direction before the edge is called a lead/follow relationship.
    min_ordering_consistency: float = 0.75


#: Documented false-positive modes, stored on every edge (spec §6.2).
#: These are shown in the UI next to the edge so a user never reads an inferred
#: link as established fact.
FALSE_POSITIVE_MODES = {
    EdgeType.CO_ACQUIRED: [
        "Both wallets may simply follow the same public caller or Telegram group",
        "Popular assets produce spurious co-occurrence between unrelated wallets",
        "A shared exchange withdrawal batch is not evidence of common ownership",
    ],
    EdgeType.LEADS: [
        "Consistent lag can reflect timezone habits rather than coordination",
        "A small number of shared assets makes ordering look deterministic by chance",
        "The follower may be copy-trading publicly, not privately connected",
    ],
}


@dataclass
class EdgeCandidate:
    source: str
    target: str
    shared_assets: list[str] = field(default_factory=list)
    lags_seconds: list[float] = field(default_factory=list)

    @property
    def observations(self) -> int:
        return len(self.shared_assets)

    @property
    def median_lag(self) -> float:
        return statistics.median(self.lags_seconds) if self.lags_seconds else 0.0

    @property
    def ordering_consistency(self) -> float:
        """Fraction of observations where source acted before target."""
        if not self.lags_seconds:
            return 0.0
        forward = sum(1 for lag in self.lags_seconds if lag > 0)
        return forward / len(self.lags_seconds)


class GraphBuilder:
    def __init__(self, session: Session, config: SequenceConfig | None = None) -> None:
        self.session = session
        self.config = config or SequenceConfig()

    def _first_acquisitions(self, as_of: datetime) -> dict[str, dict[str, datetime]]:
        """asset_id -> {wallet: first acquisition time}, bounded by observation."""
        rows = self.session.execute(
            select(Event.asset_id, Event.actor, Event.observed_at)
            .where(
                Event.observed_at <= as_of,
                Event.side.in_(["buy", "long"]),
                Event.asset_id.is_not(None),
            )
            .order_by(Event.observed_at)
        ).all()
        grouped: dict[str, dict[str, datetime]] = defaultdict(dict)
        for asset_id, actor, observed in rows:
            grouped[asset_id].setdefault(actor, observed)
        return grouped

    def build_edges(self, wallets: list[str], as_of: datetime) -> list[EdgeCandidate]:
        """Pairwise sequence analysis restricted to a wallet set.

        Restricted deliberately: an all-pairs sweep over every wallet is
        quadratic and would surface mostly coincidence. Edges are only worth
        computing between wallets that already earned attention.
        """
        wanted = set(wallets)
        by_asset = self._first_acquisitions(as_of)
        pairs: dict[tuple[str, str], EdgeCandidate] = {}

        for asset_id, actors in by_asset.items():
            present = {w: t for w, t in actors.items() if w in wanted}
            if len(present) < 2:
                continue
            ordered = sorted(present.items(), key=lambda kv: kv[1])
            for i, (source, t_source) in enumerate(ordered):
                for target, t_target in ordered[i + 1 :]:
                    lag = (t_target - t_source).total_seconds()
                    if lag > self.config.max_lag.total_seconds():
                        continue
                    key = (source, target)
                    candidate = pairs.setdefault(key, EdgeCandidate(source=source, target=target))
                    candidate.shared_assets.append(asset_id)
                    candidate.lags_seconds.append(lag)

        return [c for c in pairs.values() if c.observations >= self.config.min_shared_assets]

    def persist_edges(self, candidates: list[EdgeCandidate]) -> int:
        written = 0
        for candidate in candidates:
            consistency = candidate.ordering_consistency
            is_lead = consistency >= self.config.min_ordering_consistency
            edge_type = EdgeType.LEADS if is_lead else EdgeType.CO_ACQUIRED

            existing = self.session.execute(
                select(GraphEdge).where(
                    GraphEdge.source == candidate.source,
                    GraphEdge.target == candidate.target,
                    GraphEdge.edge_type == edge_type,
                )
            ).scalar_one_or_none()

            # Confidence rises with observations but is capped: no amount of
            # behavioural correlation proves common control.
            confidence = min(0.85, 0.3 + 0.1 * candidate.observations) * max(consistency, 0.5)

            payload = {
                "observations": candidate.observations,
                "median_lag_seconds": candidate.median_lag,
                "ordering_consistency": consistency,
                "confidence": confidence,
                "false_positive_modes": FALSE_POSITIVE_MODES.get(edge_type, []),
                "evidence": {"shared_assets": candidate.shared_assets[:20]},
            }
            if existing is None:
                self.session.add(
                    GraphEdge(
                        source=candidate.source,
                        target=candidate.target,
                        edge_type=edge_type,
                        method="co_acquisition_sequence",
                        version=CLUSTER_VERSION,
                        **payload,
                    )
                )
                written += 1
            else:
                for key, value in payload.items():
                    setattr(existing, key, value)
        self.session.flush()
        return written

    def neighborhood(self, wallet: str, depth: int = 1, limit: int = 50) -> list[GraphEdge]:
        """Bounded neighborhood expansion.

        Depth and result limits are enforced here rather than left to callers:
        an unbounded graph walk is a denial-of-service vector against our own
        database (spec §14 threat model).
        """
        depth = max(1, min(depth, 3))
        seen: set[str] = {wallet}
        frontier = {wallet}
        edges: list[GraphEdge] = []
        for _ in range(depth):
            if not frontier or len(edges) >= limit:
                break
            rows = self.session.execute(
                select(GraphEdge)
                .where((GraphEdge.source.in_(frontier)) | (GraphEdge.target.in_(frontier)))
                .limit(limit - len(edges))
            ).scalars()
            next_frontier: set[str] = set()
            for edge in rows:
                edges.append(edge)
                for node in (edge.source, edge.target):
                    if node not in seen:
                        seen.add(node)
                        next_frontier.add(node)
            frontier = next_frontier
        return edges


class DossierService:
    """Entity dossiers — durable research artifacts, not derived views."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        entity_id: str,
        display_name: str,
        wallets: list[str],
        archetype: str,
        summary: str = "",
        method: str = "manual",
        confidence: float = 0.9,
        valid_from: datetime | None = None,
    ) -> Entity:
        """Create or extend an entity.

        `valid_from` defaults to now, but must be settable: when backfilling a
        dossier from historical evidence, the membership was true from the date
        the evidence existed, not from the moment it was typed in. Defaulting to
        now would make every historical point-in-time query miss the member.
        """
        entity = self.session.get(Entity, entity_id)
        if entity is None:
            entity = Entity(
                id=entity_id,
                display_name=display_name,
                archetype=archetype,
                summary=summary,
            )
            self.session.add(entity)
            self.session.flush()

        existing = {m.wallet for m in entity.members if m.valid_to is None}
        for wallet in wallets:
            if wallet in existing:
                continue
            member = EntityMember(
                entity_id=entity.id,
                wallet=wallet,
                method=method,
                confidence=confidence,
                version=CLUSTER_VERSION,
            )
            if valid_from is not None:
                member.valid_from = valid_from
            self.session.add(member)
        self.session.flush()
        return entity

    def retract_member(self, entity_id: str, wallet: str, at: datetime) -> bool:
        """Close a membership without deleting it.

        A user must be able to retract a claim they made without destroying the
        audit trail, and a historical point-in-time query must still see the
        cluster as it was believed to be at the time (spec §6.1).
        """
        member = self.session.execute(
            select(EntityMember).where(
                EntityMember.entity_id == entity_id,
                EntityMember.wallet == wallet,
                EntityMember.valid_to.is_(None),
            )
        ).scalar_one_or_none()
        if member is None:
            return False
        member.valid_to = at
        self.session.flush()
        return True

    def members_at(self, entity_id: str, as_of: datetime) -> list[str]:
        rows = self.session.execute(
            select(EntityMember).where(
                EntityMember.entity_id == entity_id,
                EntityMember.valid_from <= as_of,
            )
        ).scalars()
        return [m.wallet for m in rows if m.valid_to is None or m.valid_to > as_of]

    def add_note(
        self, entity_id: str, body: str, kind: str = "note", author: str = "operator"
    ) -> EntityNote:
        note = EntityNote(entity_id=entity_id, body=body, kind=kind, author=author)
        self.session.add(note)
        self.session.flush()
        return note

    def entity_for_wallet(self, wallet: str, as_of: datetime) -> Entity | None:
        member = (
            self.session.execute(
                select(EntityMember).where(
                    EntityMember.wallet == wallet,
                    EntityMember.valid_from <= as_of,
                )
            )
            .scalars()
            .first()
        )
        if member is None or (member.valid_to is not None and member.valid_to <= as_of):
            return None
        return self.session.get(Entity, member.entity_id)
