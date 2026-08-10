"""Alert policy: dedupe, cooldown, risk gating, delivery.

Two rules here are load-bearing and should not be relaxed for convenience:

1. A critical risk cannot be hidden by a high significance score. The two are
   independent dimensions, and a signal that is both interesting and dangerous
   must present as both.
2. Shadow mode is the default. Nothing leaves the machine until the operator
   explicitly enables live alerts after reviewing shadow output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphagraph.config import Settings, get_settings
from alphagraph.db.models import SignalRow
from alphagraph.providers.base import NotificationProvider
from alphagraph.signals.engine import Family, Signal

CRITICAL_RISK_FAMILIES = {Family.PUMP_OPERATOR}


@dataclass(frozen=True, slots=True)
class AlertPolicy:
    min_significance: float = 0.35
    cooldown: timedelta = timedelta(hours=6)
    max_per_run: int = 25
    quiet_hours_utc: tuple[int, int] | None = None  # (start, end)

    def in_quiet_hours(self, at: datetime) -> bool:
        if self.quiet_hours_utc is None:
            return False
        start, end = self.quiet_hours_utc
        hour = at.hour
        return start <= hour < end if start <= end else hour >= start or hour < end


@dataclass
class DispatchResult:
    persisted: int = 0
    suppressed_duplicate: int = 0
    suppressed_cooldown: int = 0
    suppressed_threshold: int = 0
    delivered: int = 0
    withheld_shadow: int = 0
    risk_flagged: int = 0

    def as_dict(self) -> dict[str, int]:
        return self.__dict__.copy()


def sanitize_for_notification(text: str) -> str:
    """Neutralise clickable links in outbound alert text.

    Token metadata and social content are attacker-controlled. A notification
    that renders an unverified URL as a live link turns our own alert into a
    phishing delivery mechanism (spec §14).
    """
    return (
        text.replace("http://", "hxxp://")
        .replace("https://", "hxxps://")
        .replace("\n", " ")
        .strip()
    )


class AlertDispatcher:
    def __init__(
        self,
        session: Session,
        notifier: NotificationProvider,
        policy: AlertPolicy | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.notifier = notifier
        self.policy = policy or AlertPolicy()
        self.settings = settings or get_settings()

    def _recent_same_subject(self, signal: Signal) -> SignalRow | None:
        """Most recent signal about the SAME subject within the cooldown.

        The subject is (family, asset, wallet). Matching on family and asset
        alone collapses every wallet-scoped signal that has no asset — they all
        share asset_id NULL — so a batch of distinct new-candidate alerts would
        suppress each other.
        """
        window_start = signal.triggered_at - self.policy.cooldown
        stmt = select(SignalRow).where(
            SignalRow.family == signal.family,
            SignalRow.triggered_at >= window_start,
            SignalRow.triggered_at <= signal.triggered_at,
        )
        stmt = stmt.where(
            SignalRow.asset_id.is_(None)
            if signal.asset_id is None
            else SignalRow.asset_id == signal.asset_id
        )
        stmt = stmt.where(
            SignalRow.wallet.is_(None)
            if signal.wallet is None
            else SignalRow.wallet == signal.wallet
        )
        return self.session.execute(
            stmt.order_by(SignalRow.triggered_at.desc()).limit(1)
        ).scalar_one_or_none()

    def _assess_risk(self, signal: Signal) -> str:
        if signal.family in CRITICAL_RISK_FAMILIES:
            return "critical"
        if signal.risks:
            return "elevated"
        return "normal"

    async def dispatch(self, signals: list[Signal]) -> DispatchResult:
        result = DispatchResult()
        shadow = not self.settings.notifications_enabled

        for signal in signals[: self.policy.max_per_run]:
            key = signal.dedupe_key()
            # Look up by dedupe_key only. `session.get()` takes a PRIMARY KEY,
            # and this table's primary key is an integer id — passing the string
            # key made SQLite quietly return None while Postgres rejected the
            # query outright.
            if self.session.execute(
                select(SignalRow).where(SignalRow.dedupe_key == key)
            ).scalar_one_or_none():
                result.suppressed_duplicate += 1
                continue

            risk_band = self._assess_risk(signal)
            signal.risk_band = risk_band
            if risk_band == "critical":
                result.risk_flagged += 1

            below_threshold = signal.significance < self.policy.min_significance
            # A critical-risk signal is persisted and delivered even when it
            # scores below the interest threshold. Suppressing a warning because
            # it was not exciting enough is precisely the wrong failure mode.
            if below_threshold and risk_band != "critical":
                result.suppressed_threshold += 1
                continue

            recent = self._recent_same_subject(signal)
            if recent is not None and risk_band != "critical":
                result.suppressed_cooldown += 1
                continue

            row = SignalRow(
                dedupe_key=key,
                family=signal.family,
                definition_version="v1",
                triggered_at=signal.triggered_at,
                evidence_cutoff=signal.evidence_cutoff,
                asset_id=signal.asset_id,
                wallet=signal.wallet,
                entity_id=signal.entity_id,
                title=signal.title[:240],
                significance=signal.significance,
                confidence=signal.confidence,
                risk_band=risk_band,
                supporting_facts=signal.supporting_facts,
                risks=signal.risks,
                unknowns=signal.unknowns,
                base_rate_context=signal.base_rate_context,
                feature_vector=signal.feature_vector,
                evidence_ids=signal.evidence_ids,
                shadow_mode=shadow,
                grade="pending",
            )
            self.session.add(row)
            result.persisted += 1

            if shadow:
                result.withheld_shadow += 1
                continue
            if self.policy.in_quiet_hours(signal.triggered_at) and risk_band != "critical":
                continue

            body = sanitize_for_notification(
                " | ".join(signal.supporting_facts[:3]) or "No supporting facts recorded"
            )
            sent = await self.notifier.send(
                title=sanitize_for_notification(signal.title),
                body=body,
                url=None,
            )
            if sent:
                row.delivered = True
                result.delivered += 1

        self.session.flush()
        return result
