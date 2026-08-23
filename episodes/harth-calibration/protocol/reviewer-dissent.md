# Reviewer dissent — HARTH protocol v2

**Reviewer position:** REVISE, with reservations.

## Main objections

1. **LOSO is statistically defensible but operationally noisy.** HARTH has a finite and potentially heterogeneous subject pool. A single subject per outer fold can make calibration estimates unstable, especially for ECE and rare classes. The proposal therefore requires subject-cluster intervals and per-subject reporting, but this does not make the estimates precise.
2. **Temperature scaling may be underidentified.** Inner leave-one-subject-out validation can have sparse or missing class support. The protocol correctly refuses test-set fallback, but this may yield an incomplete calibrated result. A failure is preferable to optimistic calibration, yet the report must not present the uncalibrated and calibrated populations as if they had identical fold availability without disclosure.
3. **ECE is an unstable decision metric.** Ten fixed bins are required for continuity with v1, but sparse bins and changing confidence distributions make ECE a descriptive metric. It should never be the sole basis for a success decision; the proposal addresses this, but reviewers should enforce it.
4. **The legacy split is not a clean replication target.** S006-only training against S015-S022 has already been observed. Even a perfectly documented rerun on those subjects is not confirmatory. It is useful as a locked sensitivity/legacy comparison only, and any discrepancy must be treated as a provenance or implementation question rather than selectively explained.
5. **Sensor ablation is confounded.** Removing a sensor changes feature dimension, scaling, and potentially class coverage. The proposed within-fold refit makes the comparison fairer but cannot identify causal sensor contribution. Claims must remain predictive and exploratory.
6. **Full-data policy may exceed the current bounded smoke implementation.** The current loader has row, subject, window, and bootstrap bounds and the existing smoke path uses a window-i.i.d. bootstrap. v2 must not be declared implemented merely because this document specifies stronger behavior. A future implementation audit is required before any real run.

## Dissenting recommendation

Do not approve a real-data execution as confirmatory. Approve only a prospective, protocol-conforming exploratory run after the implementation supports session-safe full-data loading, inner subject validation, cluster bootstrap, and immutable manifests. If those gates are not met, mark the run BLOCKED and preserve the failure. The decision remains **REVISE**, not PASS, because v2 is a design correction awaiting implementation and data-free verification.
