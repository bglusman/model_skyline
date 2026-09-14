# Reviewed cross-workload catalog enrichment

Some useful frontier gates are measured under a different workload from the
frontier axes. A ResearchClawBench result can supply research quality and task
latency, while a late-needle probe supplies validated context and a session
endurance run supplies swap growth. Those observations must remain separate,
but an operator may explicitly authorize named signals to accompany the exact
route evaluated by the quality workload.

ModelSkyline provides two deliberately different operations:

- `compose-catalogs` unions candidates or disjoint signals only when every
  input has the same complete `WorkloadReference`.
- `enrich-catalog-across-workloads` requires a reviewed, hashed-input policy
  before moving any signal between different workloads.

Neither operation matches by model name, mutable alias, provider label, or a
subset of offering fields.

## Policy contract

`model-skyline/catalog-enrichment-policy/v1alpha1` records:

- the exact base catalog hash and workload;
- an output workload id and unit;
- every source catalog hash and workload;
- the complete source and target `OfferingKey` for each mapping;
- an allowlist of signal ids; and
- a non-empty human review note explaining why the signal is transferable.

The base catalog alone defines the candidate universe. Source-only offerings
do not become candidates. A mapping may intentionally connect different
`agent_harness` values—for example, a deterministic capacity probe and a
ResearchClaw harness—but only because both complete identities are written in
the policy and reviewed. No field is ignored during lookup.

The output workload version is derived as
`catalog-enrichment-sha256:<policy hash>`. A frontier configuration must use
that exact version. Changing a catalog hash, mapping, signal allowlist, review
note, or output identity therefore produces a different workload position.

## Minimal shape

```json
{
  "schema_version": "model-skyline/catalog-enrichment-policy/v1alpha1",
  "kind": "catalog-enrichment-policy",
  "algorithm_version": "reviewed-exact-offering-signal-projection-v1",
  "policy_id": "research-agent-operational-gates",
  "base_catalog_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "base_workload": {
    "id": "measured-local-research-agent-v1",
    "version": "source-identity-sha256:example",
    "unit": "benchmark_task"
  },
  "output_workload_id": "measured-local-research-agent-v1",
  "output_workload_unit": "benchmark_task",
  "projections": [
    {
      "projection_id": "validated-capacity",
      "catalog_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "workload": {
        "id": "validated-capacity-v1",
        "version": "1",
        "unit": "context_position"
      },
      "mappings": [
        {
          "source_offering": {
            "offering_id": "local/qwen-capacity-probe",
            "model_id": "Qwen/Qwen3.8-27B",
            "provider": "local:macbook-m5max-64",
            "endpoint": null,
            "billing_mode": "owned_hardware",
            "region": "local",
            "service_tier": "high-power-configured",
            "quantization": "oQ4e",
            "reasoning_effort": null,
            "agent_harness": "late-needle/v1",
            "capabilities": ["long-context", "text", "tools"]
          },
          "target_offering": {
            "offering_id": "local/qwen-research-agent",
            "model_id": "Qwen/Qwen3.8-27B",
            "provider": "local:macbook-m5max-64",
            "endpoint": null,
            "billing_mode": "owned_hardware",
            "region": "local",
            "service_tier": "high-power-configured",
            "quantization": "oQ4e",
            "reasoning_effort": "low",
            "agent_harness": "researchclaw/pinned-v1",
            "capabilities": ["long-context", "text", "tools"]
          },
          "signal_ids": ["local_validated_context_tokens"],
          "review_note": "The probe and agent use byte-identical model and runtime configuration; only the measurement harness and reasoning policy differ, neither of which changes allocation capacity."
        }
      ]
    }
  ]
}
```

The example hashes and identities are illustrative. Generate a policy from the
actual catalogs; do not edit the example into a claim about a measured route.

Validate the policy and print its derived workload identity before updating the
frontier configuration:

```console
modelskyline validate-catalog-enrichment-policy research-operational-gates.json
```

## Replay

```console
modelskyline enrich-catalog-across-workloads \
  research-operational-gates.json \
  research-quality-candidates.json \
  validated-capacity-catalog.json \
  session-endurance-catalog.json \
  --output private/research-route-gated-catalog.json
```

The command verifies the complete input set by content hash, checks every
source and target `OfferingKey`, and copies only allowlisted observations with
explicit source provenance. It never overwrites a base signal. Source catalog
metadata is not inherited; each target row receives a compact audit containing
the policy hash, base hash, source workload, source offering id, selected
signals, and review note.

An unmapped base candidate remains in the catalog without the gate signal. The
ordinary frontier engine will reject it when the frontier requires that gate.
A policy that names an absent source/target, missing signal, conflicting target
signal, unpinned catalog, or inconsistent source descriptor fails the complete
build.

Output is written through the private artifact path. Enrichment preserves
rights and provenance; it does not grant publication permission. Publication
still requires the usual privacy, rights, and evidence review.
