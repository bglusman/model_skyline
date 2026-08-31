from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from model_skyline.adapters.aider import (
    DEFAULT_ALLOWED_HOSTS as AIDER_DEFAULT_ALLOWED_HOSTS,
)
from model_skyline.adapters.aider import (
    DEFAULT_SOURCE_URL as AIDER_DEFAULT_SOURCE_URL,
)
from model_skyline.adapters.aider import (
    AiderAdapterError,
    import_aider_polyglot,
    write_aider_import,
)
from model_skyline.adapters.mcpmark import (
    MCPMARK_DEFAULT_ALLOWED_HOSTS,
    MCPMARK_VERIFIED_SHA256,
    MCPMARK_VERIFIED_URL,
    MCPMarkAdapterError,
    build_mcpmark_project_config,
    fetch_mcpmark_catalogs,
    load_mcpmark_catalogs,
    write_mcpmark_import,
)
from model_skyline.adapters.models_dev import (
    MODELS_DEV_API_URL,
    ModelsDevAdapterError,
    load_models_dev_source,
    project_aider_with_models_dev,
    write_models_dev_projection,
)
from model_skyline.engine import FrontierEngine, validate_formula_cost_basis
from model_skyline.formula import compile_formula
from model_skyline.gateway import (
    MAX_GATEWAY_ARTIFACT_BYTES,
    MAX_GATEWAY_ENVELOPE_BYTES,
    parse_gateway_sequence_checkpoint,
    parse_gateway_trust_policy,
    pin_gateway_route,
    verify_gateway_bundle,
)
from model_skyline.io import (
    InputError,
    dump_json,
    generated_schemas,
    load_catalog,
    load_config,
    load_frontier_snapshot,
    public_schemas,
)
from model_skyline.publisher import PublicationError, publish_project
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


def _retrieved_at(value: str | None) -> datetime | None:
    try:
        return _as_of(value)
    except ValueError as exc:
        raise ValueError(str(exc).replace("--as-of", "--retrieved-at")) from exc


def _verification_time(value: str | None) -> datetime | None:
    try:
        return _as_of(value)
    except ValueError as exc:
        raise ValueError(str(exc).replace("--as-of", "--at")) from exc


def _emit(value: str, output: Path | None) -> None:
    if output is None:
        typer.echo(value, nl=False)
    else:
        output.write_text(value, encoding="utf-8")


def _error(exc: Exception) -> None:
    typer.echo(f"error: {exc}", err=True)
    raise typer.Exit(code=2)


def _read_bounded(path: Path, maximum_bytes: int, *, label: str) -> bytes:
    size = path.stat().st_size
    if size > maximum_bytes:
        raise ValueError(f"{label} exceeds {maximum_bytes} bytes")
    payload = path.read_bytes()
    if len(payload) != size:
        raise ValueError(f"{label} changed while it was being read")
    return payload


@app.command()
def validate(
    config: Annotated[Path, typer.Argument(exists=True, readable=True)],
    catalog: Annotated[Path, typer.Argument(exists=True, readable=True)],
) -> None:
    """Validate configuration, formulas, and canonical observations."""

    try:
        loaded_config = load_config(config)
        loaded_catalog = load_catalog(catalog)
        for metric_id, metric in loaded_config.metrics.items():
            if metric.kind == "formula":
                compile_formula(metric.expression)
                validate_formula_cost_basis(metric_id, metric)
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


