# ModelSkyline projection for LiteLLM

> **Status: experimental, separate package.** This directory is not part of the
> `model-skyline` core wheel and is not a general gateway abstraction. Its one
> job is to project an ordinary, already-produced `SelectionSnapshot` into a
> versioned LiteLLM model group, then move one stable alias after exact
> readback. Do not treat it as production-ready until the pinned-container
> evidence below is complete for the behavior on which your deployment relies.

The boundary is deliberately small:

```text
trusted local SelectionSnapshot + operator-owned exact bindings
                             |
                             v
                immutable versioned model group
                             |
                             v
                     one stable alias
```

ModelSkyline chooses and orders complete offerings. LiteLLM owns credentials
and executes requests. This controller never accepts a target URL, API key, or
provider parameter from a selection artifact.

## Pinned compatibility target

Development targets LiteLLM `v1.98.0` and these version-specific management
interfaces:

- `POST /model/new` to create a deployment row;
- `GET /model/info` to verify all rows in a versioned group;
- `GET /get/config/callbacks` to read the runtime alias map; and
- `POST /config/update` to replace that map with a complete intended value.

The container reference used for integration work is:

```text
ghcr.io/berriai/litellm-database@sha256:5ead13edd4efd89f32dab349c1f19447d395affca53f3aeae00f5e6e01b8c08d
```

That digest identifies the multi-platform image index published for
`ghcr.io/berriai/litellm-database:v1.98.0`; use a digest rather than a moving
tag when reproducing results. The controller also requires LiteLLM database
model storage, normally PostgreSQL plus `STORE_MODEL_IN_DB=True`.

On a pristine `v1.98.0` database, `GET /model/info` returns a version-specific
HTTP 500 error instead of an empty `data` list. The client treats only the
exact, bounded, strictly decoded pinned response as an empty catalog; any
near-match or other 500 fails closed. Once the first row is staged, normal
catalog responses apply.

