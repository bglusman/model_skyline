# Contributing

ModelSkyline is in schema-design alpha. Issues that include a concrete workload,
trace shape, price-card edge case, or agent integration are especially useful.

## Development

```console
uv sync --extra dev
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run pytest
```

If a public Pydantic model changes, regenerate the committed contracts and
review the diff:

```console
uv run modelskyline regenerate-schemas schemas
git diff -- schemas
```

Pull requests should include tests for semantics, not only examples. Important
properties include input-order invariance, no dominated returned member,
explicit rejection of missing/stale data, deterministic ties, correct
minimize/maximize reversal, and failure-inclusive cost-per-success.

Publisher changes must preserve the root `latest.json` commit-marker contract:
all immutable files named by a manifest are durable before that manifest, and
the root marker is replaced after every mutable alias. Test interrupted writes,
idempotent retry, immutable collision/corruption, history validation, and
full-refresh behavior. Existing publication sets are additive; do not interpret
an omitted frontier or selection as implicit retirement. Keep publication paths
portable and bounded, and do not weaken the dedicated-root, no-symlink, or
single-writer assumptions without a documented threat-model change.

## Data and benchmarks

Do not commit third-party model outputs, traces, benchmark questions, or price
catalogs unless their terms clearly permit redistribution. An adapter can be
open source even when its upstream data is not redistributable.

Every imported observation should preserve:

- source and license/terms;
- retrieval and effective time;
- benchmark, harness, agent, judge, and configuration versions;
- offering/provider/region/tier identity;
- sample count and uncertainty when available;
- methodology and raw-artifact hash.

For a public publisher fixture or deployment, record the exact license values
passed to `--allow-license` and the external justification for every
`--authorize-source` override. The public-mode check is not a legal conclusion
or a privacy scanner. Review generated artifacts separately for prompts,
secrets, personal data, private endpoints, and sensitive free-form metadata.
Do not commit or deploy benchmark data merely because an adapter can download
or analyze it locally.

## Security

Public configuration is untrusted. Formula additions must stay within the
restricted grammar. Oracle implementations are explicitly registered by the
host; never add arbitrary module names, shell commands, or executable paths to
the normal policy file. See `SECURITY.md` for reporting vulnerabilities.
