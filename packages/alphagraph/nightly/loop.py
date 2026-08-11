"""Nightly evaluation and improvement loop (spec §9).

The bot does the labour; the operator decides. Concretely:

  * A language model does not tune thresholds here. Nothing in this module calls
    one. Tuning is statistical code with a backtest attached.
  * No proposal auto-applies. `Proposal` rows are written with status="pending"
    and require explicit approval.
  * `variants_tested` is recorded on every proposal. A system that tries
    hundreds of variants nightly will find spurious winners, and hiding the
    count would make a multiple-testing artifact look like a discovery.

Auto-tuning on small samples is how you build something that backtests
beautifully and loses money live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphagraph.core.outcomes import PREDICTIVE_CLASSES
from alphagraph.db.models import Candidate, Digest, OutcomeRow, Proposal, SignalRow
from alphagraph.discovery.engine import CandidateStatus, DiscoveryConfig, DiscoveryEngine
from alphagraph.outcomes.registry import OutcomeRegistry
from alphagraph.providers.base import MarketDataProvider
from alphagraph.signals.engine import SignalEngine
from alphagraph.signals.policy import AlertDispatcher

#: How long a signal has to be proven right before it is graded a miss.
GRADING_HORIZON = timedelta(days=21)


@dataclass
class NightlyReport:
    run_date: datetime
    outcomes_detected: dict[str, int] = field(default_factory=dict)
    signals_graded: dict[str, int] = field(default_factory=dict)
    discovery: dict[str, int] = field(default_factory=dict)
    lifecycle: dict[str, int] = field(default_factory=dict)
    new_signals: int = 0
    proposals: list[dict] = field(default_factory=list)
    variants_tested: int = 0
    family_precision: dict[str, dict[str, float]] = field(default_factory=dict)
    proposal_note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_date": self.run_date.isoformat(),
            "outcomes_detected": self.outcomes_detected,
            "signals_graded": self.signals_graded,
            "discovery": self.discovery,
            "lifecycle": self.lifecycle,
            "new_signals": self.new_signals,
            "proposals": self.proposals,
            "variants_tested": self.variants_tested,
            "family_precision": self.family_precision,
            "proposal_note": self.proposal_note,
        }


class NightlyLoop:
    def __init__(
        self,
        session: Session,
        market: MarketDataProvider,
        dispatcher: AlertDispatcher,
    ) -> None:
        self.session = session
        self.market = market
        self.dispatcher = dispatcher

    # ------------------------------------------------------------- grading

    def grade_signals(self, as_of: datetime) -> dict[str, int]:
        """Score past signals against what actually happened.

        A signal is a hit if a predictive outcome was first observed on its
        asset AFTER the signal fired. Outcomes that already existed when the
        signal fired do not count — the signal did not predict them.
        """
        counts = {"hit": 0, "miss": 0, "pending": 0}
        pending = self.session.execute(
            select(SignalRow).where(SignalRow.grade.in_(["pending", None]))
        ).scalars()

        for signal in pending:
            if signal.asset_id is None:
                signal.grade = "not_applicable"
                signal.graded_at = as_of
                continue

            outcome = self.session.execute(
                select(OutcomeRow)
                .where(
                    OutcomeRow.asset_id == signal.asset_id,
                    OutcomeRow.outcome_class.in_([c.value for c in PREDICTIVE_CLASSES]),
                    OutcomeRow.t0 > signal.triggered_at,
                    OutcomeRow.first_observed_at <= as_of,
                )
                .order_by(OutcomeRow.t0)
                .limit(1)
            ).scalar_one_or_none()

            if outcome is not None:
                signal.grade = "hit"
                signal.graded_at = as_of
                signal.grade_detail = {
                    "outcome_id": outcome.outcome_id,
                    "outcome_class": outcome.outcome_class,
                    "lead_time_days": round(
                        (outcome.t0 - signal.triggered_at).total_seconds() / 86400, 2
                    ),
                }
                counts["hit"] += 1
            elif as_of - signal.triggered_at >= GRADING_HORIZON:
                signal.grade = "miss"
                signal.graded_at = as_of
                signal.grade_detail = {"horizon_days": GRADING_HORIZON.days}
                counts["miss"] += 1
            else:
                counts["pending"] += 1

        self.session.flush()
        return counts

    # ------------------------------------------------------------ proposals

    def generate_proposals(self, as_of: datetime) -> tuple[list[Proposal], int, str]:
        """Evaluate threshold variants and propose changes — never apply them."""
        proposals: list[Proposal] = []
        variants = 0
        self.family_precision: dict[str, dict[str, float]] = {}

        graded = list(
            self.session.execute(
                select(SignalRow).where(SignalRow.grade.in_(["hit", "miss"]))
            ).scalars()
        )
        if len(graded) < 20:
            # Below this, any apparent improvement is noise. Proposing on a
            # dozen graded signals is how thresholds get tuned into the past.
            return proposals, variants, f"only {len(graded)} graded signals; need 20"

        # Per-family precision. Reported whether or not a proposal follows,
        # because "which family is generating the noise" is usually the more
        # actionable finding than any threshold tweak.
        self.family_precision = self._family_precision(graded)

        current = self.dispatcher.policy.min_significance
        best_threshold, best_precision = current, self._precision_at(graded, current)
        for candidate in (0.25, 0.35, 0.45, 0.55, 0.65):
            variants += 1
            precision = self._precision_at(graded, candidate)
            survivors = sum(1 for s in graded if float(s.significance) >= candidate)
            if precision > best_precision + 0.05 and survivors >= 10:
                best_threshold, best_precision = candidate, precision

        # A family that fires often and is right rarely costs more attention
        # than it returns. Propose muting it — never act unilaterally.
        proposals.extend(self._family_proposals(as_of))

        if best_threshold != current:
            proposal = Proposal(
                kind="threshold",
                target="alert_policy.min_significance",
                current_value=str(current),
                proposed_value=str(best_threshold),
                justification=(
                    f"Precision improves from {self._precision_at(graded, current):.1%} to "
                    f"{best_precision:.1%} on {len(graded)} graded signals."
                ),
                backtest_summary={
                    "graded_signals": len(graded),
                    "precision_current": self._precision_at(graded, current),
                    "precision_proposed": best_precision,
                },
                sample_size=len(graded),
                variants_tested=variants,
                status="pending",
            )
            self.session.add(proposal)
            proposals.append(proposal)
            note = f"proposed threshold change after testing {variants} variants"
        else:
            note = (
                f"no variant beat the current threshold by the required margin "
                f"({variants} tested on {len(graded)} graded signals)"
            )

        self.session.flush()
        return proposals, variants, note

    def _family_proposals(self, as_of: datetime) -> list[Proposal]:
        """Propose muting under-performing families and reinstating recovered ones.

        Compared against the population base rate rather than an absolute
        number: 5% precision is worthless when 6.8% of all assets move anyway,
        and respectable when only 1% do.
        """
        from alphagraph.core.outcomes import OutcomeClass
        from alphagraph.signals.policy import MUTED_FAMILIES

        base_rate, _, _ = OutcomeRegistry(self.session).base_rate(OutcomeClass.PRICE_RUN, as_of)
        proposals: list[Proposal] = []

        for family, stats in self.family_precision.items():
            graded = int(stats["graded"])
            if graded < 30:
                continue
            precision = stats["precision"]
            muted = family in MUTED_FAMILIES

            if not muted and precision < base_rate:
                proposals.append(
                    Proposal(
                        kind="mute_family",
                        target=f"alert_policy.muted_families += {family}",
                        current_value="alerting",
                        proposed_value="muted",
                        justification=(
                            f"{family} is right {precision:.1%} of the time across "
                            f"{graded} graded signals, below the {base_rate:.1%} you would "
                            "get from picking assets at random."
                        ),
                        backtest_summary={"precision": precision, "base_rate": base_rate},
                        sample_size=graded,
                        variants_tested=1,
                        status="pending",
                    )
                )
            elif muted and precision > base_rate * 2:
                proposals.append(
                    Proposal(
                        kind="unmute_family",
                        target=f"alert_policy.muted_families -= {family}",
                        current_value="muted",
                        proposed_value="alerting",
                        justification=(
                            f"{family} has recovered to {precision:.1%} across {graded} "
                            f"graded signals, more than double the {base_rate:.1%} base rate."
                        ),
                        backtest_summary={"precision": precision, "base_rate": base_rate},
                        sample_size=graded,
                        variants_tested=1,
                        status="pending",
                    )
                )

        for proposal in proposals:
            self.session.add(proposal)
        return proposals

    @staticmethod
    def _family_precision(signals: list[SignalRow]) -> dict[str, dict[str, float]]:
        by_family: dict[str, list[SignalRow]] = {}
        for signal in signals:
            by_family.setdefault(signal.family, []).append(signal)
        return {
            family: {
                "graded": len(rows),
                "hits": sum(1 for r in rows if r.grade == "hit"),
                "precision": sum(1 for r in rows if r.grade == "hit") / len(rows),
            }
            for family, rows in by_family.items()
        }

    @staticmethod
    def _precision_at(signals: list[SignalRow], threshold: float) -> float:
        kept = [s for s in signals if float(s.significance) >= threshold]
        if not kept:
            return 0.0
        return sum(1 for s in kept if s.grade == "hit") / len(kept)

    def apply_proposal(self, proposal_id: int, approved_by: str, at: datetime) -> bool:
        """Explicit operator approval. There is no automatic path to here."""
        proposal = self.session.get(Proposal, proposal_id)
        if proposal is None or proposal.status != "pending":
            return False
        proposal.status = "approved"
        proposal.decided_at = at
        proposal.decided_by = approved_by
        self.session.flush()
        return True

    # ------------------------------------------------------------------ run

    async def run(self, as_of: datetime) -> NightlyReport:
        report = NightlyReport(run_date=as_of)

        registry = OutcomeRegistry(self.session)
        from alphagraph.pipeline import assets_from_db

        report.outcomes_detected = await registry.detect_all(
            self.market, assets_from_db(self.session)
        )
        report.signals_graded = self.grade_signals(as_of)

        engine = DiscoveryEngine(self.session, DiscoveryConfig())
        scores = engine.run_sweep(as_of)
        report.discovery = engine.persist(scores, as_of)
        report.lifecycle = engine.advance_lifecycle(as_of)

        signal_engine = SignalEngine(self.session)
        signals = signal_engine.run_all(as_of)
        dispatch = await self.dispatcher.dispatch(signals)
        report.new_signals = dispatch.persisted

        proposals, variants, note = self.generate_proposals(as_of)
        report.variants_tested = variants
        report.proposal_note = note
        report.family_precision = getattr(self, "family_precision", {})
        report.proposals = [
            {
                "id": p.id,
                "target": p.target,
                "current": p.current_value,
                "proposed": p.proposed_value,
                "justification": p.justification,
                "sample_size": p.sample_size,
                "variants_tested": p.variants_tested,
                "status": p.status,
            }
            for p in proposals
        ]

        self.session.add(Digest(run_date=as_of, body=report.as_dict()))
        self.session.flush()
        return report


def render_digest(report: NightlyReport, session: Session) -> str:
    """Plain-text morning digest.

    Deterministic template, no model involved. The AI layer may rewrite this
    more readably, but the facts must exist without it (spec §9.3).
    """
    tracked = (
        session.execute(select(Candidate).where(Candidate.status == CandidateStatus.TRACKED))
        .scalars()
        .all()
    )
    shadow = (
        session.execute(select(Candidate).where(Candidate.status == CandidateStatus.SHADOW))
        .scalars()
        .all()
    )

    lines = [
        f"AlphaGraph digest — {report.run_date:%Y-%m-%d}",
        "",
        f"Tracked wallets: {len(tracked)}   Shadow-watched: {len(shadow)}",
        f"New signals: {report.new_signals}",
        f"Signals graded: {report.signals_graded.get('hit', 0)} hit, "
        f"{report.signals_graded.get('miss', 0)} miss, "
        f"{report.signals_graded.get('pending', 0)} pending",
        f"Discovery: {report.discovery.get('surfaced', 0)} surfaced, "
        f"{report.discovery.get('rejected', 0)} rejected",
        f"Lifecycle: {report.lifecycle}",
    ]
    if report.family_precision:
        lines += ["", "Precision by signal family (which families are earning their place):"]
        for family, stats in sorted(
            report.family_precision.items(), key=lambda kv: kv[1]["precision"], reverse=True
        ):
            lines.append(
                f"  • {family}: {stats['precision']:.1%} "
                f"({int(stats['hits'])}/{int(stats['graded'])} graded)"
            )

    if report.proposals:
        lines += [
            "",
            f"Proposals awaiting your approval ({report.variants_tested} variants tested):",
        ]
        for proposal in report.proposals:
            lines.append(
                f"  • {proposal['target']}: {proposal['current']} → {proposal['proposed']} "
                f"(n={proposal['sample_size']}) — {proposal['justification']}"
            )
        lines.append("  Nothing has been applied. Approve explicitly to change behaviour.")
    else:
        lines += ["", f"No tuning proposals: {report.proposal_note or 'no evidence yet'}."]
    return "\n".join(lines)