`/get/config/callbacks` is an Admin UI endpoint rather than a documented public
control-plane contract. The implementation is consequently pinned and may
need changes before any LiteLLM upgrade. LiteLLM `v1.98.0` also has a known
alias-reset hazard when another router update omits `model_group_alias`; see
[issue 36446](https://github.com/BerriAI/litellm/issues/36446) and
[PR 36451](https://github.com/BerriAI/litellm/pull/36451). Every writer of
router settings must therefore be serialized and version-reviewed.

## Install for development

From the ModelSkyline repository root:

```console
uv sync --project integrations/litellm --extra dev
uv run --project integrations/litellm modelskyline-litellm --help
```

The package requires Python 3.11 or newer and `model-skyline>=0.9,<1`.

## Local bindings, not remote routing instructions

The integration configuration pins:

- the expected selection, frontier, and workload identities;
- a lower-case stable alias and maximum candidate count;
- a complete `OfferingKey` for each allowed offering; and
- one distinct operator-local target for each binding.

Each target contains only a LiteLLM model identifier, a named-credential
identifier, and an operator-supplied SHA-256 `revision`. See the generated
[`bindings.json`](tests/fixtures/bindings.json) fixture for the exact shape and
[`regenerate-fixtures.py`](scripts/regenerate-fixtures.py) for its provenance.
Do not abbreviate an `OfferingKey` to a model name: provider, endpoint, region,
tier, quantization, reasoning settings, and harness may change its economics or
behavior.

Provision the named credentials in LiteLLM before staging. Credential creation,
rotation, endpoint configuration, and secret storage are intentionally outside
this package. The `revision` is only an operator attestation used in the
projected fingerprint. The controller cannot read or prove the contents of a
mutable LiteLLM credential with the same name. Prefer immutable, versioned
credential names and change the attested revision whenever their reviewed
target configuration changes.

## Plan, stage, activate

Use a current selection artifact and a private operator configuration. The
normal production commands use trusted current UTC and omit `--at`:

```console
uv run --project integrations/litellm modelskyline-litellm plan \
  /path/to/selection.json /path/to/litellm-bindings.json

uv run --project integrations/litellm modelskyline-litellm stage \
  /path/to/selection.json /path/to/litellm-bindings.json \
  --base-url https://litellm.internal.example

uv run --project integrations/litellm modelskyline-litellm activate \
  /path/to/selection.json /path/to/litellm-bindings.json \
  --base-url https://litellm.internal.example
```

Set the LiteLLM admin token in `LITELLM_MASTER_KEY`; there is no CLI token
argument. Plain HTTP is refused except for an explicit loopback test using
`--allow-local-http`. `--at` exists for deterministic tests and audited replay
with a trusted timestamp, not to bypass expiry in routine operation.

The commands have separate failure boundaries:

1. `plan` securely loads a regular local selection file, verifies its content
   hash, expected identities, time bounds, candidate cap, and exact bindings,
   then prints a content-free summary. It derives deterministic deployment IDs
   and a group name scoped to the alias, snapshot, and selected local-target
   fingerprints. Rotating an attested target therefore stages a new immutable
   group even when the selection snapshot itself is unchanged.
2. `stage` creates any missing rows in that versioned group and reads them all
   back. Verification requires the exact authored execution parameters, pinned
   inactive server defaults, and pinned access/block/rate controls. It ignores
   LiteLLM's live provider capability and pricing enrichment because that data
   is not stored target identity. It never changes the stable alias, so this
   command does not promote a partial or conflicting group. Retrying an exact
   stage is idempotent.
3. `activate` verifies the staged group, refuses configured global fallbacks
   that could widen it, preserves the complete observed alias map, changes one
   alias, and reads the complete map back. It refuses takeover of an unmanaged
   target and generation rollback visible through the current managed alias.

Lower LiteLLM deployment `order` values follow the ModelSkyline choice order.
The old versioned group is retained after activation; this package never
deletes a group.

A successful command exits `0`; a definite validation, preflight, or API
failure exits `2`. An activation whose write may have committed but cannot be
read back exits `3` and prefixes stderr with `indeterminate:`. Automation must
stop on exit `3` for operator reconciliation rather than treating it as an
ordinary retryable failure.

## Evidence and non-claims

The unit suite covers deterministic projection, strict identity and binding
validation, content-free planning output, bounded management responses, stage
idempotence and conflict handling, complete alias-map preservation, visible
rollback/equivocation checks, and modeled uncertain-write outcomes.

On 2026-08-31, the checked-in test passed from a clean Docker volume on ARM64
against the pinned `v1.98.0` image-index digest, PostgreSQL, and two synthetic
OpenAI-compatible targets. That one-worker, non-streaming chat-completions run
observed all of the following:

- both named credentials were created in LiteLLM's database;
- snapshot A staged and read back two deterministic rows;
- its versioned group and then the stable alias routed to rank 1;
- a forced upstream rank-1 HTTP 503 routed the request to rank 2;
- snapshot B, whose Skyline order was reversed, staged and activated so the
  stable alias routed to its new rank 1;
- after restarting LiteLLM, both groups and the alias remained verifiable; and
- the superseded versioned group remained directly callable.

Reproduce that exact local scenario with:

```console
MODELSKYLINE_RUN_LITELLM_E2E=1 \
  uv run --project integrations/litellm \
  pytest -q integrations/litellm/tests/e2e/test_blue_green.py
```

This is runtime evidence for those bounded claims, not a production-readiness
claim. It does not cover multiple LiteLLM workers/replicas, real provider
credentials, streaming, tool calls, partial responses, side effects, provider
error classes other than a pre-response 503, concurrent router writers, or
key/team-level routing policy. Use a dedicated execution key and team whose
policy has no fallback capable of widening the managed group; this controller
checks global router fallback settings but cannot inspect or enforce every
request-scoped LiteLLM policy. Custom routers or plugins that consume arbitrary
`model_info` fields are also out of scope and can invalidate this boundary.

This package also does not provide:

- a watcher, scheduler, RSS/feed poller, daemon, or automated promotion policy;
- credential provisioning, deployment cleanup, garbage collection, or a
  rollback command;
- compare-and-swap or a transaction across model rows and router settings;
- durable anti-rollback state, publisher signatures, or untrusted remote
  selection distribution;
- hard pinning of one ModelSkyline snapshot for a complete multi-request work
  unit; or
- stream/side-effect-aware retry guarantees.

The two reads before activation narrow a race but cannot remove it. LiteLLM has
no compare-and-swap primitive for this alias mutation, so **all router-settings
writers must use one external single-writer discipline**. If the write outcome
is indeterminate, stop automation and inspect the full alias map and managed
group before retrying.

LiteLLM session affinity may pin one deployment, but that is not evidence that
every turn, tool call, retry, and subagent in a logical work unit remains on one
ModelSkyline snapshot generation. Keep superseded groups for at least the
maximum work-unit lifetime, and implement work-unit generation pinning in the
consumer if that invariant is required.

See [the gateway integration guide](../../docs/gateway-integrations.md) for the
larger ecosystem boundary. Wardwright is a native consumer experiment; this is
a LiteLLM control-plane projection experiment. vLLM is useful underneath either
as an inference runtime for one exact offering, but it is not an equivalent
cross-model selection control plane.
