# HARTH protocol v2 proposal

**Status:** REVISE — protocol adjudication only; no real-data rerun was performed.
**Scope:** This is a preregistration/protocol artifact, not a result and not a claim of novelty.
**Decision date:** 2026-08-23

## Adjudication

Adopt **nested subject-held-out/LOSO evaluation** as the primary design. Retain the already observed S006-only -> S015-S022 run as a **legacy, same-data exploratory analysis** and do not pool it with, select on it, or relabel it as confirmatory evidence. The current S006-only training split is not adequate for fitting or validating temperature scaling: it has one training subject and no independent inner validation subject. Therefore, S006-only is retained only as a diagnostic baseline, and the fixed S015-S022 cohort is a locked sensitivity analysis if it is rerun under v2; neither is the confirmatory design.

The existing `episode.json` and `PROTOCOL.md` establish the dataset, subject-level split intent, nearest-centroid baseline, metrics, and 2,000-resample seed-0 bootstrap. They do not fully specify calibration fitting, clustered uncertainty, ablations, stopping, or multiplicity. v2 resolves those omissions before any real metrics are run.

## Estimand and units

The primary estimand is the expected **per-window** test loss for a new HARTH subject drawn from the finite set of eligible subjects, under the frozen preprocessing/model pipeline:

- NLL (lower is better),
- multiclass Brier score (lower is better), and
- top-label ECE with 10 equal-width bins on [0, 1] (lower is better).

The unit of generalization is the **subject**; the unit at which losses are computed is a complete, non-overlapping window. Window-level observations from one subject are correlated and are never treated as independent experimental replicates for uncertainty. Session/recording identity is retained and reported; if a subject has multiple sessions, all sessions remain in that subject's fold.

The primary summary is the macro-average of subject-level mean losses across outer test subjects. A secondary pooled-window summary is descriptive only. Accuracy, macro-F1, recall, and precision may be reported as secondary classification context, but they do not replace the three preregistered calibration metrics.

## Outer split and alternatives

1. Freeze the eligible subject list, class vocabulary, file manifest, and preprocessing version before opening any result file.
2. Primary: leave one eligible subject out for each outer fold. For fold `s`, train/inner-validate on all eligible subjects except `s`, and evaluate exactly once on `s`.
3. A subject is eligible only if it has valid provenance, at least one complete window, and every required label-field invariant. Exclusion is recorded before metrics; if eligibility changes after inspection, the run is BLOCKED.
4. The prior test subjects S015-S022 remain a fixed, named sensitivity cohort. The prior S006-only training arrangement remains a legacy diagnostic. The legacy metrics already observed are immutable and are not recomputed, tuned against, or used to choose thresholds.
5. If fewer than 2 eligible training subjects remain in any outer fold, temperature scaling is unavailable for that fold and the planned run is REVISE/BLOCKED rather than silently falling back to a different split. The uncalibrated baseline may still be reported as exploratory if all other gates pass.

## Deterministic sampling and full-data policy

Use all eligible raw rows and all complete windows by default; no row or subject quota is allowed in the primary run. Windows are formed independently within subject and session, with the frozen window size and stride recorded in the manifest. A window must not cross a subject or session boundary. If the implementation cannot guarantee session boundaries, stop before metrics.

Input files are sorted by normalized POSIX path; rows are processed in source order; subject and label normalization is deterministic and versioned. No random sampling is used for fitting or prediction. Any resource-bounded subset is a separately named exploratory run with a deterministic seed, subject-stratified quota, and no confirmatory interpretation. Missing/corrupt files, ambiguous IDs, duplicate records, absent classes, or non-deterministic ordering are failures, not reasons to retry with a changed sample.

## Preprocessing and model

Use the existing deterministic CPU nearest-centroid baseline as the sole primary model. For each fold, fit feature scaling, centroids, and every preprocessing statistic on outer-training subjects only. The current six-channel accelerometer representation (per-channel window mean and standard deviation) is frozen unless a protocol amendment is recorded before the run. Do not use test labels, test probabilities, test class frequencies, or test-derived normalization.

The full sensor model uses back and thigh channels. Pre-register exactly two ablations: back-only and thigh-only. Each ablation rebuilds features and refits all preprocessing/model parameters within the same outer folds. No post-hoc channel combinations or sensor selection are permitted.

