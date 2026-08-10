# ADR 0004 — The nightly loop proposes; it never applies

**Status:** Accepted

## Context

The requirement was "an AI or bot constantly improving and checking daily the
whole setup and improve over time based on the results". The naive
implementation re-tunes thresholds every night against recent outcomes.

With a few dozen graded signals, nightly re-tuning fits noise. The system would
show steadily improving historical metrics while its live performance decayed —
and the improving metrics would be the reason nobody noticed.

## Decision

1. No language model participates in tuning. The nightly module calls none.
2. Threshold changes are written as `Proposal` rows with status `pending`.
   Applying one requires an explicit operator action.
3. Every proposal records `variants_tested` and `sample_size`. A minimum of 20
   graded signals is required before any proposal is generated at all.
4. Per-family precision is reported whether or not a proposal follows — "which
   family is generating the noise" is usually more actionable than a threshold
   tweak.

## Consequences

- Improvement is slower and requires attention. That is the intended trade.
- Multiple-testing exposure is visible in the UI rather than hidden.
- The loop still does the labour: grading, cohort maintenance, discovery
  sweeps, and the morning digest all run unattended.
