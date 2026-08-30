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

Snapshot hashes detect corruption and provide content identity; they are not
signatures. A party that can replace a manifest can also recompute its hash.
Resolvers therefore require HTTPS by default and should pin expected selection,
frontier, and workload identities. Use a trusted origin until signed-manifest
support exists.

The repository owner must enable GitHub private vulnerability reporting as
part of creating the public repository. If the private reporting button is not
visible, do not include confidential details in a public issue.
