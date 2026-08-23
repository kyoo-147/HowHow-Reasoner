# HARTH related-work corpus

This is a bounded, reproducible evidence corpus for `harth-calibration-v1`.

- `sources.jsonl` is the existing Crossref metadata slice. Metadata/title spans are `UNVERIFIED`.
- `oa-evidence.jsonl` records legally reachable landing-page provenance, response hashes, licenses, and short quotes. Only quotes from successfully retrieved HTML are `VERIFIED`; provider failures and unresolved locators remain `UNVERIFIED`.
- `oa-retrieval-report.json` records the bounded live run and failures.
- `related-work-matrix.json` is a claim-evidence matrix and deliberately does not claim exhaustive review, novelty, or HARTH-specific calibration results.

Run `uv run python scripts/build_harth_oa_evidence.py --live` for bounded retrieval. The script never writes PDFs, raw HTML, or full-text response bodies. A failed provider must remain visible in the report; do not convert it to an access or evidence claim. `--output` supports isolated test/fixture runs.
