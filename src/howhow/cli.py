import json
from pathlib import Path

import typer

from . import __version__
from .contracts import RECORD_TYPES
from .ledger import EventStore, rebuild
from .ledger.replay import write_projection
from .project import init_project, open_project
from .publication import BuildConfig, PackageBuilder, PackageValidationError

app = typer.Typer(no_args_is_help=True)
schema_app = typer.Typer()
event_app = typer.Typer()
package_app = typer.Typer()
literature_app = typer.Typer()
app.add_typer(literature_app, name="literature")
app.add_typer(literature_app, name="literature")

harth_app = typer.Typer()
app.add_typer(harth_app, name="harth")


@harth_app.command("v2")
def harth_v2(
    input_file: Path = typer.Argument(..., exists=True, readable=True),  # noqa: B008
    classes: list[str] = typer.Option(..., "--class", help="Frozen class vocabulary."),  # noqa: B008
    checkpoint: Path | None = typer.Option(None, "--checkpoint"),  # noqa: B008
    protocol_file: Path | None = typer.Option(None, "--protocol-file"),  # noqa: B008
) -> None:
    """Run HARTH protocol-v2 on explicitly supplied (normally synthetic) windows."""
    from .episodes.harth.v2 import run_protocol

    payload = json.loads(input_file.read_text(encoding="utf-8"))
    records = payload["windows"] if isinstance(payload, dict) else payload
    result = run_protocol(records, classes, checkpoint=checkpoint, protocol_file=protocol_file)
    typer.echo(json.dumps(result.to_dict(), sort_keys=True))


@literature_app.command("export-harth")
def literature_export_harth(
    output: Path = typer.Option(  # noqa: B008
        Path("episodes/harth-calibration/literature/sources.jsonl"), "--output"
    ),
    cache_dir: Path | None = typer.Option(None, "--cache-dir"),  # noqa: B008
    provider: list[str] = typer.Option(["crossref", "openalex"], "--provider"),  # noqa: B008
    limit: int = typer.Option(3, min=1, max=10),
    live: bool = typer.Option(False, "--live", help="Use bounded network retrieval."),
) -> None:
    """Export bounded HARTH metadata and exact metadata spans as portable JSONL."""
    from .providers.literature.export import export_harth

    result = export_harth(
        output, providers=tuple(provider), limit=limit, cache_dir=cache_dir, live=live
    )
    typer.echo(json.dumps(result, sort_keys=True))


app.add_typer(schema_app, name="schema")
app.add_typer(event_app, name="event")
app.add_typer(package_app, name="package")


@app.command()
def serve(
    host: str = typer.Option(
        "127.0.0.1", help="Bind address; non-loopback requires --allow-non-loopback."
    ),
    port: int = typer.Option(8787, min=1, max=65535),
    project_root: Path | None = typer.Option(None, "--project-root"),  # noqa: B008
    allow_non_loopback: bool = typer.Option(False, "--allow-non-loopback"),
) -> None:
    """Run the local control-plane API with loopback-only defaults."""
    import uvicorn

    if host not in {"127.0.0.1", "localhost", "::1"} and not allow_non_loopback:
        raise typer.BadParameter("non-loopback binding requires --allow-non-loopback")
    from .api import create_app

    uvicorn.run(create_app(project_root=project_root or Path.cwd()), host=host, port=port)


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


@package_app.command("build")
def package_build(
    source: Path,
    output: Path,
    latex: Path | None = typer.Option(None, help="Explicit LaTeX executable path."),  # noqa: B008
    bibtex: Path | None = typer.Option(None, help="Explicit BibTeX executable path."),  # noqa: B008
    biber: Path | None = typer.Option(None, help="Explicit Biber executable path."),  # noqa: B008
    timeout: int = typer.Option(120, min=1, help="Per-process timeout in seconds."),
    evidence_reviewed: bool = typer.Option(False, help="Evidence gate passed."),
    human_reviewed: bool = typer.Option(False, help="Human review gate passed."),
    reproducible: bool = typer.Option(False, help="Reproducibility gate passed."),
) -> None:
    """Build a clean package; never submits it."""
    try:
        result = PackageBuilder(BuildConfig(latex, bibtex, biber, timeout)).build(
            source,
            output,
            evidence_reviewed=evidence_reviewed,
            human_reviewed=human_reviewed,
            reproducible=reproducible,
        )
    except Exception as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("READY FOR HUMAN REVIEW" if result.ready else "PACKAGING")
    typer.echo(str(output.resolve()))


@package_app.command("check")
def package_check(output: Path) -> None:
    """Verify package artifacts and checksums without compiling."""
    try:
        checks = PackageBuilder.check(output)
    except PackageValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(checks, sort_keys=True))


workflow_app = typer.Typer()
app.add_typer(workflow_app, name="workflow")


@workflow_app.command("demo")
def workflow_demo(
    path: Path, project_id: str = typer.Option("fixture-episode", "--project-id")
) -> None:
    """Run the local deterministic demo; all results are labelled FIXTURE."""
    from .workflows import FixtureDemo

    snapshot = FixtureDemo.run(path, project_id=project_id)
    typer.echo(
        json.dumps(
            {"label": "FIXTURE", "state": snapshot.state.value, "project_id": snapshot.project_id},
            sort_keys=True,
        )
    )


@workflow_app.command("status")
def workflow_status(path: Path) -> None:
    """Rebuild and print workflow state from the event ledger."""
    from .workflows import EpisodeWorkflow

    snapshot = EpisodeWorkflow(path).snapshot
    typer.echo(
        json.dumps(
            {
                "state": snapshot.state.value,
                "project_id": snapshot.project_id,
                "tasks": snapshot.tasks,
                "completed": snapshot.completed,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    app()