@app.command("publish-project")
def publish_project_command(
    config: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output_directory: Annotated[Path, typer.Argument(file_okay=False)],
    project_id: Annotated[
        str,
        typer.Option("--project-id", help="portable stable id for this publication root"),
    ],
    catalogs: Annotated[
        list[Path] | None,
        typer.Option(
            "--catalog",
            exists=True,
            readable=True,
            help="observation catalog; repeat for distinct workloads",
        ),
    ] = None,
    frontiers: Annotated[
        list[str] | None,
        typer.Option("--frontier", help="frontier to publish; repeat, or omit for all matches"),
    ] = None,
    selections: Annotated[
        list[str] | None,
        typer.Option(
            "--selection",
            help="selection to publish; repeat, or omit for all matching selections",
        ),
    ] = None,
    as_of: Annotated[str | None, typer.Option("--as-of")] = None,
    base_url: Annotated[
        str | None,
        typer.Option("--base-url", help="artifact URL prefix used for immutable RSS links"),
    ] = None,
    feed_items: Annotated[
        int,
        typer.Option("--feed-items", min=1, max=1000, help="meaningful RSS changes to retain"),
    ] = 20,
    public: Annotated[
        bool,
        typer.Option(
            "--public",
            help="enforce explicit source redistribution authorization and HTTPS links",
        ),
    ] = False,
    allow_licenses: Annotated[
        list[str] | None,
        typer.Option(
            "--allow-license",
            help="source license authorized for public redistribution; repeat",
        ),
    ] = None,
    authorize_sources: Annotated[
        list[str] | None,
        typer.Option(
            "--authorize-source",
            help="source id with separately documented redistribution authority; repeat",
        ),
    ] = None,
) -> None:
    """Publish immutable history, current views, RSS, and agent fallback manifests."""

    try:
        if not catalogs:
            raise ValueError("at least one --catalog is required")
        result = publish_project(
            load_config(config),
            [load_catalog(path) for path in catalogs],
            output_directory,
            project_id=project_id,
            frontier_ids=frontiers,
            selection_ids=selections,
            generated_at=_as_of(as_of),
            base_url=base_url,
            feed_items=feed_items,
            public=public,
            allowed_licenses=allow_licenses or (),
            authorized_source_ids=authorize_sources or (),
        )
        verb = "published" if result.changed else "unchanged"
        typer.echo(
            f"{verb}: publication {result.manifest.publication_id} "
            f"({len(result.manifest.frontiers)} frontiers, "
            f"{len(result.manifest.selections)} selections)"
        )
        typer.echo(result.manifest_path)
    except (InputError, OSError, PublicationError, ValueError) as exc:
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


@app.command("verify-gateway-bundle")
def verify_gateway_bundle_command(
    envelope: Annotated[Path, typer.Argument(exists=True, readable=True)],
    publication: Annotated[Path, typer.Argument(exists=True, readable=True)],
    selection: Annotated[Path, typer.Argument(exists=True, readable=True)],
    trust_policy: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    at: Annotated[
        str | None,
        typer.Option("--at", help="trusted verification time; defaults to current UTC"),
    ] = None,
    checkpoint: Annotated[
        Path | None,
        typer.Option(
            "--checkpoint",
            exists=True,
            readable=True,
            help="optional previously trusted sequence checkpoint",
        ),
    ] = None,
    required_capabilities: Annotated[
        list[str] | None,
        typer.Option(
            "--required-capability",
            help="request capability used only to narrow the signed route; may be repeated",
        ),
    ] = None,
    minimum_headroom_seconds: Annotated[
        int,
        typer.Option("--minimum-headroom-seconds", min=0),
    ] = 0,
) -> None:
    """Statically verify signed bytes and emit a checkpoint, key IDs, and pinned route.

    This command does not install durable anti-rollback state. Pass a trusted
    checkpoint when verifying an update; production admission should use
    ``SignedGatewayResolver`` with a durable installation store.
    """

    try:
        policy = parse_gateway_trust_policy(
            _read_bounded(trust_policy, MAX_GATEWAY_ARTIFACT_BYTES, label="trust policy")
        )
        prior = (
            None
            if checkpoint is None
            else parse_gateway_sequence_checkpoint(
                _read_bounded(checkpoint, MAX_GATEWAY_ENVELOPE_BYTES, label="checkpoint")
            )
        )
        now = _verification_time(at) or datetime.now(UTC)
        verified = verify_gateway_bundle(
            _read_bounded(envelope, MAX_GATEWAY_ENVELOPE_BYTES, label="DSSE envelope"),
            _read_bounded(
                publication,
                min(policy.max_artifact_bytes, MAX_GATEWAY_ARTIFACT_BYTES),
                label="publication",
            ),
            _read_bounded(
                selection,
                min(policy.max_artifact_bytes, MAX_GATEWAY_ARTIFACT_BYTES),
                label="selection",
            ),
            policy,
            now=now,
            checkpoint=prior,
        )
        route = pin_gateway_route(
            verified,
            now=now,
            required_capabilities=required_capabilities or (),
            minimum_headroom=timedelta(seconds=minimum_headroom_seconds),
        )
        result = {
            "checkpoint": verified.checkpoint.model_dump(mode="json"),
            "route": route.model_dump(mode="json"),
            "verified_key_ids": list(verified.authenticated_pointer.verified_key_ids),
        }
        _emit(json.dumps(result, indent=2) + "\n", output)
    except (OSError, ValueError) as exc:
        _error(exc)


