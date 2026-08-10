# ADR 0003 — Profit and loss is never an input to wallet ranking

**Status:** Accepted

## Context

The obvious way to rank wallets is by how much money they make. The v1 spec did
exactly this (§10.3) and additionally recommended excluding suspected insiders
from copy cohorts (§10.2).

The episode that motivated this product falsifies that approach. The entity at
its centre had touched 25 assets that later appeared on Robinhood, and it was
*unprofitable* — it round-tripped its position and was eventually liquidated. It
would have been ranked poorly or filtered out entirely.

## Decision

Wallet ranking uses predictive metrics only: hit rate against outcome classes,
shrunk toward the population base rate, with independence testing and minimum
samples. Realized P&L is computed, stored, and displayed as context, and is
never an input to promotion, demotion, or archetype classification.

## Consequences

- The system can surface a wallet that predicts well and trades badly, which is
  precisely the wallet worth watching.
- A profitable wallet with no predictive edge — a market maker, an arbitrage
  bot — is not promoted merely for being profitable.
- The UI must state this explicitly wherever P&L appears, or users will assume
  the ranking used it. `/v1/wallets/{address}` carries that note in its payload.
