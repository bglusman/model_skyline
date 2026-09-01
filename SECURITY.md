# Security policy

This project is pre-release. Please report suspected vulnerabilities through
GitHub private vulnerability reporting: open the repository's **Security** tab,
choose **Advisories**, and select **Report a vulnerability**. Do not open a
public issue for an undisclosed vulnerability.

High-priority reports include formula or YAML/JSON code execution, snapshot
hash bypass, unsafe URL/file resolution, oracle command injection, trace data
disclosure, and provider credential leakage.

ModelSkyline snapshots never need provider API keys. Keep secrets in the
execution gateway or adapter environment and do not place them in policy,
observations, metadata, RSS, or published artifacts.

The models.dev projection copies reviewed mapping `evidence` into offering
metadata. Mapping files are public-artifact inputs, not private reviewer notes;
do not include credentials, personal data, customer identifiers, private paths,
or confidential context. Remote pricing retrieval is limited to the exact
official `https://models.dev/api.json` URL. Use a bounded local file for mirrors
or operator-supplied compatible catalogs, which receive unasserted provenance
unless an exact hash-pinned local snapshot is explicitly asserted as official.

Framework adapters accept narrower accounting projections than the raw
runtime surfaces. Keep Codex JSONL, Claude result/transcript state, OpenClaw
events, Hermes reports/databases, collector HMAC keys, and pseudonymization
keys outside the repository and publication tree. Raw framework data can hold
prompts, responses, commands, paths, tool arguments, session identifiers, and
credentials even when the returned canonical trace does not. Route and outcome
attestations are operator claims, not cryptographic proof. Only exact reviewed
producer/version tuples are eligible for trusted trace provenance; adding a
producer requires source, license/terms, schema, privacy, and failure-path
review.

`publish-project --public` is a redistribution guard, not a PII, prompt, or
secret scrubber. It requires an HTTPS base URL and explicit license or
source-id authorization for every cited source in retained history, but it
cannot determine that the operator owns the data or that its metadata,
methodology, endpoint URLs, prompts, and tool results are safe to disclose.
Perform a separate privacy, credential, and source-rights review before serving
the output. A source-id override should refer to separately documented
redistribution authority; it is not evidence of that authority by itself.

Use a dedicated publication directory. The output root **and all of its parent
directories must be exclusively writable by the publisher identity**. The
publisher rejects symlink components and unmanaged entries, uses an advisory
single-writer lock, and commits immutable files before replacing the root
manifest last. Those checks support crash recovery and reduce accidental path
confusion, but they contain check-then-use windows and do not defend against a
hostile concurrent local user who can mutate the root or its parents. Publish
on a trusted filesystem, then expose a copy or read-only view to untrusted
consumers. Do not mix operator notes, web assets, or other applications' files
into the managed root. The root and its parent must share a filesystem so
temporary files can be staged as unguessable siblings and atomically committed.
Only the creating process removes those files; a hard crash can leave a sibling
for operator inspection and cleanup, but no pre-existing file is deleted based
only on a temporary-looking name.

New publication roots and artifacts default to owner-only permissions (`0700`
for the root and `0600` for files on POSIX). Existing directory permissions are
not changed. When a different service identity must host public output, copy it
into a separate read-only serving tree and set that tree's permissions
deliberately.

Snapshot hashes detect corruption and provide content identity; they are not
signatures. A party that can replace a manifest can also recompute its hash.
The unsigned `DynamicResolver` therefore requires HTTPS by default and should
pin expected selection, frontier, and workload identities on a trusted origin.
Network source URLs cannot contain query strings or fragments, so do not put
credentials in a resolver URL. Local-file sources require explicit opt-in and
POSIX no-follow, nonblocking regular-file reads. Generation rollback and
same-generation equivocation checks retain only an in-process floor. Restarting
the resolver loses that floor, and `stale_if_error` can deliberately extend use
past a snapshot's nominal TTL after a refresh failure.

Version 0.9 does not ship an authenticated untrusted-distribution protocol or
durable anti-rollback store. If those properties are required, place the
ordinary `SelectionSnapshot` behind an operator-chosen authenticated channel,
persist an independently protected monotonic checkpoint, bind each complete
`OfferingKey` to an immutable local target, and review that consumer as part of
the deployment security boundary. Superseded
[`ADR 0003`](docs/adr/0003-signed-gateway-selection-protocol.md) is historical
design input, not a supported 0.9 security profile.

Trusted UTC is part of selection validation. Monitor host time synchronization;
process-local checks cannot defend against an attacker who can roll back both
the clock and the resolver's private state.

The repository owner must enable GitHub private vulnerability reporting as
part of creating the public repository. If the private reporting button is not
visible, do not include confidential details in a public issue.
