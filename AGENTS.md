# Repository instructions

## Worktrees

- When creating a Git worktree, use `wt switch --create <branch>` instead of
  `git worktree add`. Worktrunk runs the repository's post-create setup hooks;
  bypassing it can leave dependencies, build artifacts, secrets, or other local
  prerequisites uninitialized.
- Use `wt list`, `wt switch`, and `wt remove` for normal worktree lifecycle
  operations. Use raw `git worktree` commands only when explicitly requested or
  when Worktrunk cannot perform the required operation, and explain the
  fallback.

## Development

- Preserve the language-neutral contracts in `schemas/` and regenerate them
  with `modelskyline regenerate-schemas schemas` after Pydantic model changes.
  `export-schemas` intentionally copies the committed release contracts.
- Use `Decimal` for policy and cost calculations. Do not introduce binary
  floating-point money arithmetic.
- Never execute configured Python expressions or dynamically import an oracle
  from public configuration.
- Keep offering identity narrower than model identity whenever price,
  provider, region, tier, quantization, or harness can differ.
- New upstream data adapters must retain source URL, retrieval/effective time,
  methodology/version, and license/terms metadata.
- Run `ruff`, `mypy`, and `pytest` before handing off changes.
