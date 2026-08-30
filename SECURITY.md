# Security policy

This project is pre-release. Please report suspected vulnerabilities privately
to the repository maintainers rather than opening a public issue. A dedicated
security contact will be added before the first public release.

High-priority reports include formula or YAML/JSON code execution, snapshot
hash bypass, unsafe URL/file resolution, oracle command injection, trace data
disclosure, and provider credential leakage.

ModelSkyline snapshots never need provider API keys. Keep secrets in the
execution gateway or adapter environment and do not place them in policy,
observations, metadata, RSS, or published artifacts.

Snapshot hashes detect corruption and provide content identity; they are not
signatures. A party that can replace a manifest can also recompute its hash.
Resolvers therefore require HTTPS by default and should pin expected selection,
frontier, and workload identities. Use a trusted origin until signed-manifest
support exists.

Before public launch, the repository owner must enable private security
advisories or publish a monitored security email here. Until then, this checkout
is not accepting confidential vulnerability material.