@app.command("import-aider-polyglot")
def import_aider_polyglot_command(
    output_directory: Annotated[Path, typer.Argument(file_okay=False)],
    source: Annotated[
        str | None,
        typer.Option(
            "--source",
            help="local YAML path or HTTPS URL; defaults to the pinned official leaderboard",
        ),
    ] = None,
    expected_sha256: Annotated[
        str | None,
        typer.Option("--expected-sha256", help="optional exact source-byte digest"),
    ] = None,
    source_version: Annotated[
        str | None,
        typer.Option("--source-version", help="upstream commit or release identifier"),
    ] = None,
    retrieved_at: Annotated[
        str | None,
        typer.Option(
            "--retrieved-at",
            help="timezone-aware provenance timestamp for a local source",
        ),
    ] = None,
    allow_host: Annotated[
        list[str] | None,
        typer.Option(
            "--allow-host",
            help=("explicit HTTPS hostname allowlist for a custom remote source; may be repeated"),
        ),
    ] = None,
    include_dirty: Annotated[
        bool,
        typer.Option("--include-dirty", help="include runs whose benchmark checkout was dirty"),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="replace the three generated files if present"),
    ] = False,
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout-seconds", min=0.1, max=60.0),
    ] = 20.0,
) -> None:
    """Create a reproducible frontier project from Aider's Polyglot leaderboard."""

    try:
        result = import_aider_polyglot(
            source or AIDER_DEFAULT_SOURCE_URL,
            expected_sha256=expected_sha256,
            source_version=source_version,
            retrieved_at=_retrieved_at(retrieved_at),
            include_dirty=include_dirty,
            timeout_seconds=timeout_seconds,
            allowed_hosts=allow_host or AIDER_DEFAULT_ALLOWED_HOSTS,
        )
        targets = write_aider_import(result, output_directory, overwrite=overwrite)
        typer.echo(
            f"imported {len(result.catalog.offerings)} of {result.rows_seen} Aider rows "
            f"({len(result.rejections)} rejected)"
        )
        for target in targets:
            typer.echo(target)
    except (AiderAdapterError, OSError, ValueError) as exc:
        _error(exc)


@app.command("project-aider-models-dev")
def project_aider_models_dev_command(
    output_directory: Annotated[Path, typer.Argument(file_okay=False)],
    mapping: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, dir_okay=False),
    ],
    aider_source: Annotated[
        str | None,
        typer.Option(
            "--aider-source",
            help="local YAML path or HTTPS URL; defaults to the pinned official leaderboard",
        ),
    ] = None,
    aider_expected_sha256: Annotated[
        str | None,
        typer.Option("--aider-expected-sha256", help="optional exact Aider source digest"),
    ] = None,
    aider_retrieved_at: Annotated[
        str | None,
        typer.Option(
            "--aider-retrieved-at",
            help="timezone-aware provenance timestamp for a local Aider source",
        ),
    ] = None,
    pricing_source: Annotated[
        str,
        typer.Option(
            "--pricing-source",
            help="local compatible JSON path or the exact official models.dev HTTPS URL",
        ),
    ] = MODELS_DEV_API_URL,
    pricing_expected_sha256: Annotated[
        str | None,
        typer.Option("--pricing-expected-sha256", help="optional exact pricing source digest"),
    ] = None,
    pricing_retrieved_at: Annotated[
        str | None,
        typer.Option(
            "--pricing-retrieved-at",
            help="timezone-aware provenance timestamp for a local pricing source",
        ),
    ] = None,
    assert_official_pricing_source: Annotated[
        bool,
        typer.Option(
            "--assert-official-pricing-source",
            help=(
                "assert a hash-pinned local file came from models.dev; the exact official "
                "remote URL is recognized automatically"
            ),
        ),
    ] = False,
    include_dirty: Annotated[
        bool,
        typer.Option("--include-dirty", help="include dirty Aider benchmark checkouts"),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="replace the five generated files if present"),
    ] = False,
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout-seconds", min=0.1, max=60.0),
    ] = 30.0,
) -> None:
    """Combine historical Aider quality with reviewed price-snapshot bindings."""

    try:
        aider = import_aider_polyglot(
            aider_source or AIDER_DEFAULT_SOURCE_URL,
            expected_sha256=aider_expected_sha256,
            retrieved_at=_retrieved_at(aider_retrieved_at),
            include_dirty=include_dirty,
            timeout_seconds=min(timeout_seconds, 60.0),
            allowed_hosts=AIDER_DEFAULT_ALLOWED_HOSTS,
        )
        pricing = load_models_dev_source(
            pricing_source,
            expected_sha256=pricing_expected_sha256,
            retrieved_at=_retrieved_at(pricing_retrieved_at),
            timeout_seconds=timeout_seconds,
            assert_official_source=assert_official_pricing_source,
        )
        result = project_aider_with_models_dev(aider, pricing, mapping)
        targets = write_models_dev_projection(result, output_directory, overwrite=overwrite)
        typer.echo(
            f"projected {len(result.catalog.offerings)} exact Aider/models.dev mappings "
            f"using pricing sha256:{pricing.raw_sha256}"
        )
        for target in targets:
            typer.echo(target)
    except (AiderAdapterError, ModelsDevAdapterError, OSError, ValueError) as exc:
        _error(exc)


