"""Outcome types — the labels everything else is scored against (spec §3).

Without these, a wallet has a history but no track record.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alphagraph.core.events import AssetRef
from alphagraph.core.timeutil import ensure_utc


class OutcomeClass(StrEnum):
    PRICE_RUN = "price_run"
    CEX_LISTING = "cex_listing"
    REVIVAL = "revival"
    RUG_OR_COLLAPSE = "rug_or_collapse"
    PUMP_EVENT = "pump_event"


#: Outcome classes that count as "this wallet was right" for predictive scoring.
#: RUG_OR_COLLAPSE is excluded on purpose: being early into something that died
#: is not skill, and lumping it in would rank rug-pullers as informed traders.
PREDICTIVE_CLASSES = frozenset(
    {OutcomeClass.PRICE_RUN, OutcomeClass.CEX_LISTING, OutcomeClass.REVIVAL}
)


class Outcome(BaseModel):
    """Something notable that happened to an asset.

    `event_time` is when it occurred; `first_observed_at` is when this system
    could have known. Every point-in-time query must filter on the latter.
    """

    model_config = ConfigDict(frozen=True)

    outcome_id: str
    outcome_class: OutcomeClass
    definition_version: str

    asset: AssetRef
    event_time: datetime
    first_observed_at: datetime

    # t0: the moment the move became public. Discovery rewinds from here.
    t0: datetime

    magnitude: Decimal | None = None  # e.g. 5.0 for a 5x run
    venue: str | None = None  # e.g. "robinhood" for a listing
    evidence: dict[str, str] = Field(default_factory=dict)

    @field_validator("event_time", "first_observed_at", "t0")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @property
    def is_predictive(self) -> bool:
        return self.outcome_class in PREDICTIVE_CLASSES
