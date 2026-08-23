import json
from pathlib import Path

import typer

from . import __version__
from .contracts import RECORD_TYPES

app = typer.Typer(no_args_is_help=True)
schema_app = typer.Typer()
app.add_typer(schema_app, name="schema")


@app.command()
def version() -> None:
    typer.echo(__version__)


@schema_app.command("list")
def schema_list() -> None:
    for model in RECORD_TYPES:
        typer.echo(model.__name__)


@schema_app.command("export")
def schema_export(directory: Path) -> None:
    target = directory / "v1"
    target.mkdir(parents=True, exist_ok=True)
    for model in RECORD_TYPES:
        (target / f"{model.__name__}.json").write_text(
            json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    typer.echo(str(target))


if __name__ == "__main__":
    app()
