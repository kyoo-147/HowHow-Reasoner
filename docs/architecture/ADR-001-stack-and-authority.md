# ADR-001: Python contracts and canonical authority

- **Status:** accepted for Phase 0
- **Decision:** Python 3.12, Pydantic v2, Typer, and standard JSON/filesystem primitives define the provider-neutral contract boundary. HowHow owns durable records, approvals, budgets, evidence, manifests, gates, and event history.
- **Consequences:** external runners and future web clients return typed records and never become a second source of truth. SQLite, workflow engines, and UI are deferred until a measured need exists.
