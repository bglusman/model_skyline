from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
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
from model_skyline.adapters.arc_agi import (
    DEFAULT_TIMEOUT_SECONDS as ARC_AGI_DEFAULT_TIMEOUT_SECONDS,
)
from model_skyline.adapters.arc_agi import (
    MAX_TIMEOUT_SECONDS as ARC_AGI_MAX_TIMEOUT_SECONDS,
)
from model_skyline.adapters.arc_agi import (
    ArcAgiAdapterError,
    capture_arc_agi_public_eval,
    write_arc_agi_public_eval_capture,
)
from model_skyline.adapters.codex import CodexAdapterError, adapt_codex_exec_jsonl
from model_skyline.adapters.harbor import (
    HarborAdapterError,
    import_harbor_terminal_bench,
    inspect_harbor_terminal_bench_snapshot,
    write_harbor_terminal_bench_import,
)
from model_skyline.adapters.hermes import (
    HermesAdapterError,
    import_hermes_session,
    load_hermes_session_mapping,
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
from model_skyline.adapters.swe_bench import (
    DEFAULT_MAX_SOURCE_BYTES as SWE_BENCH_DEFAULT_MAX_SOURCE_BYTES,
)
from model_skyline.adapters.swe_bench import (
    HARD_MAX_SOURCE_BYTES as SWE_BENCH_HARD_MAX_SOURCE_BYTES,
)
from model_skyline.adapters.swe_bench import (
    SWE_BENCH_DEFAULT_HARNESS_VERSION,
    SWE_BENCH_WEBSITE_URL,
    SweBenchAdapterError,
    capture_swe_bench,
    write_swe_bench_capture,
)
from model_skyline.arc_feed_monitor import (
    ArcAgiFeedMonitorError,
    inspect_arc_agi_feed,
)
from model_skyline.discovery import (
    DiscoveryError,
    PublishedBenchmarkSignal,
    build_provisional_frontier,
    discover_offerings,
    load_frontier_policies,
)
from model_skyline.engine import FrontierEngine, validate_formula_cost_basis
from model_skyline.feed_monitor import (
    FeedMonitorError,
    github_token_from_environment,
    inspect_swe_bench_feed,
)
from model_skyline.formula import compile_formula
from model_skyline.io import (
    InputError,
    dump_json,
    generated_schemas,
    load_catalog,
    load_config,
    load_frontier_snapshot,
    load_portfolio_derivation,
    load_portfolio_policy,
    load_quality_evidence,
    load_quality_import_report,
    load_quality_reconciliation,
    public_schemas,
)
from model_skyline.models import OfferingKey
from model_skyline.private_output import PrivateOutputError, write_private_text
from model_skyline.publisher import PublicationError, publish_project
from model_skyline.quality_catalog import (
    project_quality_import_report,
    quality_source_reference,
    quality_workload_reference,
)
from model_skyline.quality_evidence import QualityPublicationScope, reconcile_quality_evidence
from model_skyline.quality_portfolio import build_portfolio, verify_portfolio
from model_skyline.renderers import render_csv, render_rss, render_table
from model_skyline.selection import select_models
from model_skyline.traces import TraceAggregationError, aggregate_traces, enrich_catalog
from model_skyline.version import VERSION

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Build workload-specific model-offering Pareto frontiers.",
)

CORE_PANEL = "Core and publication"
TELEMETRY_PANEL = "Telemetry"
DATA_SOURCES_PANEL = "Data sources"
SOURCE_MONITORING_PANEL = "Source monitoring"
QUALITY_EVIDENCE_PANEL = "Quality evidence"
CONTRACTS_PANEL = "Contracts"


class OutputFormat(StrEnum):
    TABLE = "table"
    JSON = "json"
    CSV = "csv"
    RSS = "rss"


_MAX_CLI_ERROR_CHARACTERS = 4_096
_HERMES_IDENTITY_KEY_ENV = "MODEL_SKYLINE_HERMES_IDENTITY_KEY_HEX"
_HERMES_IDENTITY_KEY_HEX_RE = re.compile(r"[0-9A-Fa-f]{64}")


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


