"""Token amount handling.

Raw integer atomic units are the source of truth. Decimal-scaled values are
derived for display and arithmetic. Float is never used for a quantity that
could be compared for equality or summed across many rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

__all__ = ["TokenAmount", "to_decimal"]


def to_decimal(value: object) -> Decimal:
    """Coerce to Decimal without ever routing through binary float."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # str() first: Decimal(0.1) captures binary float error, Decimal("0.1") does not.
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"not a number: {value!r}") from exc
    raise TypeError(f"cannot convert {type(value).__name__} to Decimal")


@dataclass(frozen=True, slots=True)
class TokenAmount:
    """An amount of a token, preserving the original atomic integer.

    `raw` is what the chain reported. `decimals` is what the mint declares.
    Both are retained so that a later correction to `decimals` does not silently
    invalidate stored quantities.
    """

    raw: int
    decimals: int

    def __post_init__(self) -> None:
        if self.decimals < 0 or self.decimals > 36:
            raise ValueError(f"implausible decimals: {self.decimals}")
        if not isinstance(self.raw, int) or isinstance(self.raw, bool):
            raise TypeError(f"raw must be int, got {type(self.raw).__name__}")

    @classmethod
    def from_decimal(cls, value: object, decimals: int) -> TokenAmount:
        scaled = to_decimal(value) * (Decimal(10) ** decimals)
        return cls(raw=int(scaled.to_integral_value()), decimals=decimals)

    @property
    def value(self) -> Decimal:
        return Decimal(self.raw) / (Decimal(10) ** self.decimals)

    def __add__(self, other: TokenAmount) -> TokenAmount:
        self._assert_same_scale(other)
        return TokenAmount(self.raw + other.raw, self.decimals)

    def __sub__(self, other: TokenAmount) -> TokenAmount:
        self._assert_same_scale(other)
        return TokenAmount(self.raw - other.raw, self.decimals)

    def _assert_same_scale(self, other: TokenAmount) -> None:
        if self.decimals != other.decimals:
            raise ValueError(
                f"refusing to combine amounts with different decimals "
                f"({self.decimals} vs {other.decimals})"
            )

    def __str__(self) -> str:
        return f"{self.value:f}"
