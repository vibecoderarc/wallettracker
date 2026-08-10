# ADR 0001 — Modular monolith, not separate services

**Status:** Accepted

## Context

The v1 spec (§6.3) sketched a repository split across `/services/ingestion`,
`/services/enrichment`, `/services/signals`, and so on. That layout implies
separately deployed services with network boundaries between them.

This system has exactly one operator and one workload. Every stage reads the
same Postgres tables, and the analytics are inherently cross-cutting: discovery
reads events, outcomes, and wallet metrics simultaneously and point-in-time.

## Decision

One installable Python package, `alphagraph`, with submodules that mirror the
service names. Module boundaries are enforced by import discipline and the
provider interfaces, not by HTTP.

## Consequences

- A point-in-time query spans ingestion, outcomes, and wallets in one
  transaction. Across service boundaries this would require either distributed
  reads or data duplication, and duplication is where point-in-time correctness
  goes to die.
- Splitting later is mechanical: each submodule already has a narrow entry
  point, and the provider layer is already abstract.
- Cost: nothing prevents a careless import from coupling two modules. The test
  suite and review are the guard.
