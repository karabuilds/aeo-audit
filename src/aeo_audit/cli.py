"""Command-line interface for aeo-audit."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from aeo_audit import __version__
from aeo_audit.config import ConfigError
from aeo_audit.paths import ProjectPaths, validate_slug

app = typer.Typer(
    name="aeo-audit",
    help=(
        "Measure how often a company appears in AI-generated answers compared with its "
        "competitors, and produce a one-page report."
    ),
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

RootOption = Annotated[
    Path | None,
    typer.Option(
        "--root",
        help="Project folder that contains config/ and companies/. Defaults to the current folder.",
        show_default=False,
    ),
]


def _fail(message: str, code: int = 1) -> typer.Exit:
    err_console.print(f"[bold red]Error:[/] {message}")
    return typer.Exit(code)


def _config_fail(exc: ConfigError) -> typer.Exit:
    err_console.print(f"[bold red]Problem with {exc.source}:[/]")
    for problem in exc.problems:
        err_console.print(f"  - {problem}")
    return typer.Exit(2)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"aeo-audit {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show the version."
        ),
    ] = False,
) -> None:
    """aeo-audit command-line tool."""


@app.command()
def init(
    slug: Annotated[
        str, typer.Argument(help="Short name for the company, for example acme-treasury.")
    ],
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
    root: RootOption = None,
) -> None:
    """Create companies/<slug>.yaml from a template for you to fill in."""
    try:
        validate_slug(slug)
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    paths = ProjectPaths.discover(root)
    target = paths.company_file(slug)
    if target.exists() and not force:
        raise _fail(f"{target} already exists. Use --force to overwrite it.")
    template = resources.files("aeo_audit.templates").joinpath("company.yaml").read_text("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(template.replace("{slug}", slug), encoding="utf-8")
    console.print(f"[green]Created[/] {target}")
    console.print(f"Fill in the fields, then run: [bold]aeo-audit queries {slug}[/]")
