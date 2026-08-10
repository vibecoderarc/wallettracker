"""Address normalization per network.

Networks disagree about case sensitivity, encoding, and length. Comparing a
checksummed EVM address to a lowercase one, or a base58 Solana address to a
truncated display form, silently splits one wallet into two — which would
corrupt every wallet metric downstream.
"""

from __future__ import annotations

import re
from enum import StrEnum

__all__ = ["Network", "display_address", "is_valid_address", "normalize_address"]

_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


class Network(StrEnum):
    SOLANA = "solana"
    BSC = "bsc"
    HYPERLIQUID = "hyperliquid"

    @property
    def is_evm(self) -> bool:
        return self in {Network.BSC}


def is_valid_address(address: str, network: Network) -> bool:
    if not address:
        return False
    if network.is_evm:
        return bool(_EVM_RE.match(address))
    if network is Network.SOLANA:
        return bool(_BASE58_RE.match(address))
    # Hyperliquid identifies accounts by EVM-style address.
    return bool(_EVM_RE.match(address))


def normalize_address(address: str, network: Network) -> str:
    """Return the canonical storage form, raising on anything unrecognised.

    Rejecting is deliberate. An address that cannot be validated should surface
    as an ingestion error, not be stored as a subtly different key.
    """
    candidate = address.strip()
    if not is_valid_address(candidate, network):
        raise ValueError(f"invalid {network} address: {address!r}")
    if network.is_evm or network is Network.HYPERLIQUID:
        # EVM addresses are case-insensitive; lowercase is the canonical form.
        return candidate.lower()
    # Solana base58 IS case-sensitive — lowercasing would corrupt it.
    return candidate


def display_address(address: str, keep: int = 4) -> str:
    if len(address) <= keep * 2 + 3:
        return address
    return f"{address[:keep]}…{address[-keep:]}"
