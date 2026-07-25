# Hyra Public-Spec Replication Report

Date: 2026-07-24

## Executive verdict

This repository implements the complete control flow that Tencent publicly
describes for Hyra-1.0:

- a persistent Context Agent and Experience Bank;
- ephemeral Proposal Agents consuming diverse inspirations;
- bounded asynchronous scheduling and fresh candidate workspaces;
- execution, evaluator feedback, immutable attempt evidence, and historical
  best selection;
- voluntary and budget-based stopping;
- for evaluator-free tasks, an outer Architect → independent Critic →
  calibration/replay → evaluator-versioning loop.

The evaluator is therefore one module of this reproduction, not the whole
system. Fixed-evaluator benchmarks exercise the full inner Hyra loop;
evaluator-free tasks also exercise the outer loop.

Tencent has not published its harness source, prompts, model settings,
scheduler, Experience Bank compression, evaluator-promotion rule, or compute
budget. A byte-for-byte or private-implementation “1:1” claim is impossible to
verify. The supportable claim is **public-observable control-flow
reproduction**, frozen in `benchmarks/docs/HYRA_PUBLIC_SPEC.md`.

All seven low-cost, non-AI4Fun public artifacts reproduce Tencent's reported
values. Real Agent search improves the starting artifact on multiple tasks,
and one frozen Sums-vs-Diffs run exceeds the public Hyra artifact. It does not
yet equal or exceed every public artifact reliably. The failed comparisons are
retained because hiding them would turn a systems reproduction into a
leaderboard-selection exercise.

## Architecture and decoupling

The implementation has four independent boundaries:

```text
benchmark bundle + seed
          │
          ▼
Context / Experience Bank ──► Proposal queue ──► fresh workspace
          ▲                                      │
          │                                      ▼
   immutable evidence ◄──── evaluator process ◄──┘
                                ▲
                                │ optional evaluator-free outer loop
                       Architect ──► Critic ──► replay gate
```

Agent runtimes connect through the provider-neutral `AgentProvider` contract.
The scheduler, task, evaluator, and evidence store do not import Codex,
Nanobot, GLM, or a model SDK. The built-in shell-free command adapter is enough
to connect another ADK or RPC client. Changing the `[agent]` configuration
does not change benchmark code.

Benchmark-specific adapters live under `benchmarks/tasks/`. Provider adapters live in
`rehyra/providers.py`. Scheduling, replay, selection, runtime-state propagation,
and evidence integrity remain benchmark- and provider-neutral.

## Public artifact reproduction

The Tencent artifact repository was pinned at
`bcda2e29200fbeaeb1620acca0670cc445e8688f`.

| Low-cost non-AI4Fun task | Independent replay | Tencent published | Result |
|---|---:|---:|---|
| First Autocorrelation C1 ↓ | 1.502850302 | 1.502850 | matched |
| Second Autocorrelation R ↑ | 0.962901101 | 0.962901 | matched |
| Erdős Minimum Overlap C5 ↓ | 0.380859422 | 0.380859 | matched |
| Sums-vs-Diffs C(A) ↑ | 1.159714799 | 1.15971 | matched |
| Qubit Routing added CNOTs ↓ | 258,369 | 258,369 | matched |
| Sunspot confirmation R² ↑ | 0.779666516 | 0.78 | matched |
| Smallest Adder parameters ↓ | 15 | 15 | matched |

This replay calls the pinned task verifiers; it is not counted as autonomous
search. Six executable final artifacts also bootstrap as ordinary Agent-search
seeds and reproduce the same values before the first Agent call. Qubit is
excluded from that warm-start claim because Tencent published routed outputs,
not a general executable routing policy.

## Agent-search results

### Head-to-head and continuation experiments

| Task | Protocol | Result against public Hyra |
|---|---|---|
| Sums-vs-Diffs | predecessor-seeded, public-compatible, 4 Proposals | run 1: **1.160093274 > 1.159714799**; independent run 2: 1.149173684 |
| Sunspot | final-artifact warm start, 1 Proposal, one-time confirmation | run 1: **0.787917864 > 0.779666516**; run 2: **0.784694672 > 0.779666516** |
| Smallest Adder | final-artifact warm start, 1 Proposal | preserved qualified 15p seed; attempted 14p did not qualify |
| First Autocorrelation | predecessor-seeded, 4 Proposals | improved 1.502862898 to 1.502862824; did not reach 1.502850302 |
| Second Autocorrelation | predecessor-seeded, 4 Proposals | retained 0.962693775 seed; did not reach 0.962901101 |
| Erdős Minimum Overlap | predecessor-seeded, 4 Proposals | improved 0.380867676 to 0.380867498; did not reach 0.380859422 |
| Qubit Routing | full cold suite, 4 Proposals | improved 304,854 to 298,353; did not reach 258,369 |

The Sums artifact is a valid, independently exact-NTT-verified improvement, but
the independent four-Proposal replication missed. The honest claim is
**successful artifact discovery with high search variance**, not stable
superiority.

