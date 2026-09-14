# Gateway pointer v1alpha1 conformance vectors

These deterministic files exercise ADR 0003 without requiring the Python
package. They are intended for independent Go, Elixir, Rust, TypeScript, and
other native gateway consumers.

[`compatibility.json`](compatibility.json) is the machine-readable index for
consumer implementations. It pins the protocol features and limits plus the
exact length, SHA-256, media type, role, and stable ID of every required schema
and public fixture. Validate it with
[`gateway-consumer-compatibility.schema.json`](../../../schemas/gateway-consumer-compatibility.schema.json),
then verify every indexed byte before running the vectors. Its
`resource_set_sha256` is a compact fixture-set identifier, not authentication;
the release itself must arrive through a trusted pinned channel.

`valid/payload.json` has no trailing newline: its exact bytes are the DSSE
payload. `artifacts/publication.json` and `artifacts/selection.json` likewise
retain the exact published bytes whose raw SHA-256 and lengths appear in the
pointer. `valid/expected.json` records the DSSE PAE, signature, four hash
domains, checkpoint, and ordered local target IDs. The `intermediate/` files
expose the exact JWK thumbprint input, decoded canonical pointer, DSSE PAE, and
semantic publication/selection hash inputs so another runtime can isolate
canonicalization disagreements without relying on Python.

Pointer `audience` and `selection.required_capabilities` arrays must be
duplicate-free and sorted in ascending Unicode code-point order (currently
equivalent to ASCII lexical order for their allowed identifier alphabets).

Normative structure is in the three gateway JSON Schemas:

- [`gateway-selection-pointer.schema.json`](../../../schemas/gateway-selection-pointer.schema.json)
- [`gateway-selection-envelope.schema.json`](../../../schemas/gateway-selection-envelope.schema.json)
- [`gateway-trust-policy.schema.json`](../../../schemas/gateway-trust-policy.schema.json)

The verification and authorization rules that schemas cannot express are in
[ADR 0003](../../../docs/adr/0003-signed-gateway-selection-protocol.md).

The two `*.test-seed.hex` files are public, non-confidential deterministic
Ed25519 private-key test material. They remain package-shipped so independent
producer implementations can reproduce the expected signatures, but they must
never be used for a production signing key. Production private keys never
belong in ModelSkyline configuration, publications, traces, fixtures, or source
control. The compatibility index deliberately excludes the test seeds because
a verifier needs only the public JWKs and signed bytes.

Expected decisions at the time in `valid/expected.json`:

- `valid/envelope.dsse.json` accepts under `valid/trust-policy.json`;
- `valid/threshold-2-of-2.dsse.json` accepts under the threshold-two policy;
- `valid/rotation-same-payload.dsse.json` has the same decoded payload identity
  with an additional valid signature;
- every file under `invalid/` rejects for the reason encoded by its name;
- `expired.dsse.json` rejects at its exact `hard_expires_at` boundary;
- `rollback.dsse.json` and `same-sequence-different-payload.dsse.json` reject
  after the valid sequence-7 checkpoint has been installed.

Regenerate using the locked development environment:

```console
uv run --extra gateway python scripts/generate_gateway_conformance.py
```

The repository tests independently recompute the expected PAE/signature/hash
values and exercise each accept/reject decision.