@app.command("import-mcpmark-verified")
def import_mcpmark_verified_command(
    output_directory: Annotated[Path, typer.Argument(file_okay=False)],
    source: Annotated[
        str | None,
        typer.Option(
            "--source",
            help="local JSON path or HTTPS URL; defaults to the pinned verified summary",
        ),
    ] = None,
    expected_sha256: Annotated[
        str | None,
        typer.Option(
            "--expected-sha256",
            help="exact source-byte digest; the pinned default is verified automatically",
        ),
    ] = None,
    source_version: Annotated[
        str | None,
        typer.Option("--source-version", help="upstream commit or release identifier"),
    ] = None,
    retrieved_at: Annotated[
        str | None,
        typer.Option(
            "--retrieved-at",
            help="timezone-aware provenance timestamp for a local source",
        ),
    ] = None,
    allow_host: Annotated[
        list[str] | None,
        typer.Option(
            "--allow-host",
            help=("explicit HTTPS hostname allowlist for a custom remote source; may be repeated"),
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="replace generated files if present"),
    ] = False,
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout-seconds", min=0.1, max=60.0),
    ] = 15.0,
) -> None:
    """Create experimental workload projects from MCPMark Verified telemetry."""

    try:
        if source is None:
            catalogs = fetch_mcpmark_catalogs(
                url=MCPMARK_VERIFIED_URL,
                source_version=source_version,
                required_sha256=(
                    MCPMARK_VERIFIED_SHA256 if expected_sha256 is None else expected_sha256
                ),
                timeout_seconds=timeout_seconds,
                allowed_hosts=allow_host or MCPMARK_DEFAULT_ALLOWED_HOSTS,
            )
        elif "://" in source:
            if retrieved_at is not None:
                raise ValueError("--retrieved-at is only available for local MCPMark sources")
            catalogs = fetch_mcpmark_catalogs(
                url=source,
                source_version=source_version,
                required_sha256=expected_sha256,
                timeout_seconds=timeout_seconds,
                allowed_hosts=allow_host or MCPMARK_DEFAULT_ALLOWED_HOSTS,
            )
        else:
            catalogs = load_mcpmark_catalogs(
                source,
                source_version=source_version,
                required_sha256=expected_sha256,
                retrieved_at=_retrieved_at(retrieved_at),
            )
        config = build_mcpmark_project_config(catalogs)
        targets = write_mcpmark_import(
            catalogs,
            config,
            output_directory,
            overwrite=overwrite,
        )
        counts = ", ".join(
            f"{section}={len(catalog.offerings)}" for section, catalog in catalogs.items()
        )
        source_label = (
            "MCPMark Verified"
            if catalogs["overall"].workload.id == "mcpmark-verified-overall"
            else "operator-supplied MCPMark summary"
        )
        typer.echo(f"imported {source_label} telemetry ({counts})")
        for target in targets:
            typer.echo(target)
    except (MCPMarkAdapterError, OSError, ValueError) as exc:
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
