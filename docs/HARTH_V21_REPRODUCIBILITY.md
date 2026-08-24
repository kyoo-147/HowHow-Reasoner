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

The authorization record is an exact JSON object containing `authorization_version`,
`protocol_version`, `allow_rerun: true`, `one_shot: true`, the independent hashes for
archive/protocol/config/schema/code/vocabulary, fixed budgets, canonical destination,
current git revision, and the frozen vocabulary. The consent environment variable is
required in addition to that record. Preflight rejects stale or current-head/code,
tracked-dirty, protocol/config/schema/archive/vocabulary/budget, and destination
mismatches before archive loading. It never resumes or retries and requires an empty
publication directory.

Loader, v2 engine, v2.1 aggregation/validation, timeout, quarantine, and immutable
atomic publication are owned by one `RunGuard`. Failure artifacts are metrics-free;
stdout contains only an operational pass/block line, never metrics. Real outputs remain
quarantined and make no performance claim. No real HARTH archive or checkpoint is
included in this repository or required by the test suite.
