# Agent Guidelines

This repository is an independent, public-spec reimplementation of the Hyra
research loop. It is not Tencent's unreleased Hyra harness.

## Boundaries

- Keep the orchestration core benchmark-agnostic.
- Keep Codex-specific workflow files under `codex-plugin/`; the Adapter may
  import `rehyra`, but the core must never import the Adapter.
- Put benchmark-specific bootstrapping and evaluation under `benchmarks/tasks/`.
- Treat upstream evaluators as immutable external sources pinned by commit.
- Do not vendor unlicensed upstream code; fetch it during task bootstrap.
- Do not put this experiment into Open Recursive core.
- Use shell-free argument arrays for child processes.
- Keep every attempt, including failures, immutable and auditable.

## Python and tests

- Target Python 3.11+ and prefer the standard library.
- Use `pathlib.Path`, dataclasses, and explicit JSON/TOML files.
- Default tests must not call models, networks, or provider authentication.
- Run:

```bash
python -m unittest discover -s tests
python -m compileall rehyra codex-plugin benchmarks/tasks tests
```
