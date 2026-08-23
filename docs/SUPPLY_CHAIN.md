# Supply-chain audit

Run the reproducible local audit from the repository root:

```console
uv run scripts/audit_supply_chain.py --write --refresh
uv run scripts/audit_supply_chain.py --check
```

`uv.lock` and `pnpm-lock.yaml` are the dependency authorities. The generated
`compliance/dependency-allowlist.json` inventories direct and transitive locked
packages, registry license metadata, lock integrity, and the HARTH CC BY 4.0
attribution assertion. `compliance/THIRD_PARTY_NOTICES.md` is a reviewable public
notice generated from that inventory.

The audit is fail-closed: unknown licenses, GPL/AGPL/non-commercial terms,
missing lock artifact integrity, suspicious tracked filenames/tokens, or
reachable-history secret matches are findings. Registry lookup failures remain
`UNKNOWN`; they are never silently approved. Vulnerability scanning is currently
`BLOCKED` when `pip-audit` and `pnpm audit` are unavailable, and must be supplied
by CI or an operator before treating vulnerability status as complete.

The scanner emits package names, versions, licenses, and public metadata URLs
only. Raw local logs, credentials, downloaded data, and operator evidence are
not written under `compliance/`.
