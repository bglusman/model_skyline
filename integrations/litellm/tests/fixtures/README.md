# Fixture provenance

`selection-a.json` and `selection-b.json` are ordinary ModelSkyline
`SelectionSnapshot` artifacts generated from the repository's CC0 synthetic
coding-session example. They contain no real model claims, prompts, responses,
credentials, endpoints, or user data.

Snapshot A orders the synthetic `qualityworks` offering before `balancedai`.
For snapshot B, the generator changes only synthetic quality and price evidence
so both offerings remain non-dominated while the order reverses. `bindings.json`
maps their complete `OfferingKey` values to fake named-credential templates.

Verify committed bytes:

```console
python scripts/regenerate-fixtures.py
```

Regenerate intentionally:

```console
python scripts/regenerate-fixtures.py --write
```
