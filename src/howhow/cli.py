import json
from pathlib import Path

import typer

from . import __version__
from .contracts import RECORD_TYPES
from .ledger import EventStore, rebuild
from .ledger.replay import write_projection
from .project import init_project, open_project

app = typer.Typer(no_args_is_help=True)
schema_app = typer.Typer()
event_app = typer.Typer()
app.add_typer(schema_app, name="schema")
app.add_typer(event_app, name="event")


@app.command()
def version() -> None:
    typer.echo(__version__)


@app.command()
def init(
    path: Path, project_id: str = typer.Option(..., "--project-id"), name: str | None = None
) -> None:
    """Atomically create a portable filesystem project."""
    typer.echo(str(init_project(path, project_id=project_id, name=name).root))


@event_app.command("verify")
def event_verify(path: Path) -> None:
    """Verify the event chain and print its record count."""
    typer.echo(str(EventStore(open_project(path)).verify()))


@event_app.command("rebuild")
def event_rebuild(path: Path) -> None:
    """Replay events into the canonical projection."""
    typer.echo(str(write_projection(path, rebuild(path))))


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
