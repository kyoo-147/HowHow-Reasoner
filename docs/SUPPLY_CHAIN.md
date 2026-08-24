# Supply-chain audit

Run the reproducible local audit from the repository root:

```console
uv run scripts/audit_supply_chain.py --write --refresh
uv run scripts/audit_supply_chain.py --check
```

`uv.lock` and `pnpm-lock.yaml` are the dependency authorities. The generated
`compliance/dependency-allowlist.json` inventories direct and transitive locked
packages, registry and shipped-distribution license metadata, lock integrity,
vulnerability scan results, and the HARTH CC BY 4.0 attribution assertion.
`compliance/THIRD_PARTY_NOTICES.md` is a reviewable public notice generated from
that inventory.

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
