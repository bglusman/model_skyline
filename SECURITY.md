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
into the managed root.

New publication roots and artifacts default to owner-only permissions (`0700`
for the root and `0600` for files on POSIX). Existing directory permissions are not changed.
When a different service identity must host public output, copy it into a
separate read-only serving tree and set that tree's permissions deliberately.

Snapshot hashes detect corruption and provide content identity; they are not
signatures. A party that can replace a manifest can also recompute its hash.
Resolvers therefore require HTTPS by default and should pin expected selection,
frontier, and workload identities. Use a trusted origin until signed-manifest
support exists.

The repository owner must enable GitHub private vulnerability reporting as
part of creating the public repository. If the private reporting button is not
visible, do not include confidential details in a public issue.
