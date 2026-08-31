# Synthetic three-benchmark quality gate

This self-contained example builds and verifies a quality-gated selection in
memory. It uses three fictional benchmark workloads—code repair, reasoning, and
tool use—plus a primary cost/performance workload. All values, model names, and
organizations are synthetic and are not claims about real models.

Run it from the repository root:

```console
uv run --no-env-file python examples/quality-gated/example.py
```

The example demonstrates:

- exact, complete `OfferingKey` identity across the primary and benchmark data;
- a required three-component quality bundle with explicit coverage records;
- exclusion of one route because it is missing the tool-use benchmark;
- Pareto and proximity recomputation after that hard exclusion;
- overlap ranking that selects the route on all three quality frontiers over a
  cheaper route on only two; and
- full source-backed replay verification of both the bundle and final selection.

The three components are operator-declared as separate workloads. Different
IDs, signals, and content hashes do **not** prove statistical independence or
prevent correlated benchmark results. A real operator must make and document
that judgment.

The script performs no network access and writes no files; its report goes only
to standard output.
