# HARTH related-work corpus

This is a bounded, reproducible metadata corpus for `harth-calibration-v1`.

- `sources.jsonl` contains Crossref `SourceRecord` rows and short `EvidenceSpan` rows only.
- `related-work-matrix.json` links the bounded evidence to support, contradiction, unknowns, and candidate gaps.
- `retrieval-report.json` records the retrieval timestamp, provider outcome, and raw-response policy.
- `live-smoke.jsonl` is the pre-existing provider smoke artifact; it is not a complete review.

The retrieval script is `scripts/build_harth_literature.py`. It stores SHA-256 hashes of the raw provider JSON response but never stores provider full text or PDFs. Metadata-only spans remain `UNVERIFIED`; the matrix deliberately does not claim novelty, correctness, or HARTH-specific calibration results.
