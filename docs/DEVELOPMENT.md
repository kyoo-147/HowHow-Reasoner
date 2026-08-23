# Development

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
uv run howhow version
uv run pytest
uv run ruff check .
uv run mypy src
uv run howhow schema export schemas
uv run howhow schema export schemas
uv run python scripts/check.py
```

The JSON files in `schemas/v1/` are generated contract snapshots. Changes require updating them deliberately and passing the schema drift test.

## CI status

`.github/workflows/ci.yml` defines the same locked Python and frontend gates on
Ubuntu and Windows, with least-privilege read-only permissions, cancellation of
superseded runs, and a 20-minute job timeout. It does not upload artifacts or
use repository secrets. Run the deterministic local equivalent with:

```bash
uv sync --locked
uv run python scripts/check_all.py
```

The local command skips tests marked `real_miktex` unless
`--real-miktex` is supplied. Cloud execution is `UNVERIFIED`/`BLOCKED` when
the GitHub account billing lock prevents jobs from starting; a workflow file
alone is not evidence that CI passed.