## Temperature scaling and inner validation

Temperature scaling is a bounded post-hoc intervention applied to logits before softmax. For each outer fold, create deterministic inner subject folds from the outer-training subjects only: leave one inner subject out, fit the nearest-centroid model on the remaining inner subjects, and collect inner-validation logits. Fit one scalar `T` by minimizing inner-validation NLL over `T ∈ [0.05, 20]`, using a deterministic bounded one-dimensional optimizer with fixed tolerance and tie-breaking toward the smaller `T`. Refit the base model on all outer-training subjects, then apply the frozen inner-selected `T` to the outer test subject.

If an inner fold cannot represent the full frozen class vocabulary, calibration is unavailable for that outer fold; record the failure and do not invent labels or fit `T` on outer-test data. The uncalibrated baseline remains a separate estimand. No temperature, threshold, bin count, or ablation is selected from outer-test metrics.

## Uncertainty and comparisons

Use a subject-cluster bootstrap over outer test subjects with 2,000 resamples, seed 0, and percentile 95% intervals. Each resample samples subjects with replacement and includes all windows from each sampled subject; calculate the macro subject loss for each metric. Do not use the existing window-i.i.d. bootstrap as the primary interval. Report the number of subjects and windows per fold.

The primary comparison is calibrated versus uncalibrated nearest-centroid under the same outer folds, assessed separately for NLL, Brier, and ECE. Sensor ablations are secondary comparisons against the full-sensor baseline. Report paired per-subject differences and cluster-bootstrap intervals. Control the three primary metric comparisons with a preregistered Holm correction at family-wise alpha 0.05. Treat ablation comparisons as exploratory, label their raw and adjusted p-values, and do not convert them into a success claim. ECE is descriptive and particularly sensitive to sparse bins; no ECE-only decision can establish calibration.

## Metrics, failure rules, budget, and stopping

Report per-fold and macro subject means for NLL, Brier, ECE, and the number of windows; include the exact 10-bin definition already implemented. Also report class support, missing-class events, optimizer convergence, selected temperatures, and provenance hashes.

Hard failures: leakage; mutable or missing frozen split/config; test-derived preprocessing or calibration; invalid probabilities; incomplete provenance; crossed windows; a changed input after results inspection; non-finite metrics; failed class coverage; or any run exceeding the declared resource/time budget. Preserve the failure record and stop. Do not retry with a new split or silently downgrade a failure.

Budget: one primary nested-LOSO run, one full-sensor fit per outer fold plus the pre-registered back-only and thigh-only ablations, and the bounded inner calibration fits required by the folds. Maximum 22 eligible outer folds, 3 sensor configurations, 2 calibration states, 2,000 cluster-bootstrap resamples per reported configuration, and 30 minutes wall-clock on the declared CPU environment. No early stopping based on test metrics. Stop after all eligible outer folds complete or at the first hard failure; a timeout is a failure. A separate fixed-cohort sensitivity run is allowed only after the primary protocol is frozen and is not used to amend it.

## Interpretation and claim boundary

- **Positive:** only that the preregistered estimator showed lower loss than its paired comparator under the specified finite HARTH subject sample, with uncertainty and multiplicity reported. This is not evidence of deployment performance or universal calibration.
- **Negative:** no improvement was detected under this protocol; do not conclude that calibration never helps.
- **Inconclusive:** insufficient eligible subjects, failed class coverage, wide intervals, unstable calibration, or any preserved failure. Do not convert this into a positive or negative scientific claim.
- Existing S006-only -> S015-S022 metrics, if documented, are prior exploratory same-data observations. Any revised analysis using those same subjects is exploratory and cannot be called confirmatory, regardless of outcome.
- The protocol supports no claim about real-time performance, other datasets, causal sensor importance, clinical utility, novelty, or literature priority. The bounded metadata-only related-work corpus remains UNVERIFIED and is not evidence for those claims.

## Required immutable outputs

For any future real run, preserve the frozen config, input manifest, code/protocol version, leakage checks, per-fold records, failure records, metric JSON, and this proposal's hash. This proposal itself is immutable once the run begins; amendments require a new version and must identify whether they are prospective or post hoc.
