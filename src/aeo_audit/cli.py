"""Command-line interface for aeo-audit."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from aeo_audit import __version__
from aeo_audit.checks.style import check_file
from aeo_audit.config import (
    ConfigError,
    load_author,
    load_company,
    load_engines,
    load_report_config,
)
from aeo_audit.cost import EngineEstimate, estimate_engine, helper_estimate
from aeo_audit.engines.registry import build_engines, load_keys, parse_engine_list
from aeo_audit.llm import ClaudeJSON, JSONModel, LLMError
from aeo_audit.paths import ProjectPaths, validate_slug
from aeo_audit.queries import approve, load_queries, save_queries
from aeo_audit.querygen import QueryGenerationError, TemplateQuestionModel, generate_queries
from aeo_audit.reporting import ReportError, build_report
from aeo_audit.runner import execute, find_or_create_run, latest_complete_run, plan_jobs

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


# ------------------------------------------------------------------ run


def _print_estimate(
    estimates: list[EngineEstimate], helper: tuple[float, float], cap: float
) -> float:
    table = Table(title="Estimated cost for this run", show_lines=False)
    table.add_column("Engine")
    table.add_column("Model")
    table.add_column("Calls", justify="right")
    table.add_column("Estimate (USD)", justify="right")
    table.add_column("Pricing verified")
    for e in estimates:
        table.add_row(
            e.display_name,
            e.model,
            str(e.calls),
            f"${e.low_usd:.2f} to ${e.high_usd:.2f}",
            e.verified,
        )
    table.add_row(
        "Writing and classifier", "Claude", "-", f"${helper[0]:.2f} to ${helper[1]:.2f}", ""
    )
    console.print(table)
    low = float(sum(e.low_usd for e in estimates)) + helper[0]
    high = float(sum(e.high_usd for e in estimates)) + helper[1]
    console.print(f"[bold]Total estimate: ${low:.2f} to ${high:.2f}[/] (budget cap ${cap:.2f})")
    for e in estimates:
        if e.note:
            console.print(f"  [dim]{e.display_name}: {e.note}[/]")
    return high


@app.command()
def run(
    slug: Annotated[str, typer.Argument(help="Company slug, for example acme-treasury.")],
    approve_queries: Annotated[
        bool,
        typer.Option("--approve", help="Approve the questions in companies/<slug>.queries.yaml."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show the plan and cost estimate without making calls."),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
    engines: Annotated[
        str | None,
        typer.Option("--engines", help="Comma-separated engines, for example openai,perplexity."),
    ] = None,
    mock: Annotated[
        bool, typer.Option("--mock", help="Use saved sample answers. No keys, no cost.")
    ] = False,
    new: Annotated[
        bool, typer.Option("--new", help="Start a fresh run instead of resuming an incomplete one.")
    ] = False,
    max_cost: Annotated[
        float | None,
        typer.Option("--max-cost", help="Override the budget cap in config/report.yaml (USD)."),
    ] = None,
    root: RootOption = None,
) -> None:
    """Run every approved question on every available engine, with caching and resume."""
    paths = ProjectPaths.discover(root)
    try:
        company = load_company(slug, paths)
        engines_cfg = load_engines(paths)
        report_cfg = load_report_config(paths)
        qpath = paths.queries_file(slug)
        qf = approve(qpath, slug) if approve_queries else load_queries(qpath)
    except ConfigError as exc:
        raise _config_fail(exc) from exc
    if not qf.is_approved:
        raise _fail(
            f"The questions in {qpath.name} are not approved. Review them, then run:\n"
            f"  aeo-audit run {slug} --approve"
        )
    if mock and not company.fictional:
        raise _fail(
            "Mock mode only works with the fictional sample company, because the saved "
            "answers were written for it. Run without --mock to audit a real company."
        )
    try:
        names = parse_engine_list(engines, list(engines_cfg.engines))
    except ValueError as exc:
        raise _fail(str(exc)) from exc

    fixture_dir = paths.fixtures_dir / "mock" / slug if mock else None
    selection = build_engines(
        engines_cfg, names, keys={} if mock else load_keys(paths.root), mock_fixture_dir=fixture_dir
    )
    for name, reason in selection.skipped.items():
        err_console.print(
            f"[yellow]Skipping {engines_cfg.engines[name].display_name}:[/] {reason}. "
            "It will be listed as not tested."
        )
    if not selection.engines:
        raise _fail("No engines available. Add at least one API key to .env, or use --mock.")

    store, resumed = find_or_create_run(
        paths.company_output_dir(slug), queries_hash=qf.content_hash(), mock=mock, new=new
    )
    existing = store.load_results()
    jobs = plan_jobs(selection.engines, qf.queries, company.runs_per_query, existing)
    total_calls = len(selection.engines) * len(qf.queries) * company.runs_per_query
    cap = max_cost if max_cost is not None else report_cfg.budget_cap_usd
    prior_cost = sum(r.cost_usd or 0.0 for r in existing.values())

    console.print(
        f"[bold]{company.company_name}[/]: {len(qf.queries)} questions x "
        f"{company.runs_per_query} runs x {len(selection.engines)} engines = {total_calls} calls"
        + (" [cyan](mock mode, no cost)[/]" if mock else "")
    )
    if resumed:
        console.print(
            f"Resuming {store.folder.name}: {total_calls - len(jobs)} calls already saved, "
            f"{len(jobs)} to go."
        )

    today = datetime.now(UTC).date()
    by_engine: dict[str, int] = {}
    for j in jobs:
        by_engine[j.engine.name] = by_engine.get(j.engine.name, 0) + 1
    estimates = [
        estimate_engine(e.name, e.settings, by_engine.get(e.name, 0), today)
        for e in selection.engines
    ]
    helper = (0.0, 0.0) if mock else helper_estimate(engines_cfg)
    if mock:
        estimates = [replace(e, low_usd=0.0, high_usd=0.0) for e in estimates]
    high = _print_estimate(estimates, helper, cap)

    if dry_run:
        console.print("[cyan]Dry run: no calls made.[/]")
        return
    if not jobs:
        console.print("Nothing to do: every call is already saved.")
    elif high + prior_cost > cap:
        raise _fail(
            f"The high estimate (${high + prior_cost:.2f}) is above the budget cap (${cap:.2f}). "
            "Reduce questions, runs or engines, or raise the cap with --max-cost."
        )
    elif not yes and not typer.confirm("Start the run?", default=False):
        console.print("Cancelled. No calls made.")
        raise typer.Exit(0)

    manifest = store.manifest() or {
        "slug": slug,
        "company": company.company_name,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "queries_hash": qf.content_hash(),
        "mock": mock,
    }
    manifest.update(
        status="incomplete",
        runs_per_query=company.runs_per_query,
        engines_planned=[e.name for e in selection.engines],
        engines_skipped=selection.skipped,
    )
    store.write_manifest(manifest)

    outcome = _execute_with_progress(jobs, store, engines_cfg, cap, prior_cost)

    final = store.load_results()
    failed = sum(1 for r in final.values() if not r.ok)
    if outcome.budget_stopped:
        err_console.print(
            f"[bold red]Stopped:[/] actual cost reached the ${cap:.2f} cap. "
            "Saved answers are kept. Rerun the same command to finish after raising the cap."
        )
        raise typer.Exit(3)
    store.update_manifest(
        status="complete", completed_at=datetime.now(UTC).isoformat(timespec="seconds")
    )
    console.print(
        f"[green]Run complete:[/] {len(final) - failed} successful, {failed} failed. "
        f"Actual cost: ${sum(r.cost_usd or 0.0 for r in final.values()):.2f}"
    )
    console.print(f"Saved to {store.folder}")
    console.print(f"Next: [bold]aeo-audit report {slug}[/]")


def _execute_with_progress(
    jobs: list[Any], store: Any, engines_cfg: Any, cap: float, prior_cost: float
) -> Any:
    counts: dict[str, int] = {}
    for j in jobs:
        counts[j.engine.name] = counts.get(j.engine.name, 0) + 1
    with Progress(
        TextColumn("{task.description:<18}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[red]{task.fields[failed]} failed"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        tasks = {
            name: progress.add_task(engines_cfg.engines[name].display_name, total=n, failed=0)
            for name, n in counts.items()
        }
        failed: dict[str, int] = dict.fromkeys(counts, 0)

        def on_result(result: Any) -> None:
            if not result.ok:
                failed[result.engine] += 1
            progress.update(tasks[result.engine], advance=1, failed=failed[result.engine])

        return asyncio.run(
            execute(
                jobs,
                store,
                engines_cfg.retry,
                budget_cap_usd=cap,
                prior_cost_usd=prior_cost,
                on_result=on_result,
            )
        )


# ------------------------------------------------------------------ queries


@app.command()
def queries(
    slug: Annotated[str, typer.Argument(help="Company slug, for example acme-treasury.")],
    force: Annotated[
        bool,
        typer.Option("--force", help="Replace an existing questions file, even an approved one."),
    ] = False,
    mock: Annotated[
        bool,
        typer.Option("--mock", help="Use offline templates (fictional sample company only)."),
    ] = False,
    root: RootOption = None,
) -> None:
    """Generate unbranded buyer questions for review. Questions never run until approved."""
    paths = ProjectPaths.discover(root)
    try:
        company = load_company(slug, paths)
        engines_cfg = load_engines(paths)
    except ConfigError as exc:
        raise _config_fail(exc) from exc
    qpath = paths.queries_file(slug)
    if qpath.exists() and not force:
        try:
            existing = load_queries(qpath)
        except ConfigError as exc:
            raise _config_fail(exc) from exc
        if existing.is_approved:
            console.print(
                f"[green]{qpath.name} is already approved[/] and will be reused so results stay "
                "comparable across runs. Use --force to generate new questions."
            )
        else:
            console.print(
                f"{qpath.name} already exists and is waiting for your review. Edit it, then run:\n"
                f"  [bold]aeo-audit run {slug} --approve[/]\n"
                "Use --force to throw it away and generate new questions."
            )
        return

    if mock:
        if not company.fictional:
            raise _fail("--mock only works with the fictional sample company.")
        model: JSONModel = TemplateQuestionModel(company)
        model_name = "offline templates (mock mode)"
    else:
        key = load_keys(paths.root).get(engines_cfg.helpers.api_key_env)
        if not key:
            raise _fail(
                f"Question generation uses Claude. Set {engines_cfg.helpers.api_key_env} in .env, "
                "or write the questions file by hand."
            )
        model = ClaudeJSON(key, engines_cfg.helpers.writer_model)
        model_name = engines_cfg.helpers.writer_model

    console.print(f"Writing {company.query_count} buyer questions for {company.company_name}...")
    try:
        qf = generate_queries(company, model, model_name)
    except (QueryGenerationError, LLMError) as exc:
        raise _fail(str(exc)) from exc
    save_queries(qpath, qf, slug)

    table = Table(title=f"Questions for review ({qpath.name})")
    table.add_column("ID")
    table.add_column("Intent")
    table.add_column("Topic")
    table.add_column("Question")
    for q in qf.queries:
        table.add_row(q.id, q.intent, q.topic or "", q.text)
    console.print(table)
    console.print(
        f"\n[bold yellow]Stop here and review.[/] Edit, replace or delete any question in "
        f"{qpath}.\nWhen you are happy with the list, run:\n"
        f"  [bold]aeo-audit run {slug} --approve[/]"
    )


# ------------------------------------------------------------------ check-text


@app.command("check-text")
def check_text(
    files: Annotated[
        list[Path], typer.Argument(help="Files to check (Markdown, HTML or plain text).")
    ],
) -> None:
    """Check files against the writing rules: no em dashes, no banned hype words."""
    total = 0
    for path in files:
        if not path.exists():
            raise _fail(f"{path} does not exist")
        violations = check_file(path)
        total += len(violations)
        for v in violations:
            err_console.print(f"[red]{v.describe(str(path))}[/]")
    if total:
        raise _fail(f"{total} writing-rule violation(s) found.")
    console.print(f"[green]Writing rules passed[/] for {len(files)} file(s).")


# ------------------------------------------------------------------ report


@app.command()
def report(
    slug: Annotated[str, typer.Argument(help="Company slug, for example acme-treasury.")],
    date: Annotated[
        str | None,
        typer.Option(
            "--date",
            help="Run folder to use, for example 2026-11-17. Defaults to the latest complete run.",
        ),
    ] = None,
    no_pdf: Annotated[bool, typer.Option("--no-pdf", help="Write the HTML report only.")] = False,
    root: RootOption = None,
) -> None:
    """Build metrics.json, findings and the one-page report from saved results. No engine calls."""
    paths = ProjectPaths.discover(root)
    try:
        company = load_company(slug, paths)
        engines_cfg = load_engines(paths)
        report_cfg = load_report_config(paths)
        author = load_author(paths)
        qf = load_queries(paths.queries_file(slug))
    except ConfigError as exc:
        raise _config_fail(exc) from exc
    store = latest_complete_run(paths.company_output_dir(slug), date)
    if store is None:
        where = f"for {date}" if date else "yet"
        raise _fail(f"No complete run {where}. Run: aeo-audit run {slug} --approve")
    console.print(f"Building the report from {store.folder.name}...")
    try:
        out = build_report(
            store=store,
            company=company,
            queries=qf.queries,
            engines_cfg=engines_cfg,
            author=author,
            report_cfg=report_cfg,
            keys={} if store.manifest().get("mock") else load_keys(paths.root),
            make_pdf=not no_pdf,
        )
    except ReportError as exc:
        err_console.print(f"[bold red]Report stopped:[/] {exc}")
        for p in exc.problems:
            err_console.print(f"  - {p}")
        raise typer.Exit(4) from exc
    console.print(f"[green]metrics.json[/]  {out.metrics_path}")
    console.print(f"[green]report.html[/]   {out.html_path}")
    if out.pdf_path:
        console.print(f"[green]report.pdf[/]    {out.pdf_path} ({out.pdf_pages} page)")
    elif out.pdf_note:
        err_console.print(f"[yellow]PDF skipped:[/] {out.pdf_note}")
    for w in out.warnings:
        err_console.print(f"[yellow]Warning:[/] {w}")
    console.print(
        f"Findings verified against metrics.json in {out.insights.attempts} attempt(s). "
        "Writing rules passed."
    )


@app.command("all")
def run_all(
    slug: Annotated[str, typer.Argument(help="Company slug, for example acme-treasury.")],
    approve_queries: Annotated[
        bool, typer.Option("--approve", help="Approve the questions file.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
    engines: Annotated[
        str | None, typer.Option("--engines", help="Comma-separated engines.")
    ] = None,
    mock: Annotated[
        bool, typer.Option("--mock", help="Use saved sample answers. No keys, no cost.")
    ] = False,
    no_pdf: Annotated[bool, typer.Option("--no-pdf", help="Write the HTML report only.")] = False,
    root: RootOption = None,
) -> None:
    """Run every approved question, then build the report."""
    run(
        slug,
        approve_queries=approve_queries,
        dry_run=False,
        yes=yes,
        engines=engines,
        mock=mock,
        new=False,
        max_cost=None,
        root=root,
    )
    report(slug, date=None, no_pdf=no_pdf, root=root)
