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

For untrusted distribution, use the signed gateway profile in
[`ADR 0003`](docs/adr/0003-signed-gateway-selection-protocol.md). Its security
boundary includes locally provisioned Ed25519 keys, audience/channel policy,
durable sequence state, exact artifact bytes, and immutable local target
revisions. Production private keys never belong in ModelSkyline inputs, test
vectors, publications, or the resolver host. The committed `*.test-seed.hex`
files are public deterministic conformance inputs, not credentials. Place the
SQLite state in an operator-owned private directory and retain the returned
route for one work unit; deleting or restoring checkpoint state can reopen a
rollback window. The reference store enforces mode `0700` on its final parent
and mode `0600` on the database/WAL/shared-memory files; create a dedicated
directory rather than placing the database directly in a typical `0755`
checkout or service working directory.

Trusted UTC is part of the signed resolver's security boundary. The reference
resolver fails closed on an observed backward clock step, compares restart time
to durable installation time, and rechecks time after network I/O. Monitor host
time synchronization; rolling back both the clock and private state directory
is outside the process-local defense.

The repository owner must enable GitHub private vulnerability reporting as
part of creating the public repository. If the private reporting button is not
visible, do not include confidential details in a public issue.
