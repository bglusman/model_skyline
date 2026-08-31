# Gateway and agent-runtime integrations

ModelSkyline is the workload evidence and selection control plane. A consumer
gateway is the execution data plane. The signed protocol in ADR 0003 lets the
gateway expose one stable logical model while silently updating its exact
default and fallback targets for new work units.

Gateway-pointer `v1alpha1` accepts only the ordinary `kind: "selection"`
artifact. It rejects `kind: "quality-gated-selection"`; that additive v0.7
wrapper currently requires a trusted distribution channel plus the unsigned
resolver's explicit bundle pin. Do not infer signature authentication or
durable anti-rollback protection for it from this integration guide.

## Consumer boundary

A native consumer implements this pipeline without embedding Python:

```text
untrusted bytes
  -> authenticated DSSE pointer
  -> digest-checked publication and selection bytes
  -> semantically verified selection
  -> exact local target bindings
  -> durably installed generation
  -> per-work-unit pinned route
  -> attempts and bounded receipt
```

The protocol [schemas](../schemas/) and
[portable conformance vectors](../conformance/gateway-pointer/v1alpha1/) are
the language boundary; [ADR 0003](adr/0003-signed-gateway-selection-protocol.md)
defines the verification rules that JSON Schema cannot express. Remote
fields never construct an endpoint, credential, provider header, command, or
ordinary gateway fallback. Each complete `OfferingKey` maps to an opaque local
target revision registered by the gateway operator.

The data plane can skip an unhealthy or request-incompatible member while
preserving the published order. If none remain, it fails closed. It cannot
fall through to the gateway's catch-all model, route-DAG descendants, or a
target that merely shares provider/model text.

## Integration priorities

