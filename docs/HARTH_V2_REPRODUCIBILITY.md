# HARTH protocol-v2 reproducibility run preparation

This repository contains the protocol and a fail-closed preparation command. It
must not be read as evidence that a real HARTH rerun occurred.

## Frozen inputs

- Protocol: `episodes/harth-calibration/protocol/protocol-v2-proposal.json`
- Run configuration: `episodes/harth-calibration/run-config-v2.json`
- Archive: `episodes/harth-calibration/data/harth.zip` (not committed)
- Engine: `src/howhow/episodes/harth/v2/` (not changed by the preparation layer)

The operator must replace `REQUIRED_OPERATOR_CHECKSUM` in a private run copy (or
supply an equivalent checked-in config change) with the independently verified
archive SHA-256. An unknown checksum is always rejected.

## Safe preflight

Run from the repository root:

```bash
uv run python scripts/harth_v2_run.py --preflight-only \
  --archive episodes/harth-calibration/data/harth.zip \
  --output episodes/harth-calibration/artifacts/v2-run
```

Preflight refuses a dirty working tree, missing or mismatched protocol/config,
missing or mismatched archive, non-empty output directory, invalid frozen seed,
budget/fold/config invariants, and a checkpoint outside the output directory.
On success it writes only `preflight.json`; it never loads the engine and never
produces scientific metrics. The manifest records hashes, exact argv, git
revision/status, environment/tool versions, process identity, budget, and
checkpoint path.

A failed preparation is a failure, not an inconclusive scientific result. The
operator must retain its diagnostic output and correct the input before retrying
with a new empty output directory.

## Real execution gate

`--execute-real` is intentionally unavailable in this preparation-only layer.
Any future execution adapter must require both:

```bash
HOWHOW_RUN_REAL_HARTH=1 uv run python scripts/harth_v2_run.py --execute-real
```

and a current successful preflight. No command without both explicit consents
may invoke real metrics. Synthetic tests exercise refusal and restart/clean
output behavior; they are not HARTH evidence.

## Evidence boundary

`PREFLIGHT PASS` means only that immutable run inputs and safety invariants were
validated. It does not prove archive contents, model performance, calibration,
latency, or any scientific claim. There are no real metrics in this change.
