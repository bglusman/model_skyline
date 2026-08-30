from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from model_skyline.engine import FrontierEngine
from model_skyline.formula import compile_formula
from model_skyline.io import (
    InputError,
    dump_json,
    generated_schemas,
    load_catalog,
    load_config,
    load_frontier_snapshot,
    public_schemas,
)
from model_skyline.renderers import render_csv, render_rss, render_table
from model_skyline.selection import select_models
from model_skyline.traces import TraceAggregationError, aggregate_traces, enrich_catalog
from model_skyline.version import VERSION

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Build workload-specific model-offering Pareto frontiers.",
)


class OutputFormat(StrEnum):
    TABLE = "table"
    JSON = "json"
    CSV = "csv"
    RSS = "rss"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(VERSION)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True),
    ] = False,
) -> None:
    """Build and publish workload-specific model skylines."""


def _as_of(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        result = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("--as-of must be an ISO 8601 timestamp") from exc
    if result.tzinfo is None:
        raise ValueError("--as-of must include a timezone")
    return result


def _emit(value: str, output: Path | None) -> None:
    if output is None:
        typer.echo(value, nl=False)
    else:
        output.write_text(value, encoding="utf-8")


def _error(exc: Exception) -> None:
    typer.echo(f"error: {exc}", err=True)
    raise typer.Exit(code=2)


@app.command()
def validate(
    config: Annotated[Path, typer.Argument(exists=True, readable=True)],
    catalog: Annotated[Path, typer.Argument(exists=True, readable=True)],
) -> None:
    """Validate configuration, formulas, and canonical observations."""

    try:
        loaded_config = load_config(config)
        loaded_catalog = load_catalog(catalog)
        for metric in loaded_config.metrics.values():
            if metric.kind == "formula":
                compile_formula(metric.expression)
        workload = loaded_config.workloads.get(loaded_catalog.workload.id)
        if (
            workload is None
            or workload.version != loaded_catalog.workload.version
            or workload.unit != loaded_catalog.workload.unit
        ):
            raise ValueError("catalog workload does not match a configured workload")
        typer.echo(
            f"valid: {len(loaded_config.frontiers)} frontiers, "
            f"{len(loaded_config.metrics)} metrics, "
            f"{len(loaded_catalog.offerings)} offerings"
        )
    except (InputError, ValueError) as exc:
        _error(exc)


@app.command()
def evaluate(
    config: Annotated[Path, typer.Argument(exists=True, readable=True)],
    catalog: Annotated[Path, typer.Argument(exists=True, readable=True)],
    frontier: Annotated[str, typer.Argument(help="frontier id in the configuration")],
    output_format: Annotated[OutputFormat, typer.Option("--format", "-f")] = OutputFormat.TABLE,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    as_of: Annotated[str | None, typer.Option("--as-of")] = None,
    previous: Annotated[
        Path | None,
        typer.Option(
            "--previous", exists=True, readable=True, help="previous snapshot for RSS diff"
        ),
    ] = None,
    link: Annotated[str | None, typer.Option("--link", help="canonical URL for RSS")] = None,
    raw_csv: Annotated[
        bool,
        typer.Option("--raw-csv", help="do not neutralize spreadsheet formula cells"),
    ] = False,
) -> None:
    """Calculate one frontier and render a table, JSON artifact, CSV, or RSS change item."""

    try:
        loaded_config = load_config(config)
        loaded_catalog = load_catalog(catalog)
        snapshot = FrontierEngine().calculate(
            loaded_config,
            loaded_catalog,
            frontier,
            generated_at=_as_of(as_of),
        )
        if output_format is OutputFormat.JSON:
            rendered = dump_json(snapshot)
        elif output_format is OutputFormat.CSV:
            rendered = render_csv(snapshot, spreadsheet_safe=not raw_csv)
        elif output_format is OutputFormat.RSS:
            old_snapshot = load_frontier_snapshot(previous) if previous else None
            rendered = render_rss(snapshot, previous=old_snapshot, link=link)
        else:
            rendered = render_table(snapshot)
        _emit(rendered, output)
    except (InputError, OSError, ValueError) as exc:
        _error(exc)


@app.command()
def select(
    config: Annotated[Path, typer.Argument(exists=True, readable=True)],
    catalog: Annotated[Path, typer.Argument(exists=True, readable=True)],
    selection: Annotated[str, typer.Argument(help="selection id in the configuration")],
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    as_of: Annotated[str | None, typer.Option("--as-of")] = None,
) -> None:
    """Publish an immutable agent default and ordered fallback manifest."""

    try:
        loaded_config = load_config(config)
        loaded_catalog = load_catalog(catalog)
        try:
            definition = loaded_config.selections[selection]
        except KeyError as exc:
            raise ValueError(f"unknown selection {selection!r}") from exc
        frontier_snapshot = FrontierEngine().calculate(
            loaded_config,
            loaded_catalog,
            definition.frontier,
            generated_at=_as_of(as_of),
        )
        snapshot = select_models(loaded_config, frontier_snapshot, selection)
        _emit(dump_json(snapshot), output)
    except (InputError, OSError, ValueError) as exc:
        _error(exc)


@app.command("aggregate-traces")
def aggregate_trace_command(
    catalog: Annotated[Path, typer.Argument(exists=True, readable=True)],
    traces: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    source_id: Annotated[str | None, typer.Option("--source-id")] = None,
) -> None:
    """Add failure- and cache-aware work-unit aggregates from JSONL or Parquet traces."""

    try:
        loaded_catalog = load_catalog(catalog)
        summary = aggregate_traces(
            traces,
            workload=loaded_catalog.workload,
            source_id=source_id,
        )
        enriched = enrich_catalog(loaded_catalog, summary)
        _emit(dump_json(enriched), output)
    except (InputError, OSError, TraceAggregationError, ValueError) as exc:
        _error(exc)


@app.command("export-schemas")
def export_schemas(
    output_directory: Annotated[Path, typer.Argument(file_okay=False)],
) -> None:
    """Export the language-neutral JSON Schemas used at integration boundaries."""

    try:
        output_directory.mkdir(parents=True, exist_ok=True)
        for name, schema in public_schemas().items():
            target = output_directory / name
            target.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
            typer.echo(target)
    except OSError as exc:
        _error(exc)


@app.command("regenerate-schemas", hidden=True)
def regenerate_schemas(
    output_directory: Annotated[Path, typer.Argument(file_okay=False)],
) -> None:
    """Regenerate candidate contracts for maintainers using the locked toolchain."""

    try:
        output_directory.mkdir(parents=True, exist_ok=True)
        for name, schema in generated_schemas().items():
            target = output_directory / name
            target.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
            typer.echo(target)
    except OSError as exc:
        _error(exc)