def _trace_timestamp(value: str) -> datetime:
    try:
        result = _as_of(value)
    except ValueError as exc:
        raise ValueError(str(exc).replace("--as-of", "--timestamp")) from exc
    if result is None:  # pragma: no cover - Typer requires the option
        raise ValueError("--timestamp is required")
    return result


def _decimal_outcome(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("--work-unit-success must be an exact decimal") from exc


def _hermes_identity_key_from_environment() -> bytes:
    value = os.environ.get(_HERMES_IDENTITY_KEY_ENV)
    if value is None or _HERMES_IDENTITY_KEY_HEX_RE.fullmatch(value) is None:
        raise ValueError(
            f"{_HERMES_IDENTITY_KEY_ENV} must contain exactly 64 hexadecimal characters"
        )
    return bytes.fromhex(value)


def _load_provisional_benchmarks(path: Path | None) -> tuple[PublishedBenchmarkSignal, ...]:
    if path is None:
        return ()
    value = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    if not isinstance(value, list):
        raise ValueError("provisional benchmark file must contain an array")
    return tuple(PublishedBenchmarkSignal.model_validate(item) for item in value)


def _emit(value: str, output: Path | None) -> None:
    if output is None:
        typer.echo(value, nl=False)
    else:
        output.write_text(value, encoding="utf-8")


def _emit_private(value: str, output: Path | None, *, overwrite: bool) -> None:
    if output is None:
        typer.echo(value, nl=False)
    else:
        write_private_text(output, value, overwrite=overwrite)


def _safe_error_message(exc: Exception) -> str:
    """Bound and neutralize exception text before it reaches a terminal or log."""

    try:
        value = str(exc)
    except Exception:  # pragma: no cover - defensive against hostile exception classes
        value = type(exc).__name__
    rendered: list[str] = []
    used = 0
    truncated = False
    for character in value:
        codepoint = ord(character)
        category = unicodedata.category(character)
        is_noncharacter = 0xFDD0 <= codepoint <= 0xFDEF or codepoint & 0xFFFF in {
            0xFFFE,
            0xFFFF,
        }
        if category in {"Cc", "Cf", "Cs"} or is_noncharacter:
            replacement = f"\\u{codepoint:04x}" if codepoint <= 0xFFFF else f"\\U{codepoint:08x}"
        else:
            replacement = character
        if used + len(replacement) > _MAX_CLI_ERROR_CHARACTERS:
            truncated = True
            break
        rendered.append(replacement)
        used += len(replacement)
    if truncated:
        rendered.append("…[truncated]")
    return "".join(rendered)


def _error(exc: Exception) -> None:
    typer.echo(f"error: {_safe_error_message(exc)}", err=True)
    raise typer.Exit(code=2)


def _path_assignments(values: list[str] | None, *, option: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values or ():
        assignment_id, separator, raw_path = value.partition("=")
        if not separator or not assignment_id or not raw_path:
            raise ValueError(f"{option} must use ID=PATH")
        if assignment_id in result:
            raise ValueError(f"{option} repeats id {assignment_id!r}")
        result[assignment_id] = Path(raw_path)
    if not result:
        raise ValueError(f"at least one {option} is required")
    return result


@app.command(rich_help_panel=CORE_PANEL)
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


@app.command("discover", rich_help_panel=CORE_PANEL)
def discover(
    feeds: Annotated[
        list[str] | None, typer.Option("--feed", help="HTTPS RSS/Atom feed URL (repeatable)")
    ] = None,
    include_openrouter: Annotated[bool, typer.Option("--openrouter/--no-openrouter")] = True,
    model_pattern: Annotated[
        str | None, typer.Option("--model-pattern", help="Regex applied to model ids")
    ] = None,
    admission_policy: Annotated[
        str, typer.Option("--admission-policy", help="review, catalog-only, or vendor-reported")
    ] = "review",
    frontier_policy_file: Annotated[
        Path | None,
        typer.Option(
            "--frontier-policy-file",
            exists=True,
            readable=True,
            dir_okay=False,
            help='JSON file: {"frontiers": {"frontier-id": "policy"}}',
        ),
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    review_queue: Annotated[Path | None, typer.Option("--review-queue")] = None,
    provisional_output: Annotated[
        Path | None,
        typer.Option("--provisional-output", help="write the separate day-one frontier artifact"),
    ] = None,
    provisional_benchmarks: Annotated[
        Path | None,
        typer.Option(
            "--provisional-benchmarks",
            exists=True,
            readable=True,
            dir_okay=False,
            help="JSON array of published scores with benchmark and methodology",
        ),
    ] = None,
) -> None:
    """Discover public model offerings and write a provenance-preserving review artifact."""
    try:
        artifact = discover_offerings(
            feeds=feeds or (),
            include_openrouter=include_openrouter,
            model_pattern=model_pattern,
            admission_policy=admission_policy,
            frontier_policies=(
                load_frontier_policies(frontier_policy_file)
                if frontier_policy_file is not None
                else None
            ),
        )
        rendered = artifact.model_dump_json(indent=2) + "\n"
        _emit(rendered, output)
        if review_queue is not None:
            review_queue.write_text(
                json.dumps(artifact.review_queue, indent=2) + "\n", encoding="utf-8"
            )
        if provisional_output is not None:
            provisional = build_provisional_frontier(
                artifact.offerings,
                generated_at=artifact.retrieved_at,
                published_benchmarks=_load_provisional_benchmarks(provisional_benchmarks),
            )
            provisional_output.write_text(
                provisional.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
    except (DiscoveryError, OSError, ValueError) as exc:
        _error(exc)


@app.command(rich_help_panel=CORE_PANEL)
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


@app.command(rich_help_panel=CORE_PANEL)
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


@app.command("publish-project", rich_help_panel=CORE_PANEL)
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


@app.command("aggregate-traces", rich_help_panel=TELEMETRY_PANEL)
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


@app.command("import-codex-exec", rich_help_panel=TELEMETRY_PANEL)
def import_codex_exec_command(
    source: Annotated[
        Path,
        typer.Argument(
            exists=True,
            readable=True,
            dir_okay=False,
            help="private JSONL emitted by exactly one `codex exec --json` turn",
        ),
    ],
    codex_version: Annotated[str, typer.Option("--codex-version")],
    provider: Annotated[str, typer.Option("--provider")],
    model: Annotated[str, typer.Option("--model")],
    offering_id: Annotated[str, typer.Option("--offering-id")],
    timestamp: Annotated[
        str,
        typer.Option("--timestamp", help="timezone-aware observation timestamp"),
    ],
    workload_id: Annotated[str, typer.Option("--workload-id")],
    workload_version: Annotated[str, typer.Option("--workload-version")],
    work_unit_id: Annotated[str, typer.Option("--work-unit-id")],
    result_id: Annotated[
        str,
        typer.Option("--result-id", help="local pseudonymous result identifier"),
    ],
    attempt_id: Annotated[
        str,
        typer.Option("--attempt-id", help="local pseudonymous attempt identifier"),
    ],
    work_unit_success: Annotated[
        str,
        typer.Option("--work-unit-success", help="exact decimal outcome from zero through one"),
    ],
    model_route_attested: Annotated[
        bool,
        typer.Option(
            "--model-route-attested",
            help="attest that provider/model was the turn's only route",
        ),
    ] = False,
    endpoint: Annotated[str | None, typer.Option("--endpoint")] = None,
    billing_mode: Annotated[str | None, typer.Option("--billing-mode")] = None,
    region: Annotated[str | None, typer.Option("--region")] = None,
    service_tier: Annotated[str | None, typer.Option("--service-tier")] = None,
    quantization: Annotated[str | None, typer.Option("--quantization")] = None,
    reasoning_effort: Annotated[str | None, typer.Option("--reasoning-effort")] = None,
    route_details_attested: Annotated[
        bool,
        typer.Option(
            "--route-details-attested",
            help="attest optional route fields that Codex JSONL cannot observe",
        ),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", dir_okay=False, help="write mode-0600 canonical JSONL"),
    ] = None,
) -> None:
    """Project a private Codex JSONL turn into one content-free trace row."""

    try:
        trace = adapt_codex_exec_jsonl(
            source,
            codex_version=codex_version,
            model_route_attested=model_route_attested,
            selected_provider=provider,
            selected_model=model,
            route_details_attested=route_details_attested,
            timestamp=_trace_timestamp(timestamp),
            workload_id=workload_id,
            workload_version=workload_version,
            work_unit_id=work_unit_id,
            offering=OfferingKey(
                offering_id=offering_id,
                model_id=model,
                provider=provider,
                endpoint=endpoint,
                billing_mode=billing_mode,
                region=region,
                service_tier=service_tier,
                quantization=quantization,
                reasoning_effort=reasoning_effort,
                agent_harness="codex",
            ),
            result_id=result_id,
            attempt_id=attempt_id,
            work_unit_success=_decimal_outcome(work_unit_success),
        )
        _emit_private(trace.model_dump_json() + "\n", output, overwrite=False)
    except (CodexAdapterError, PrivateOutputError, OSError, ValueError) as exc:
        _error(exc)


@app.command("import-hermes-session", rich_help_panel=TELEMETRY_PANEL)
def import_hermes_session_command(
    state_database: Annotated[
        Path,
        typer.Argument(
            exists=True,
            readable=True,
            dir_okay=False,
            help="private schema-26 Hermes state database",
        ),
    ],
    mapping_file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            readable=True,
            dir_okay=False,
            help="private operator-reviewed session and exact-route mapping JSON",
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", dir_okay=False, help="write mode-0600 canonical JSONL"),
    ] = None,
) -> None:
    """Project one completed Hermes session into one content-free trace row.

    Set MODEL_SKYLINE_HERMES_IDENTITY_KEY_HEX to a private 32-byte hexadecimal
    pseudonymization key. Key rotation changes the opaque trace identifiers.
    """

    try:
        mapping = load_hermes_session_mapping(mapping_file)
        trace = import_hermes_session(
            state_database,
            mapping=mapping,
            identity_key=_hermes_identity_key_from_environment(),
        )
        _emit_private(trace.model_dump_json() + "\n", output, overwrite=False)
    except (HermesAdapterError, PrivateOutputError, OSError, ValueError) as exc:
        _error(exc)


@app.command("import-aider-polyglot", rich_help_panel=DATA_SOURCES_PANEL)
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


@app.command("project-aider-models-dev", rich_help_panel=DATA_SOURCES_PANEL)
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


@app.command("import-mcpmark-verified", rich_help_panel=DATA_SOURCES_PANEL)
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


@app.command("capture-swe-bench-bash-only", rich_help_panel=DATA_SOURCES_PANEL)
def capture_swe_bench_bash_only_command(
    output_directory: Annotated[Path, typer.Argument(file_okay=False)],
    source: Annotated[
        str | None,
        typer.Option(
            "--source",
            help=("local JSON path or HTTPS URL; defaults to the pinned official website feed"),
        ),
    ] = None,
    expected_sha256: Annotated[
        str | None,
        typer.Option(
            "--expected-sha256",
            help="optional exact source-byte digest; the pinned default is verified",
        ),
    ] = None,
    source_revision: Annotated[
        str | None,
        typer.Option(
            "--source-revision",
            help="required upstream revision for non-default sources",
        ),
    ] = None,
    retrieved_at: Annotated[
        str | None,
        typer.Option(
            "--retrieved-at",
            help="timezone-aware provenance timestamp; required for a local source",
        ),
    ] = None,
    harness_version: Annotated[
        str,
        typer.Option(
            "--mini-swe-agent-version",
            help="exact bash-only mini-SWE-agent cohort to normalize",
        ),
    ] = SWE_BENCH_DEFAULT_HARNESS_VERSION,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="replace the complete private capture bundle"),
    ] = False,
    max_bytes: Annotated[
        int,
        typer.Option(
            "--max-bytes",
            min=1,
            max=SWE_BENCH_HARD_MAX_SOURCE_BYTES,
            help="maximum accepted source bytes",
        ),
    ] = SWE_BENCH_DEFAULT_MAX_SOURCE_BYTES,
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout-seconds", min=0.1, max=60.0),
    ] = 30.0,
) -> None:
    """Capture one strict, route-free SWE-bench bash-only quality cohort."""

    try:
        capture = capture_swe_bench(
            source or SWE_BENCH_WEBSITE_URL,
            expected_sha256=expected_sha256,
            source_revision=source_revision,
            retrieved_at=_retrieved_at(retrieved_at),
            harness_version=harness_version,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
        )
        targets = write_swe_bench_capture(
            capture,
            output_directory,
            overwrite=overwrite,
        )
        typer.echo(
            f"captured {capture.rows_seen} SWE-bench bash-only rows "
            f"({capture.valid_rows} valid, {capture.invalid_rows} quarantined)"
        )
        typer.echo(f"source identity sha256:{capture.evidence.source_identity_sha256}")
        for target in targets:
            typer.echo(target)
    except (SweBenchAdapterError, OSError, ValueError) as exc:
        _error(exc)


