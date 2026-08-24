# Supply-chain audit

Run the reproducible local audit from the repository root:

```console
uv run scripts/audit_supply_chain.py --write --refresh
uv run scripts/audit_supply_chain.py --check
```

`uv.lock` and `pnpm-lock.yaml` are the dependency authorities. The generated
`compliance/dependency-allowlist.json` inventories direct and transitive locked
package metadata separately from `artifacts`, which is derived from the exact
artifact URLs and SHA-256 values in `uv.lock`. The generated notice renders
single-line package summaries and artifact-scoped provenance; it does not imply
that every lockfile platform artifact is redistributed by HowHow.
`compliance/numpy-notices/` contains verbatim NumPy 2.3.2 upstream attachments.
The generated NumPy section maps sdist/source notices and each wheel platform
tag explicitly, including the Windows `.dll` notice. HARTH CC BY 4.0 remains a
separate data attribution, and HowHow stays `UNKNOWN` until an owner decision.

The audit is fail-closed: unknown licenses, GPL/AGPL/non-commercial terms,
missing lock artifact integrity, suspicious tracked filenames/tokens, reachable-
history secret matches, vulnerability findings, and scanner failures are
findings. Registry lookup failures remain `UNKNOWN`; they are never silently
approved. Use `--scan` to run `uvx pip-audit --format json` and `pnpm audit
--json`; each result is retained as structured evidence with `PASS`, `FINDINGS`,
or `SCANNER_FAILURE` status. Library calls report scanning as `NOT_APPLICABLE`
instead of pretending a scan ran.

The scanner emits package names, versions, licenses, public metadata URLs, and
structured vulnerability results only. Raw local logs, credentials, downloaded
data, and operator evidence are not written under `compliance/`.