| System | Best integration point | Value | Constraint before production |
| --- | --- | --- | --- |
| [Wardwright](https://github.com/bglusman/wardwright) | Native resolver, durable generation, and work-unit lease feeding a strict route | Reference consumer with OpenAI-compatible logical names, route DAGs, and receipts | Split target ID from upstream model, retain target revisions, execute ordered provider failure fallback, and pin across turns |
| [Envoy AI Gateway v1.1.0](https://github.com/envoyproxy/ai-gateway/releases/tag/v1.1.0) | Kubernetes controller reconciling a verified generation to references to pre-existing backends | Stable `v1beta1` API, model virtualization, backend priority, Responses API, and pre-first-byte failover | CRD status must expose signature/sequence/expiry; controller may name backends but never synthesize credentials |
| [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/concepts/api-overview/) | Lower serving tier beneath a selected offering | Pod/replica scheduling and accelerator-aware serving | `InferencePool` chooses a replica of one offering; it must not choose a different frontier offering |
| [Vercel AI SDK](https://ai-sdk.dev/docs/ai-sdk-core/provider-management) | Custom `Provider` exposing stable logical IDs | Native TypeScript model aliases and restricted model registries | Return a lease-aware model and pin before provider execution; omit `fallbackProvider`; middleware alone is too late to select another model |
| [LiteLLM](https://docs.litellm.ai/docs/proxy/model_management) | External controller/compiler targeting an isolated logical model | Broad gateway adoption and existing deployment routing | Routing plugins only narrow an existing candidate pool; require atomic generation update and affinity outside the plugin |
| [Bifrost core v1.7.15](https://github.com/maximhq/bifrost/releases/tag/core/v1.7.15) | Once-per-request `PreRequestHook` paired with a blocking HTTP or LLM hook | Go-native provider/model/fallback mutation observed by every attempt | A `PreRequestHook` error is non-blocking; verification failure must short-circuit before provider execution |
| [OpenRouter](https://openrouter.ai/docs/guides/routing/provider-selection) | One exact model/provider-endpoint request per pinned attempt | Immediate hosted execution experiment with endpoint-specific routing controls | Use exact endpoint slugs and disable platform fallback; do not let hosted routing widen the verified tuple |
| LangChain, Pydantic AI, OpenAI Agents SDK | Thin model-provider adapter | Useful when no central gateway exists | Adapter must persist work-unit affinity and prevent nested SDK retries from widening/multiplying |

The first community deliverable is the signed protocol plus conformance suite,
not a plugin for one gateway. Wardwright is the best dogfood/reference consumer.
An Envoy controller and a Vercel custom provider are the next cleanest native
surfaces. LiteLLM is initially safer behind an external compiler/controller.
Bifrost is viable when routing mutation and fail-closed enforcement are kept as
two explicit hook responsibilities.

## Wardwright mapping

Wardwright at commit `afe7c0a3de6003da19c32ffae5539ca79a1d4636` is
complementary, but it cannot currently retain a selection through a complete
agent/tool trajectory:

- each Chat Completions request rereads the latest global model config and
  replans;
- session runtime stores events and Dune sessions, not a route lease, and
  absent session IDs collapse to one `anonymous` identity;
- persistent model storage retains only the latest free-form config and memory
  is updated before the ignored persistence result;
- `model` conflates local target identity with the provider's upstream model;
- cascade plans do not yet execute provider-error fallback and one path can
  choose outside the declared cascade;
- Responses API chain affinity is not implemented.

Keep the projects separate. A Wardwright native consumer should add:

1. `target_id`, immutable `target_revision_digest`, and `provider_model` as
   separate identities. Map ModelSkyline offerings only to concrete direct
   provider targets in the first version.
2. A supervised resolver per logical model/source. It consumes schemas and
   fixtures with OTP Ed25519 and a real RFC 8785 implementation—never a Python
   subprocess or the existing adapter HMAC helper.
3. SQLite tables for checkpoints, exact installed generations, selection
   sources, immutable model revisions, and HMAC-pseudonymized work-unit pins.
   Use one `BEGIN IMMEDIATE` transaction and commit before swapping GenServer
   state.
4. A `SelectionLease` pinning pointer digest/sequence, selection snapshot,
   ordered target revisions, local config digest, and hard expiry. An explicit
   work-unit ID wins; session/thread fallback is opt-in; missing identity never
   becomes global `anonymous` affinity.
5. A strict route core whose input type is a lease, not a raw model string.
   Request capabilities, context fit, and health only intersect its target
   tuple. Empty intersection blocks.
6. Typed provider failures, one global attempt/deadline budget, streaming and
   side-effect boundaries, and bounded content-free receipts.

Likely integration surfaces are `application.ex`, `wardwright.ex`,
`model_graph.ex`, `route_planner.ex`, `router.ex`, `provider_runtime.ex`,
`sqlite_store.ex`, `runtime.ex`, `runtime/session_runtime.ex`,
`request_context.ex`, `stream_runtime.ex`, `receipt_builder.ex`, and the Gleam
route core. New protocol code belongs under a dedicated native
`SelectionSource.ModelSkylineV1` boundary.

A safe PR sequence is:

1. target identity and immutable local configuration revisions;
2. inactive protocol verifier/resolver plus upstream fixtures and durable LKG;
3. work-unit pinning and strict simulated routes;
4. Chat execution/fallback/receipts against two fake providers;
5. Responses chain affinity and framework propagation.

The critical scenario is generation 10 pinned by work unit A, generation 11
installed for new work unit B, restart preserving both, and rollback,
equivocation, unknown mapping, target mutation, or expiry unable to retarget A.

## Envoy AI Gateway controller profile

Pin this integration to [Envoy AI Gateway
v1.1.0](https://github.com/envoyproxy/ai-gateway/releases/tag/v1.1.0), the first
minor release on its stable 1.x API. Its `v1beta1` `AIGatewayRoute` can reference
pre-existing `AIServiceBackend` objects, rewrite the request model with
[`modelNameOverride`](https://github.com/envoyproxy/ai-gateway/blob/v1.1.0/api/v1beta1/ai_gateway_route.go#L354-L358),
and express ordered backend priority with
[`priority`](https://github.com/envoyproxy/ai-gateway/blob/v1.1.0/api/v1beta1/ai_gateway_route.go#L387-L397).
The release also supports `/v1/responses` and stream-idle failover before the
first token. These are useful data-plane primitives; none authenticates a
ModelSkyline publication or supplies rollback state.

Define a ModelSkyline controller/CRD profile whose spec references the stable
pointer and local `OfferingKey`-to-backend-revision mappings. Status reports the
observed pointer digest, sequence, hard expiry, last success, bounded error
class, and installed generation. The reconciler writes only references to
pre-existing backend objects and exact model overrides. It never derives a
backend, credential, `BackendSecurityPolicy`, header mutation, or body mutation
from signed remote metadata.

Compile each published fallback index to a distinct backend priority; do not
use weights for frontier order. Pair pre-first-byte retry/failover with one
global attempt and deadline budget, and disable retry once response bytes or a
side effect cross the work-unit boundary. The generated route must not contain
the controller's normal catch-all backend. When a local offering maps to an
`InferencePool` backend reference, that pool remains a replica selector for the
one offering rather than another model selector.

Sigstore/cosign and OCI distribution can be an additional Kubernetes transport
profile. Transparency inclusion never replaces ModelSkyline's channel sequence,
equivocation check, exact artifact hashes, or hard expiry. A controller must
retain old generations while any admitted work-unit lease still references
them; Kubernetes rollout success alone is not trajectory affinity.

## Gateway protocol adapter profiles

These profiles describe where a native verifier or trusted local sidecar can
attach. They do not relax ADR 0003: in every profile the local adapter consumes
a verified, durably installed generation and exact local mappings, then pins
one generation before provider execution.

### LiteLLM

Begin with an external controller and one isolated database-backed logical
model. Compile only verified, locally mapped offerings and preserve their
order. Do not mix YAML and database model definitions as competing sources of
truth. Publish the complete generation atomically and keep immutable deployment
revisions available for existing leases.

LiteLLM's official [routing-plugin
contract](https://docs.litellm.ai/docs/routing_plugins) exposes
`candidate_models` as an existing `provider/model` pool that a plugin may
narrow. An empty pool fails closed, but the plugin does not install new exact
offering bindings, and plugins disable the documented session-affinity mode.
Consequently a plugin can enforce an already installed generation but cannot
be the generation installer or trajectory lease. A production upstream
proposal should expose an atomic ranked-candidate generation, immutable
deployment revisions, and an explicit trajectory-affinity token.

### Bifrost

Bifrost core v1.7.15 has the right two phases, but they must be composed
carefully. [`PreRequestHook`](https://github.com/maximhq/bifrost/blob/core/v1.7.15/core/schemas/plugin.go#L283-L300)
runs once per top-level request and may mutate provider, model, and fallbacks;
the mutation is then visible to every attempt. Use it to apply an already
verified pinned route and store the generation/lease identity in bounded local
context.

Do not use its return error as the policy gate. The documented [plugin error
semantics](https://github.com/maximhq/bifrost/blob/core/v1.7.15/core/schemas/plugin.go#L170-L204)
log hook errors and continue. For `bifrost-http`, reject missing, invalid, or
expired leases in `HTTPTransportPreHook`. For the Go SDK, use a
`PreLLMHook` short circuit before the first provider call. The per-attempt hook
must only enforce the tuple selected once; it must not re-resolve on fallback.
Set the Bifrost fallback list to the exact remaining published targets and make
any policy failure non-fallbackable.

### Vercel AI SDK

Implement a custom `Provider`, not only `LanguageModelMiddleware`. The official
[`customProvider`](https://github.com/vercel/ai/blob/main/content/docs/07-reference/01-ai-sdk-core/42-custom-provider.mdx)
surface demonstrates that provider model IDs can map to arbitrary model
objects and can limit the available model set. Expose a stable logical model
ID whose lookup returns a lease-aware model object. At its first `doGenerate`
or `doStream`, admit or retrieve the work-unit lease from trusted local context
and bind the exact underlying model for that call and the rest of the work unit.

Do not configure `fallbackProvider`: its documented purpose is to satisfy IDs
missing from the custom map, which would widen a failed-closed selection.
Middleware remains useful inside the returned model for receipts and parameter
checks, but it receives an already chosen model and is not the authority for
switching models. One wrapper owns the global fallback/deadline budget and
propagates the lease explicitly to subagents.

### OpenRouter

Treat OpenRouter as a hosted executor beneath the ModelSkyline route, not as
the selection authority. For each attempt send one exact OpenRouter model and
one exact provider endpoint slug. Its [provider-routing
controls](https://openrouter.ai/docs/guides/routing/provider-selection) support
endpoint variants such as region or turbo slugs, an `only` allowlist, ordered
providers, `require_parameters`, and `allow_fallbacks: false`.

Use the exact endpoint slug in both `only` and `order`, enable
`require_parameters`, and disable OpenRouter fallback. A base provider slug can
match every region/variant, so it is not exact enough when region, tier, or
endpoint affects the `OfferingKey`. Execute a cross-model fallback list as
separate attempts under the local pinned-route executor; do not submit a
hosted `models` fallback array when each model requires a different exact
provider binding. Account-wide OpenRouter restrictions may narrow the set and
cause a local skip, but cannot authorize an offering absent from the lease.

## OpenAI compatibility is data-plane compatibility

An OpenAI-compatible Chat Completions or Responses endpoint is a valuable way
to keep an agent unaware of model changes. It is only the request/streaming data
plane. A `model` string and ordinary HTTP retry configuration do not convey a
DSSE trust policy, exact `OfferingKey`, immutable target revision, durable
sequence checkpoint, hard expiry, or work-unit generation lease.

Therefore expose a stable logical model through the compatibility endpoint only
after a native consumer, controller, or trusted local sidecar has completed the
signed protocol. Keep lease metadata on an authenticated internal hop and
strip it before the provider call. A public client-supplied model alias or
header never selects a target revision directly. Implementations that provide
only OpenAI wire compatibility are transport adapters, not conforming
ModelSkyline consumers.

## Runtime and v1alpha3 conformance

Control-plane and feedback conformance are separate release gates:

| Boundary | Normative artifacts | Required behavior |
| --- | --- | --- |
| Signed selection | [`gateway-pointer/v1alpha1`](../conformance/gateway-pointer/v1alpha1/) vectors, gateway JSON Schemas, and ADR 0003 | Independently reproduce every valid PAE/signature/hash/checkpoint result and reject every invalid, expired, rollback, and equivocation vector |
| Runtime feedback | [`request-trace-v1alpha3.schema.json`](../schemas/request-trace-v1alpha3.schema.json) plus the `RequestTrace` semantic and aggregation rules | Preserve observation scope and unknowns; validate exact Decimal arithmetic and cross-row workload/offering/provenance coherence |

A native gateway consumer should run the portable pointer vectors without
calling the ModelSkyline Python verifier. Fixture bytes are exact—canonical
payload and signed artifact files intentionally have no trailing newline. Test
keys are public deterministic inputs, never production secrets. Schema-only
success is insufficient because signature threshold, canonical JSON, semantic
hashes, sequence state, target binding, and expiry are application rules.

Before claiming an integration, exercise a loopback end-to-end path with two
fake provider targets: publish and sign, retrieve over bounded HTTP, verify and
durably install, pin work unit A, install the next sequence for work unit B,
restart, and prove that A retains its old target tuple while B receives the new
one. Also prove fail-closed behavior for rollback, same-sequence equivocation,
expiry, unknown mapping, mutated local target revision, capability mismatch,
redirect, oversized artifact, and a failure after response bytes begin.

Runtime receipts should project into the retained trace contract, not invent a
gateway-specific accounting format. Use `model-skyline/request-trace/v1alpha3`
when the observable unit is a logical model call that may hide provider retries;
set `observation_unit: model_call` and leave `model_request_count` unknown unless
the complete request count is proven. Use `request` only for one observed
provider request, and `attempt` or `work_unit` only for genuine aggregates.
Never relabel a `model_call` as a request to fit a v1alpha2 consumer. Validate
rows against the matching versioned schema and then run semantic aggregation;
JSON Schema cannot prove exact totals or cross-row coherence.

## Receipts and feedback

At admission, retain pointer digest, sequence, selection/policy/frontier/workload
identity, target revision tuple, hard expiry, work-unit pin scope, and whether
an unexpired LKG was used. Per attempt, retain the original fallback index,
offering digest, target revision, local skip reason, and typed failure.

Expose only bounded selection/offering digests and fallback index to callers.
Strip internal headers before provider execution. Do not record prompt/tool
content, endpoint URLs, credentials, repository paths, tenant email, or raw
session/work-unit IDs. Pseudonymize correlations with a domain-separated,
rotating-key HMAC and keep telemetry delivery asynchronous.

Aggregated feedback can use the OpenTelemetry
[GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
and then project into ModelSkyline `RequestTrace` rows. Low-cardinality operator
features—workload, operation class, framework, context bucket, tool-use bucket,
latency SLO, batch/interactive, and capabilities—are more useful and safer than
prompt classification at the gateway.
