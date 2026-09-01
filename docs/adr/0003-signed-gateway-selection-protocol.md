# ADR 0003: signed gateway selection protocol

Status: superseded in v0.9; design retained as historical research

Version 0.9 removed the DSSE pointer, trust-policy schemas, SQLite store,
gateway resolver, and conformance corpus. They had no demonstrated native
consumer and expanded the product beyond its proven catalog → frontier →
ordinary-selection path. Current integrations consume `SelectionSnapshot` from
a trusted local file or trusted HTTPS origin and must describe their transport
and process-local rollback limitations. This ADR is not a shipped protocol or
security claim; it remains a design reference if untrusted distribution becomes
a concrete consumer requirement.

## Context

`SelectionSnapshot` already provides a semantic content identity, a workload,
frontier and policy binding, an expiry, and an ordered default/fallback list of
complete `OfferingKey` values. It does not authenticate a publisher, bind an
intended gateway fleet, or prevent a valid old snapshot from being replayed.
HTTPS and ETags protect transport and caching; they are not a durable update
protocol.

ModelSkyline must remain the slow evidence and policy control plane. Gateways
and agent frameworks remain the fast data plane that owns credentials,
transport, provider health, streaming, retry execution, and receipts. A
gateway may narrow a verified candidate list for request capabilities or
temporary health. It must never widen the list, match by model name, or fall
through to an unrelated default.

## Decision

ModelSkyline defines a gateway-neutral three-artifact protocol:

```text
stable channel URL
  -> DSSE envelope
       -> RFC 8785 GatewaySelectionPointer
            -> exact PublicationManifest bytes
            -> exact SelectionSnapshot bytes
                 -> exact local OfferingKey-to-target bindings
                      -> pinned route for one work unit
```

The stable URL serves a small, frequently renewed DSSE pointer. The pointer
contains audience, channel, safe-integer sequence, activation window, hard
expiry, and SHA-256/length references to immutable publication and selection
files. It does not contain an endpoint, credential, key-discovery URL, or
gateway retry policy.

