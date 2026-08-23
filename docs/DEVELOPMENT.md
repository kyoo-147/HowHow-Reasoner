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

GitHub Actions is temporarily disabled because the repository account is billing-locked; no cloud job steps ran for this PR. TODO: restore `.github/workflows/ci.yml` with the Windows and Ubuntu matrix when billing is fixed. Until then, `uv run python scripts/check.py` is the deterministic cross-platform local check and is the only validation claim made here.
