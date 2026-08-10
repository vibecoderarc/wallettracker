"""Idempotency keys for canonical events.

Consumers are at-least-once; every write must therefore be safe to repeat.
The key is derived from the chain coordinates that uniquely locate an event, so
replaying a block range produces the same keys and no duplicate rows.
"""

from __future__ import annotations

import hashlib

__all__ = ["content_hash", "event_key"]


def event_key(
    *,
    network: str,
    transaction_id: str,
    event_type: str,
    event_index: int,
    semantic_index: int = 0,
) -> str:
    """Stable identity for one canonical event.

    `event_index` locates the log/instruction within the transaction.
    `semantic_index` disambiguates several canonical events derived from the
    same log — e.g. a swap that produces both a buy and a sell leg.

    Deliberately excludes: block hash (changes on reorg, but it is the same
    event), observation time, provider, and parser version. Including any of
    those would make replay produce duplicates, which is the exact failure this
    function exists to prevent.
    """
    if event_index < 0 or semantic_index < 0:
        raise ValueError("indices must be non-negative")
    parts = (network, transaction_id, event_type, str(event_index), str(semantic_index))
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
    return f"evt_{digest[:32]}"


def content_hash(payload: str | bytes) -> str:
    data = payload.encode() if isinstance(payload, str) else payload
    return hashlib.sha256(data).hexdigest()