The protocol uses [DSSE v1.0.2](https://github.com/secure-systems-lab/dsse/blob/master/protocol.md)
with payload type
`application/vnd.model-skyline.gateway-selection-pointer.v1alpha1+json`.
Signatures are pure Ed25519 over DSSE pre-authentication encoding:

```text
"DSSEv1" SP LEN(payloadType) SP payloadType SP LEN(payload) SP payload
```

The pointer is RFC 8785 canonical JSON. A verifier authenticates the exact
decoded bytes before parsing, then requires them to round-trip through RFC
8785 byte-for-byte. Producers emit ordinary padded RFC 4648 Base64. Ed25519
public keys use the RFC 8037 public JWK form, and `keyid` is its RFC 7638/RFC
9278 SHA-256 JWK-thumbprint URI. `keyid` is only an unauthenticated lookup hint;
the verifier counts distinct locally trusted public keys that actually verify.

RFC 8785 orders object members but does not reorder arrays. This profile treats
`audience` and `selection.required_capabilities` as sets with one wire form:
each array must be duplicate-free and sorted in ascending Unicode code-point
order. Their current identifier alphabets are ASCII, so this is also ascending
ASCII lexical order. A conforming consumer rejects an otherwise valid signature
whose pointer uses a different array order.

Asymmetric crypto is an optional `model-skyline[gateway]` dependency. Schema,
hash, DSSE PAE, and conformance data remain in the base package. A missing
crypto implementation is an explicit construction/verification error and can
never downgrade to the unsigned resolver.

## Trust and hash domains

Trust policy is local and pins:

- trust namespace, normalized HTTPS issuer, audience, channel, and project;
- trusted Ed25519 public keys and a distinct-key threshold;
- expected selection, frontier, workload, and optionally policy hash;
- minimum sequence, maximum future skew, pointer age and lifetime;
- exact complete `OfferingKey` mappings to opaque local target IDs and
  immutable target revision digests;
- locally asserted target capabilities and resource limits.

Four hash domains must remain distinct:

1. SHA-256 of decoded canonical DSSE payload bytes: update/checkpoint identity.
2. SHA-256 and length of exact publication/selection file bytes: transport
   representation binding.
3. `SelectionSnapshot.snapshot_id`: semantic selection identity, including its
   narrowly scoped absent/null `billing_mode` compatibility behavior.
4. `PublicationManifest.publication_id`: semantic publication identity.

The signed raw-file digests are the security binding. Semantic hashes retain
ModelSkyline provenance and are verified independently; neither substitutes
for the other.

## Verification and installation

A consumer performs these gates in order:

1. Bound and strictly parse one DSSE envelope, rejecting duplicate JSON names,
   invalid UTF-8/Base64, oversized content, and excess signatures.
2. Verify the local distinct-key threshold over exact DSSE PAE bytes before
   parsing the pointer.
3. Require exact payload type and RFC 8785 pointer bytes.
4. Check issuer, audience, channel, project, expected identities, local limits,
   activation, hard expiry, and preliminary sequence state.
5. Resolve only safe relative paths below the configured issuer. Fetch with no
   redirect or content coding and bounded bytes/time.
6. Check exact length and raw SHA-256 before parsing either artifact.
7. Validate `publication_id` and require its one `PublishedSelection` entry to
   match every signed selection/path/hash/frontier binding.
8. Validate the selection schema and semantic hash; require every duplicated
   identity and validity bound to match.
9. Bind every candidate by RFC 8785 equality of the complete `OfferingKey` to
   a pre-registered target revision. Signed, offering-declared, local-target,
   and request-derived capabilities must agree.
10. Commit checkpoint, exact envelope/publication/selection bytes, canonical
    bound-target tuple, hard expiry, and target revision digest atomically. Only
    then expose the route.

SQLite is the Python reference store. `BEGIN IMMEDIATE` serializes installers;
checkpoint and last-known-good bytes are committed together before the resolver
swaps its active in-memory generation. The process-local store is explicitly
for tests and ephemeral consumers, not a production rollback boundary. The
SQLite file must live in an operator-owned directory with mode `0700`; database,
WAL, and shared-memory files use mode `0600`. These controls prevent accidental
cross-user disclosure, not deletion or wholesale rollback by an identity that
can replace the private state directory.

## Sequence, expiry, and rotation

The checkpoint key is `(trust namespace, issuer, configured audience,
channel)`. It deliberately excludes key ID, selection snapshot, and protocol
implementation version, so those cannot reset rollback history.

- A higher sequence can install after all verification gates pass.
- An equal sequence with the same pointer payload is idempotent. The outer
  envelope may add rotation signatures without changing pointer identity.
- An equal sequence with different pointer bytes is equivocation and fails.
- A lower sequence is rollback and always fails.
- Sequence gaps are valid because pollers may miss publications.
- First contact enforces a locally configured minimum sequence.

Keys rotate out of band: pre-provision the new public key, dual-sign the same
payload during overlap, update local threshold policy, then retire the old key.
Sequence never resets. Autonomous in-band root rotation is out of scope; use
[TUF](https://theupdateframework.github.io/specification/latest/) if its
old-and-new-threshold root chain is required.

The canonical local target tuple is also part of one installed generation. A
different target revision under the same signed sequence is never activated as
an in-place retarget. On restart after an intentional local mapping change, the
resolver retains the authenticated old sequence as its rollback floor, refuses
to use those old bytes as LKG under the new mapping, and accepts only a higher
signed sequence. Operators must therefore coordinate target-revision rollouts
with a sequence advance; already pinned work units keep their original tuple.

`hard_expires_at` is exclusive and strict. Clock skew applies only to future
`issued_at`/`not_before` checks; it never extends hard expiry. A transport error
may use an installed last-known-good route only before the earlier of pointer
hard expiry and selection expiry. Invalid signature, rollback, equivocation,
schema, digest, mapping, or capability errors block new admissions by default.
An emergency route is a separate, explicit, observable local policy—not an
implicit widening of the signed list.

The resolver clock is a security input. The reference resolver rejects a wall
clock that moves backward within a process or precedes the durable installation
timestamp at restart. It reacquires time after network retrieval, before
installation, and again before admission, so fetch latency cannot consume
expiry headroom unnoticed. Operators must provide authenticated UTC and alert
on clock failures; the protocol cannot repair a host whose clock and durable
state are both rolled back by the same privileged actor.

## Trajectory and retry semantics

Resolution returns an opaque `PinnedGatewayRoute` containing pointer digest,
sequence, selection/policy/frontier/workload identities, hard expiry, ordered
complete offerings, local target IDs, and target revision digests. The caller
retains it for the complete work unit and propagates it to subagents that share
affinity. A refresh affects only new work.

Local request capability and health checks may filter this ordered tuple. They
cannot add targets or change target revisions. A data-plane executor must own
one global attempt/time budget so SDK, gateway, and route-graph retries do not
multiply. Stateful provider response chains additionally require exact-target
stickiness or transcript replay.

Admission requires sufficient expiry headroom for the configured maximum
trajectory duration. No pin authorizes execution after hard expiry.

## Multi-frontier selections

The first profile accepts only current `kind: selection`. A future profile may
carry `MultiFrontierSelectionSnapshot`, but its consumer must reproduce the
source-backed verification from ADR 0002. Treating its content hash alone as
authorization would weaken the overlap/proximity evidence contract.

## Privacy and observability

Receipts and status are content-free and bounded. They may contain selection
digest, sequence, policy/frontier/workload IDs, selected offering digest,
fallback index, target revision digest, and a typed local skip/failure reason.
They must not contain prompts, tool arguments/results, endpoint URLs,
credentials, raw tenant/session/work-unit IDs, or arbitrary remote metadata.
Internal routing headers are stripped before provider calls.

Asynchronous feedback should project allowlisted, low-cardinality fields into
OpenTelemetry GenAI attributes and canonical `RequestTrace` rows. Work-unit
correlation uses a rotating-key, domain-separated HMAC. Routing never depends
synchronously on telemetry delivery.

## Alternatives

- Bare Ed25519 over `snapshot_id` was rejected because semantic identity is not
  an exact byte representation and does not bind audience, sequence, or expiry.
- JWS was rejected because its algorithm/key-discovery surface is larger and
  DSSE directly authenticates payload type plus exact bytes.
- HTTP Message Signatures and `Content-Digest` remain useful transport checks,
  but RFC 9421 and RFC 9530 require an application profile and do not supply
  durable rollback/freeze state.
- Full TUF is stronger for delegated targets, root recovery, mirrors, and
  autonomous trust rotation. This smaller profile intentionally reproduces
  only the timestamp/targets pattern needed for one locally configured channel.
- Merging a gateway into ModelSkyline was rejected. Native consumers should
  implement the JSON/schema/fixture contract in their own runtime language.

## Consequences

Automatic frontier updates can become invisible to an agent while remaining
auditable and fail-closed. Gateways bear explicit durable-state, exact-mapping,
capability, pinning, retry, and receipt responsibilities. Operators must manage
signing keys and a monotonically increasing channel sequence. Cross-language
conformance fixtures and a second independent verifier are release gates before
calling the profile stable.
