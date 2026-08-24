# HARTH v2.1 reproducibility boundary

The executable has two deliberately separate modes. Synthetic mode is a metrics-free
structural test:

```sh
uv run python scripts/harth_v21_run.py --synthetic-fixture complete --output /tmp/howhow-harth-v21
```

The guarded real path is explicit and one-shot:

```sh
HOWHOW_RUN_REAL_HARTH_V21=1 uv run python scripts/harth_v21_run.py \
  --execute-real --archive /absolute/path/harth.zip \
  --authorization /absolute/path/authorization-v2.1.json \
  --output /absolute/path/new-output
```

The authorization record is validated by `schemas/v2.1/Authorization.json` as an exact
JSON object containing a separate `decision_id` and `decision_sha256`,
`allow_rerun: true`, `allow_resume: false`, `allow_retry: false`,
`allow_tuning: false`, `one_shot: true`, and exact `main`/`code`/`protocol`/`config`/
`schema`/`archive`/`vocabulary`/`budgets` hashes. `main`, `protocol`, `config`, and
`schema` are strict current Git object IDs (40 lowercase hex characters); `code`,
`archive`, `vocabulary`, and `budgets` remain SHA-256 hashes (64 lowercase hex
characters). Hashes for tracked artifacts are Git-blob identities (LF-independent),
not checkout bytes. The `main` key is retained for v2.1 compatibility and refers to
the runner script blob; it is not renamed without a coordinated migration. The consent environment variable is
required in addition to that record. Preflight rejects stale or current-head/code,
tracked-dirty, protocol/config/schema/archive/vocabulary/budget, and destination
mismatches before archive loading. It never resumes or retries and requires an empty
publication directory.

Authorization is validated before the destination exists; only then is an exclusive
owned marker created. Loader, v2 engine, v2.1 aggregation/validation, timeout,
quarantine, and immutable atomic publication are owned by one `RunGuard`. Failure
artifacts are metrics-free; stdout contains only an operational pass/block line, never
metrics. Real outputs truthfully record `real_data=true`, `performance_bearing=true`,
`scientific_status=UNVERIFIED`, and `release=false`; they remain quarantined and make
no scientific claim. No real HARTH archive or checkpoint is
included in this repository or required by the test suite.
