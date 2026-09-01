# Security notes for the LiteLLM projection

This experimental package inherits the repository's
[security policy](../../SECURITY.md). Report suspected vulnerabilities through
GitHub private vulnerability reporting; do not publish secrets or exploit
details in an issue.

## Trust boundary

The controller assumes that all of the following are trusted and
operator-controlled:

- the local regular file containing the `SelectionSnapshot`;
- the local integration configuration and its complete offering bindings;
- the host clock and process environment;
- the configured LiteLLM HTTPS origin, certificate validation, admin API, and
  database; and
- the process that serializes router-settings mutations.

The selection content hash detects mutation but does not authenticate its
publisher. The CLI uses the core resolver's bounded, no-follow regular-file
read and disables last-known-good staleness for each invocation, but local
filesystem permissions remain part of the trust boundary. Version 0.1 alpha
has no signature verification or durable monotonic checkpoint.

Selection metadata is not trusted to define execution targets. A choice must
match the canonical identity of an operator-configured complete `OfferingKey`
exactly. Only then is it projected to a local target template. The template
schema accepts a model identifier, named-credential identifier, and revision
digest; it does not accept an endpoint, API key, provider header, or arbitrary
LiteLLM parameter.

## Credentials and target identity

The LiteLLM master key is read only from `LITELLM_MASTER_KEY`. It has no command
line or configuration-file field. The management client requires HTTPS unless
plain HTTP is explicitly enabled for a loopback integration test, disables
redirects and ambient proxy environment variables, bounds response bodies, and
does not include raw response bodies in errors.

Disabling ambient environment configuration also means proxy variables and
environment-supplied private CA bundles are ignored. This alpha exposes no
explicit custom-CA option; the LiteLLM certificate must validate with the
client's default trust store (or an operator-owned TLS terminator must do so).
The full model catalog and config response are capped at 2 MiB and fail closed,
which may require a reviewed pagination/change before unusually large installs.

The pinned alias read uses LiteLLM's hidden `/get/config/callbacks` Admin UI
endpoint because `v1.98.0` has no dedicated alias-read API. That response is
not router-only: for a full administrator it can also include callback and
alert configuration whose generic values are not all redacted. The controller
does not log or retain that response beyond reconciliation, but unrelated
configuration can enter process memory. Use a dedicated, isolated controller
process and treat it as fully privileged. A documented least-privilege
router-settings endpoint is an upstream requirement before production use.

Provider keys, endpoints, and other target parameters live in named LiteLLM
credentials provisioned outside this controller. The integration configuration
therefore contains a credential **name**, not a credential. Protect that file
anyway: target names and offering topology may still be operationally
sensitive, and validation cannot recognize every string that an operator might
mistakenly use as a secret.

The target `revision` is an operator attestation, not a cryptographic reading of
LiteLLM state. A credential can be changed in LiteLLM without changing its name
or the projected revision. To preserve target identity:

1. use a new, versioned credential name for every reviewed target revision;
2. restrict mutation of named credentials independently of this controller;
3. derive and record the revision through an operator-owned review process; and
4. rotate the integration configuration and stage a new selection group rather
   than editing a credential behind an active group.

A revision digest is an identity attestation, not a monotonic sequence. The
controller can distinguish two same-snapshot target projections but cannot
prove which one is newer, so a later operator can reactivate either. Deploy an
external monotonic policy if same-snapshot target rollback must be prohibited.

Never place master or provider keys, credential payloads, target URLs, tenant
data, prompts, or responses in a selection, binding, revision label, test
fixture, log, or issue.

## Staging integrity

The projection and deployment IDs are deterministic from the snapshot,
complete offerings, and selected local-target fingerprints. Rotating a target
attestation therefore produces a different immutable group even if the
selection is unchanged. A stage reads the LiteLLM catalog, rejects a
deterministic ID used by another group, creates missing rows, and verifies the
exact group before returning. "Exact" here means the top-level row shape, all
authored execution parameters, pinned inactive LiteLLM parameter defaults, and
pinned access/block/rate controls. LiteLLM `v1.98.0` also merges live provider
capability and pricing metadata into `model_info`; the verifier treats that
enrichment as non-authoritative rather than incorporating it into target
identity.
The stable alias is never touched by `stage`, so a partial group is not promoted
by this controller.

This is idempotent reconciliation, not a database transaction. A management
request can commit before a timeout. The controller reads by deterministic
identity after such a failure and succeeds only when the exact expected row is
visible; otherwise it fails closed. It does not clean up partial rows.

## Alias activation and single-writer requirement

LiteLLM `v1.98.0` has no compare-and-swap operation for the alias map. The
controller reads the complete map twice, refuses a detected preflight change,
writes the complete intended map, and verifies the readback. A concurrent
writer can still change state between those operations, and an unrelated
router update that omits `model_group_alias` can reset aliases in this pinned
version.

Use one external lock or controller identity for **every** router-settings
writer, including UI operations and unrelated automation. Do not infer safety
from the two preflight reads. Review the pinned version's
[`/config/update` alias issue](https://github.com/BerriAI/litellm/issues/36446)
before deployment and repeat integration tests before upgrading LiteLLM.

Activation also rejects:

- a stable alias that currently points to an unmanaged group;
- a visible older generation replacing a newer managed generation;
- different snapshots with the same visible generation time; and
- non-empty global `fallbacks` or `*_fallbacks` settings that could widen the
  managed candidate set.

That last check does not cover key- or team-level routing policy. Execute the
managed alias only through a dedicated LiteLLM key and team whose policy has no
fallback capable of widening the selected group. The alpha controller cannot
inspect or enforce every request-scoped policy, so sharing a key/team with
independent routing automation invalidates the bounded-candidate claim.

Those checks depend on the current mutable LiteLLM rows and alias map. They are
not durable anti-rollback protection if that state is deleted, replaced, or
restored from an older backup.

If activation reports an indeterminate outcome, do not blindly retry. A write
may have committed even though the request failed. Stop automated promotion,
read the entire alias map and managed group through an independent operator
path, compare them with the content-free plan, and resolve any concurrent
writer before proceeding. There is no automated rollback command.

## Data-plane limitations

The stable alias is a routing convenience, not a ModelSkyline work-unit lease.
This package does not guarantee that an already-started multi-request agent
trajectory remains on its admitted snapshot after alias rotation. LiteLLM
session affinity pins a deployment under its own semantics, not the exact
ModelSkyline generation and ordered tuple.

The controller also cannot coordinate nested SDK retries, partial streams, or
non-idempotent tool/provider side effects. The execution consumer must own one
bounded attempt/deadline budget, stop fallback after response bytes or a side
effect, and retain a generation for the full logical work unit when those
properties matter.

Keep old versioned groups for at least the maximum admitted work-unit lifetime.
Deletion and garbage collection are manual and outside this package. Confirm
that no active work refers to a group before removing it.

## Logging and evidence

The CLI's plan output contains identifiers and hashes, not target routes or
credential names. The client and reconciliation errors are intentionally
content-free and do not log management payloads or responses. LiteLLM, its
database, reverse proxies, and surrounding process supervision have separate
logging behavior; configure and review them independently.

Unit tests with a modeled admin API establish controller behavior only. The
checked-in pinned-container test additionally establishes only the bounded
single-worker, non-streaming routing and restart observations listed in the
README. Neither establishes production security. Deployment claims must name
the exact image digest and remain limited to behavior actually observed by
that test on the deployed platform.