@app.command("capture-arc-agi-2-public-eval", rich_help_panel=DATA_SOURCES_PANEL)
def capture_arc_agi_2_public_eval_command(
    output_directory: Annotated[Path, typer.Argument(file_okay=False)],
    retrieved_at: Annotated[
        str | None,
        typer.Option(
            "--retrieved-at",
            help="timezone-aware provenance timestamp; defaults to now",
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="replace the complete private capture bundle"),
    ] = False,
    timeout_seconds: Annotated[
        float,
        typer.Option(
            "--timeout-seconds",
            min=0.1,
            max=ARC_AGI_MAX_TIMEOUT_SECONDS,
            help="total deadline for revision metadata plus all 32 summaries",
        ),
    ] = ARC_AGI_DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Capture pinned ARC-AGI-2 summaries without fetching attempt content."""

    try:
        capture = capture_arc_agi_public_eval(
            retrieved_at=_retrieved_at(retrieved_at),
            timeout_seconds=timeout_seconds,
        )
        targets = write_arc_agi_public_eval_capture(
            capture,
            output_directory,
            overwrite=overwrite,
        )
        typer.echo(
            f"captured {capture.rows_seen} ARC-AGI-2 public-eval rows "
            f"({capture.valid_rows} valid, {capture.invalid_rows} quarantined)"
        )
        typer.echo(f"source identity sha256:{capture.evidence.source_identity_sha256}")
        for target in targets:
            typer.echo(target)
    except (ArcAgiAdapterError, OSError, ValueError) as exc:
        _error(exc)


@app.command("check-swe-bench-feed", rich_help_panel=SOURCE_MONITORING_PANEL)
def check_swe_bench_feed_command(
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", dir_okay=False),
    ] = None,
    fail_on_semantic_change: Annotated[
        bool,
        typer.Option(
            "--fail-on-semantic-change/--report-only",
            help="exit 3 after rendering a source, subject, row-set, or result change",
        ),
    ] = True,
) -> None:
    """Compare the latest official SWE-bench file with the reviewed pin."""

    try:
        status = inspect_swe_bench_feed(
            github_token=github_token_from_environment(),
        )
        _emit(json.dumps(status.document(), indent=2, ensure_ascii=False) + "\n", output)
    except (FeedMonitorError, OSError, TypeError, ValueError) as exc:
        _error(exc)
    if status.semantic_change and fail_on_semantic_change:
        raise typer.Exit(code=3)


@app.command("check-arc-agi-2-feed", rich_help_panel=SOURCE_MONITORING_PANEL)
def check_arc_agi_2_feed_command(
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", dir_okay=False),
    ] = None,
    fail_on_change: Annotated[
        bool,
        typer.Option(
            "--fail-on-change/--report-only",
            help="exit 3 after rendering a dataset head that differs from the reviewed pin",
        ),
    ] = True,
) -> None:
    """Check whether ARC-AGI-2 still points at the reviewed dataset revision."""

    try:
        status = inspect_arc_agi_feed()
        _emit(json.dumps(status.document(), indent=2, ensure_ascii=False) + "\n", output)
    except (ArcAgiFeedMonitorError, OSError, TypeError, ValueError) as exc:
        _error(exc)
    if status.review_required and fail_on_change:
        raise typer.Exit(code=3)


@app.command("inspect-harbor-terminal-bench", rich_help_panel=DATA_SOURCES_PANEL)
def inspect_harbor_terminal_bench_command(
    snapshot: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, dir_okay=False),
    ],
    import_config: Annotated[
        Path,
        typer.Argument(
            exists=True,
            readable=True,
            dir_okay=False,
            help="provenance/rights config; reconciliation entries may be empty",
        ),
    ],
    retrieved_at: Annotated[
        str,
        typer.Option(
            "--retrieved-at",
            help="timezone-aware timestamp when the local Harbor capture was retrieved",
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", dir_okay=False, help="write review inventory JSON"),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="replace an existing private inventory file"),
    ] = False,
) -> None:
    """Inspect stable source/subject identities before adding reconciliations."""

    try:
        timestamp = _retrieved_at(retrieved_at)
        if timestamp is None:  # pragma: no cover - Typer requires the option
            raise ValueError("--retrieved-at is required")
        inventory = inspect_harbor_terminal_bench_snapshot(
            snapshot,
            import_config,
            retrieved_at=timestamp,
        )
        _emit_private(
            json.dumps(inventory, indent=2, ensure_ascii=False) + "\n",
            output,
            overwrite=overwrite,
        )
    except (HarborAdapterError, PrivateOutputError, OSError, ValueError) as exc:
        _error(exc)


@app.command("reconcile-quality-evidence", rich_help_panel=QUALITY_EVIDENCE_PANEL)
def reconcile_quality_evidence_command(
    evidence: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, dir_okay=False),
    ],
    reconciliation: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, dir_okay=False),
    ],
    publication_scope: Annotated[
        QualityPublicationScope,
        typer.Option(
            "--publication-scope",
            help="rights scope to enforce while producing mapped rows",
        ),
    ] = QualityPublicationScope.INTERNAL,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", dir_okay=False),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="replace an existing private import report"),
    ] = False,
) -> None:
    """Apply an exact reviewed mapping to normalized benchmark evidence."""

    try:
        report = reconcile_quality_evidence(
            load_quality_evidence(evidence),
            load_quality_reconciliation(reconciliation),
            publication_scope=publication_scope,
        )
        _emit_private(dump_json(report), output, overwrite=overwrite)
    except (InputError, PrivateOutputError, OSError, TypeError, ValueError) as exc:
        _error(exc)


@app.command("project-quality-catalog", rich_help_panel=QUALITY_EVIDENCE_PANEL)
def project_quality_catalog_command(
    evidence: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, dir_okay=False),
    ],
    reconciliation: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, dir_okay=False),
    ],
    report: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, dir_okay=False),
    ],
    workload_id: Annotated[str, typer.Option("--workload-id")],
    workload_unit: Annotated[str, typer.Option("--workload-unit")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", dir_okay=False),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="replace an existing private quality catalog"),
    ] = False,
) -> None:
    """Project reviewed benchmark rows into a private quality-only catalog."""

    try:
        normalized = load_quality_evidence(evidence)
        source = quality_source_reference(normalized)
        workload = quality_workload_reference(
            normalized,
            workload_id=workload_id,
            unit=workload_unit,
        )
        catalog = project_quality_import_report(
            normalized,
            load_quality_reconciliation(reconciliation),
            load_quality_import_report(report),
            workload=workload,
            source=source,
        )
        _emit_private(dump_json(catalog), output, overwrite=overwrite)
    except (InputError, PrivateOutputError, OSError, TypeError, ValueError) as exc:
        _error(exc)


@app.command("build-quality-portfolio", rich_help_panel=QUALITY_EVIDENCE_PANEL)
def build_quality_portfolio_command(
    policy: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, dir_okay=False),
    ],
    base_catalog: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, dir_okay=False),
    ],
    catalog_output: Annotated[
        Path,
        typer.Option("--catalog-output", dir_okay=False),
    ],
    derivation_output: Annotated[
        Path,
        typer.Option("--derivation-output", dir_okay=False),
    ],
    component_frontiers: Annotated[
        list[str] | None,
        typer.Option(
            "--component-frontier",
            help="quality component assignment as COMPONENT_ID=FRONTIER_PATH; repeat",
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="replace existing private outputs"),
    ] = False,
    as_of: Annotated[str | None, typer.Option("--as-of")] = None,
) -> None:
    """Enrich one catalog with a replayable two-to-four-benchmark portfolio."""

    try:
        if catalog_output.resolve(strict=False) == derivation_output.resolve(strict=False):
            raise ValueError("catalog and derivation outputs must be different files")
        assignments = _path_assignments(
            component_frontiers,
            option="--component-frontier",
        )
        frontiers = {
            component_id: load_frontier_snapshot(path) for component_id, path in assignments.items()
        }
        result = build_portfolio(
            load_portfolio_policy(policy),
            frontiers,
            load_catalog(base_catalog),
            generated_at=_as_of(as_of) or datetime.now(UTC),
        )
        # Each file is independently canonical and replayable. Write the compact
        # derivation first so a later catalog-output failure never loses its lock.
        write_private_text(
            derivation_output,
            dump_json(result.snapshot),
            overwrite=overwrite,
        )
        write_private_text(
            catalog_output,
            dump_json(result.catalog),
            overwrite=overwrite,
        )
        typer.echo(derivation_output)
        typer.echo(catalog_output)
    except (InputError, PrivateOutputError, OSError, TypeError, ValueError) as exc:
        _error(exc)


@app.command("verify-quality-portfolio", rich_help_panel=QUALITY_EVIDENCE_PANEL)
def verify_quality_portfolio_command(
    policy: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, dir_okay=False),
    ],
    base_catalog: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, dir_okay=False),
    ],
    derivation: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, dir_okay=False),
    ],
    component_frontiers: Annotated[
        list[str] | None,
        typer.Option(
            "--component-frontier",
            help="quality component assignment as COMPONENT_ID=FRONTIER_PATH; repeat",
        ),
    ] = None,
    at: Annotated[
        str | None,
        typer.Option("--at", help="timezone-aware verification time; defaults to now"),
    ] = None,
) -> None:
    """Replay a portfolio derivation against its exact policy and inputs."""

    try:
        assignments = _path_assignments(
            component_frontiers,
            option="--component-frontier",
        )
        frontiers = {
            component_id: load_frontier_snapshot(path) for component_id, path in assignments.items()
        }
        snapshot = load_portfolio_derivation(derivation)
        verify_portfolio(
            load_portfolio_policy(policy),
            frontiers,
            load_catalog(base_catalog),
            snapshot,
            now=_verification_time(at) or datetime.now(UTC),
        )
        typer.echo(f"valid quality portfolio {snapshot.snapshot_id}")
    except (InputError, OSError, TypeError, ValueError) as exc:
        _error(exc)


@app.command("import-harbor-terminal-bench", rich_help_panel=DATA_SOURCES_PANEL)
def import_harbor_terminal_bench_command(
    output_directory: Annotated[Path, typer.Argument(file_okay=False)],
    snapshot: Annotated[
        Path,
        typer.Argument(exists=True, readable=True, dir_okay=False),
    ],
    import_config: Annotated[
        Path,
        typer.Argument(
            exists=True,
            readable=True,
            dir_okay=False,
            help="reviewed provenance, rights, and reconciliation configuration",
        ),
    ],
    retrieved_at: Annotated[
        str,
        typer.Option(
            "--retrieved-at",
            help="timezone-aware timestamp when the local Harbor capture was retrieved",
        ),
    ],
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="replace the generated private audit bundle"),
    ] = False,
    allow_partial: Annotated[
        bool,
        typer.Option(
            "--allow-partial",
            help=(
                "write an audit bundle even when reviewed rows fail reconciliation; "
                "default is fail closed"
            ),
        ),
    ] = False,
) -> None:
    """Import reviewed evidence from a captured Harbor Terminal-Bench response."""

    try:
        timestamp = _retrieved_at(retrieved_at)
        if timestamp is None:  # pragma: no cover - Typer requires the option
            raise ValueError("--retrieved-at is required")
        result = import_harbor_terminal_bench(
            snapshot,
            import_config,
            retrieved_at=timestamp,
            allow_partial=allow_partial,
        )
        targets = write_harbor_terminal_bench_import(
            result,
            output_directory,
            overwrite=overwrite,
        )
        typer.echo(
            f"imported {len(result.catalog.offerings)} mapped Terminal-Bench rows "
            f"from {result.rows_seen} normalized rows "
            f"({len(result.excluded)} not mapped)"
        )
        for target in targets:
            typer.echo(target)
    except (HarborAdapterError, OSError, ValueError) as exc:
        _error(exc)


@app.command("export-schemas", rich_help_panel=CONTRACTS_PANEL)
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
