# Published solutions

This directory contains every ReHyra-produced solution promoted to a primary
reported result. It is not a dump of run workspaces.

`manifest.toml` is the source of truth. Every task in the seven-task public
suite appears exactly once:

| Task | Status | Result |
|---|---|---:|
| Smallest Adder | published | 35 parameters |
| Sums vs. Diffs | published | 1.1600932741088714 |
| First Autocorrelation | published | 1.5028628243951712 |
| Second Autocorrelation | no new solution | seed retained |
| Erdős Minimum Overlap | published | 0.3808674981608905 |
| Qubit Routing | published | 298,353 added CNOTs |
| Sunspot Symbolic | published | confirmation R² 0.6967635448250007 |

A result is published here only when it:

1. was produced or modified by a ReHyra Proposal Agent;
2. became the qualified incumbent, or passed a separately declared
   confirmatory protocol;
3. has a frozen evidence record and independently checked result;
4. can be redistributed as compact source or construction data.

Unchanged seeds, failed proposals, rejected candidates, and superseded runs
remain in `evidence/`; they are not presented as solutions. A task with no
promoted candidate is recorded explicitly as `no_new_solution` rather than
silently omitted.

## Reproduction

The bundles preserve exact source and construction bytes from the frozen runs.
Their hashes and evidence records are checked by:

```bash
python -m unittest tests.test_solutions
```

The latest source-to-result publication audit is stored in
`evidence/solution-publication-verification.json`.

Bootstrap the pinned scientific tasks before executing the Python and Rust
solutions:

```bash
python -m pip install -e '.[science]'
rehyra bootstrap-science \
  --output task-data/low-cost-science \
  --tasks autocorrelation_first,autocorrelation_second,erdos_min_overlap,sums_diffs,qubit_routing \
  --seed-source predecessor \
  --metric-mode auto
```

The first-autocorrelation and Erdős programs use the bootstrapped predecessor
through `SIMPLETES_SHARED_CONSTRUCTION_PATH`. Their checked-in
`construction.json` files allow the final objective to be verified without
rerunning the long optimization.

For the adder candidate:

```bash
rehyra bootstrap-adderboard --output task-data/adderboard
python benchmarks/solutions/adderboard_gauge_35p/materialize.py \
  --workspace task-data/adderboard/seed-workspace
```

For Sunspot, bootstrap the pinned public data and use
`benchmarks/tasks/sunspot/evaluate.py`. Qubit Routing requires the full
SimpleTES Rust toolchain and suite; the published `solution.rs` is the exact
qualified candidate.

## Provenance and licenses

Each manifest entry links the solution to its frozen evidence and names the
relevant upstream license. Some task scaffolds derive from SimpleTES and remain
subject to AGPL-3.0-or-later; the TTT-Discover predecessor is MIT licensed, and
the M10S adder seed is MIT licensed. Upstream baselines are fetched by the
bootstrap commands instead of being copied into these bundles.

The complete license texts, pinned upstream commits, and per-bundle
modification notices are in [`../LICENSES/README.md`](../LICENSES/README.md).
They live outside the bundles so the frozen source and construction hashes stay
identical to the independently audited records.
