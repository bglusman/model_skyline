# Discovery and provisional evidence

Discovery inventories possible offerings and records provenance. It does not
rank offerings, add them to a mature frontier automatically, or alter the
`evaluate` and `select` contracts.

## Admission policies

To record different admission rules for different frontiers, pass a strict
JSON policy file:

```console
cat > frontier-policies.json <<'JSON'
{"frontiers": {"agent-value": "require_quality", "experimental": "allow_catalog_only"}}
JSON
uv run modelskyline discover --frontier-policy-file frontier-policies.json \
  --output discovery.json
```

The supported policies are:

- `require_quality`: exclude offerings without evaluation quality evidence;
- `allow_catalog_only`: admit catalog-verified offerings only;
- `allow_vendor_reported`: also admit vendor-reported offerings; and
- `mark_unverified`: admit all discovered offerings as unverified.

Every decision is retained under `frontier_admissions`, including an explicit
exclusion reason. Weaker-evidence admissions carry
`uncertainty_marker: true` and an admission value ending in `*`. The same
offering can therefore be admitted by one frontier and excluded by another.
Catalog identity is never treated as an evaluation result. The policy is
data-only JSON; arbitrary code, plugins, and executable policy are not
supported.

## Provisional evidence catalog

A discovery run can also emit a separate, explicitly non-mature artifact:

```console
uv run modelskyline discover --provisional-catalog-output provisional.json \
  --output discovery.json
```

`provisional.json` uses
`model-skyline/provisional-evidence-catalog/v1alpha1`. It retains separate
offering identities, including batch and contributor variants, and copies
launch-day catalog signals such as exact OpenRouter input/output/cache prices,
context length, and explicitly supplied aggregator telemetry.

Each signal has one evidence label:

- `catalog_verified`;
- `vendor_evaluated`;
- `independent_non_comparable`;
- `independent_comparable`; or
- `aggregator_telemetry`.

No absent quality is converted to zero. Named benchmark results may be supplied
as a strict JSON array with `--provisional-benchmarks`; each row must include
the offering id, benchmark, methodology, score, source URL, and evidence label.

This inventory is not an `ObservationCatalog`, frontier, or selection input.
Its records carry `mature_evaluation_eligible: false` and
`selection_eligible: false`; `evaluate` and `select` never read it. Vendor and
independent evidence can remain visible without weakening mature admission
rules or being promoted to independently measured quality.
