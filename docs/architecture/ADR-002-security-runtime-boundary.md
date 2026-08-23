# ADR-002: deny-by-default runtime boundary

- **Status:** accepted for Phase 0
- **Decision:** contracts record truthful provider identity, readiness, leases, budgets, portable artifact paths, and immutable hashes. Runners are untrusted adapters; they cannot mutate canonical state. Network, secrets, destructive writes, and publication remain explicitly denied unless a later policy grants them.
- **Consequences:** native subprocesses are not claimed as hostile-code isolation. Unknown readiness, ambiguous dispatch, stale fencing, invalid paths, and budget violations fail closed.
