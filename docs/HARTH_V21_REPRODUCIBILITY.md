# HARTH protocol-v2.1 synthetic integration

The executable integration is synthetic-only and publishes a quarantine marker. It
loads a generated fixture through the existing v2 loader, runs the existing nested
LOSO engine, then applies the v2.1 support/schema contract and truthful generator:

```sh
uv run python scripts/harth_v21_run.py \
  --synthetic-fixture complete \
  --output /tmp/howhow-harth-v21
```

Other deterministic fixtures are `zero-support`, `incomplete-family`,
`schema-failure`, `timeout`, and `dirty-identity`. The command refuses a non-empty
output directory, has no resume/retry path, and never opens `harth.zip`. Its output
is structural synthetic evidence only; it is not a scientific run or performance
claim. stdout is deliberately limited to a pass/block status line.

The v2.1 artifact binds protocol, schema, config, code, input, vocabulary,
eligibility, and pairing hashes. Failure artifacts preserve the stage and error
without publishing result metrics.
