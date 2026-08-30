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

## Security

Public configuration is untrusted. Formula additions must stay within the
restricted grammar. Oracle implementations are explicitly registered by the
host; never add arbitrary module names, shell commands, or executable paths to
the normal policy file. See `SECURITY.md` for reporting vulnerabilities.