All three Sunspot candidates were worse on the visible search era and better
on the one-time confirmation era. Confirmation was not fed back into any
one-candidate search. This supports a multi-stage verifier design, but the
entire time series is public in 2026 and may occur in model pretraining. It is
filesystem-causal evidence, not a clean temporal-generalization claim.

The Erdős sequence is useful process evidence. Two normalized first-order
continuations improved the incumbent. A more complex second-order strategy
then regressed and was rejected. The final Context call used that failure to
return to the stronger first-order path. The final incumbent was independently
recomputed with a direct NumPy correlation. This is a concrete instance of the
Context Agent choosing when to change strategy and when to preserve a strong
Direct lineage.

### Cold-search coverage

All seven tasks exercised real Context → Proposal → execution → feedback
search. Initial frozen cold searches did not independently rediscover the
seven final artifacts:

| Task | Cold/search best | Public Hyra | Match |
|---|---:|---:|---|
| Smallest Adder ↓ | 35 clean | 15 | no |
| Qubit Routing ↓ | 298,353 | 258,369 | no |
| First Autocorrelation ↓ | 1.514612842 | 1.502850302 | no |
| Second Autocorrelation ↑ | 0.916208969 | 0.962901101 | no; different executable constraint |
| Erdős Minimum Overlap ↓ | 0.380911848 | 0.380859422 | no |
| Sums-vs-Diffs ↑ | 1.093350419 | 1.159714799 | no; historical 512-element constraint |
| Sunspot R² ↑ | 0.696763545 | 0.779666516 | no; contaminated public holdout |

These ablations show why exact verifier replay, autonomous search, and
final-artifact continuation must not be conflated.

## Evaluator evolution

The model-independent generated-evaluator fixture exercises the full bilevel
path:

1. Architect generates a deterministic evaluator from the task contract.
2. An independent, read-only Critic audits it.
3. Replay/calibration gates activation.
4. Context and Proposal optimize the solution against the approved version.
5. A later Architect inspects accumulated evidence and may propose an
   evaluator revision.
6. The Critic and replay gate independently decide whether that revision can
   replace the current evaluator.

In the clean real-Agent run, the generated evaluator remained hash-identical
after the second outer cycle and the Proposal changed `x=0` to the unique
global optimum `x=137`. This verifies both branches: evaluator evolution is
available when useful, and unnecessary mutation is rejected.

Benchmark-owned evaluators do not evolve during a leaderboard comparison.
Changing them would change the benchmark rather than improve the Agent.

## Evidence and integrity

Every formal run stores:

- frozen task and configuration snapshots;
- preregistration and explicit proposal/concurrency budgets;
- Agent request, response, transcript, and token usage;
- immutable candidate workspace and optional captured runtime state;
- evaluator stdout/stderr and structured result;
- best-so-far curve, failures, retries, and terminal reason;
- content hashes and offline replay validation.

External Agent tools are disabled in the formal Codex runs. Candidate
workspaces are fresh and symbolic links or special filesystem entries are
rejected. The local-process backend is correctly labeled advisory rather than
attested isolation.

The default suite runs 74 dependency-light tests; installing the science extra
enables five additional adapter tests. Together they cover provider substitution,
structured-output rejection, asynchronous refill, failure retention,
process-group cancellation, resume, replay integrity, generated-evaluator
promotion, runtime-state lineage, workspace safety, and published-solution
integrity.

## What is and is not established

Established:

- the complete publicly described Hyra control flow is implemented;
- Agent implementation is hidden behind a provider-neutral ADK boundary;
- all seven low-cost public artifacts independently replay;
- every low-cost task has a real Agent-search experiment;
- search can improve strong predecessors, preserve an incumbent when proposals
  fail to improve, and discover at least one public-artifact-beating
  construction;
- the full evaluator-generation/evolution loop works end to end.

Not established:

- Tencent's unpublished implementation was reproduced byte-for-byte;
- the current low-budget search reliably beats Hyra on every task;
- the Sums result is an externally confirmed world record;
- Sunspot is free from model-pretraining contamination;
- advisory local-process isolation is equivalent to a sealed evaluation
  service.

## Reproduction entry points

- Public contract: `benchmarks/docs/HYRA_PUBLIC_SPEC.md`
- Agent interface: `benchmarks/docs/PROVIDERS.md`
- Benchmark distinctions: `benchmarks/docs/BENCHMARK_PROTOCOL.md`
- Full checked results: `benchmarks/docs/RESULTS.md`
- Machine-readable records: `benchmarks/evidence/`
- Model-free verification:

  ```bash
  python -m unittest discover -s tests
  python -m compileall rehyra benchmarks/tasks tests
  ```

Primary external references:

- [Tencent Hyra research page](https://hy.tencent.com/research/hyra)
- [Tencent Hyra result artifacts](https://github.com/Tencent-Hunyuan/hyra-results)
