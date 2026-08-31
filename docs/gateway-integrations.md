# Gateway and agent-runtime integrations

ModelSkyline is the workload evidence and selection control plane. A consumer
gateway is the execution data plane. The signed protocol in ADR 0003 lets the
gateway expose one stable logical model while silently updating its exact
default and fallback targets for new work units.

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
| [LiteLLM](https://docs.litellm.ai/docs/proxy/model_management) | External controller/compiler targeting database-backed model definitions plus an upstream trajectory-affinity hook | Broad gateway adoption and existing ordered fallbacks | One authoritative config source, atomic generation update, exact offering metadata, no fallback outside the verified set |
| [Envoy AI Gateway](https://aigateway.envoyproxy.io/docs/latest/api/) | Kubernetes controller reconciling a verified generation to references to pre-existing backends | Strong status/rollout/OCI ecosystem and fleet-wide policy | CRD status must expose signature/sequence/expiry; controller may name backends but never synthesize credentials |
| [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/concepts/api-overview/) | Lower serving tier beneath a selected offering | Pod/replica scheduling and accelerator-aware serving | `InferencePool` chooses a replica of one offering; it must not choose a different frontier offering |
| [Bifrost](https://github.com/maximhq/bifrost/blob/dev/docs/plugins/writing-go-plugin.mdx) | `PreRequestHook` after a fail-closed/blocking policy-error hook exists | Go-native, once-per-top-level-request model/provider/fallback injection | Current hook errors are logged and skipped, so an expired or invalid policy can fall through; upstream blocking semantics are a prerequisite |
| LangChain, Pydantic AI, OpenAI Agents SDK, Vercel AI SDK | Thin model-provider or middleware adapter | Useful when no central gateway exists | Adapter must persist work-unit affinity and prevent nested SDK retries from widening/multiplying |
| OpenRouter | Compile a pinned ordered model/provider list per work unit | Immediate hosted execution experiment | Verify provider routing controls can prohibit provider/model substitutions outside exact offering mappings |

The first community deliverable is the signed protocol plus conformance suite,
not a plugin for one gateway. Wardwright is the best dogfood/reference consumer;
LiteLLM and Envoy are the highest-leverage follow-ons. Bifrost should wait for a
blocking hook rather than treating logged plugin failure as policy enforcement.

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

## LiteLLM and Envoy profiles

For LiteLLM, begin with an external controller and an isolated database-backed
logical model. Compile only verified, locally mapped offerings and preserve
their order. Do not mix YAML and database model definitions as competing
sources of truth. A production upstream proposal should expose an atomic
ranked-candidate generation plus trajectory-affinity token, instead of relying
on repeated mutation of ordinary fallback configuration.

For Envoy, define a controller/CRD profile whose spec references the stable
pointer and local backend mappings. Status should report observed pointer
digest, sequence, expiry, last success, bounded error class, and installed
generation. A valid reconciler only writes references to pre-existing backend
objects. Sigstore/cosign and OCI distribution can be an additional Kubernetes
transport profile; transparency inclusion never replaces channel sequence or
hard expiry.

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
