# Hyra Public-Observable Specification

This document freezes what Tencent publicly describes for Hyra-1.0 and
separates it from implementation details that Tencent has not released.

Primary sources:

- [Hyra launch page](https://hy.tencent.com/research/hyra), retrieved
  2026-07-23.
- [Hyra result artifacts](https://github.com/Tencent-Hunyuan/hyra-results),
  commit `bcda2e29200fbeaeb1620acca0670cc445e8688f`.

## Observable contract

The public harness has these components and behaviors:

1. A Context Agent maintains an Experience Bank containing source, files,
   artifacts, execution logs, evaluator feedback, successes, and failures.
2. The Context Agent continuously synthesizes diverse inspiration contexts
   from the bank and places them on a task queue.
3. Multiple Proposal Agents asynchronously consume inspirations. Each writes a
   complete `solution/` directory whose entry point is `solve.sh`.
4. Every solution runs and receives a score in a fresh isolated sandbox.
5. Results and artifacts return to the Experience Bank.
6. Semaphores bound scarce resources such as model calls, sandboxes, and queue
   capacity.
7. The loop stops when an agent voluntarily exits or the budget is exhausted
   and returns the historical best solution.
8. If the task has no evaluator, Hyra creates an initial evaluator from the
   task description. After an inner solution loop, it inspects accumulated
   experience and may improve evaluator efficiency, feedback resolution,
   reward-hacking resistance, or remaining headroom. A new inner loop then
   runs against the upgraded evaluator.

The implementation must preserve all eight behaviors. The evaluator-free path
is a bilevel loop; a fixed-evaluator benchmark uses only the inner loop.

## Not publicly observable

Tencent has not released the Hyra harness, prompts, model/provider choices,
sampling settings, proposal count, evaluator-query budget, scheduling policy,
Experience Bank compression policy, evaluator promotion test, or compute
budget. It is therefore impossible to reproduce those private details
one-for-one from public evidence.

This project labels every choice in that set as an implementation choice. It
uses “public-spec reproduction”, never “Tencent implementation reproduction”.

## Local implementation choices

- Agents are injected through a provider-neutral Agent Development Kit
  protocol. Tasks do not import Codex, Nanobot, or any model SDK.
- Queue refill is asynchronous and bounded by explicit semaphores.
- Every Proposal Agent is ephemeral and receives one copied parent workspace
  plus selected Experience Bank evidence. Additional attempts selected for
  recombination are exposed as bounded source snapshots during the Agent call
  and the selected parent's validated runtime artifact is exposed for
  artifact-level continuation. Both are removed before candidate execution.
- Agent-visible evaluator results exclude evaluator argv, absolute paths, and
  execution-backend details. Raw evidence retains them for audit. This keeps a
  sealed evaluator or data location out of the agent feedback channel.
- Local process isolation is advisory unless the selected execution backend
  attests a stronger boundary.
- Evaluator upgrades are versioned. They cannot overwrite past evaluators or
  evidence, and historical artifacts are replayed before an upgrade is used.
- A fixed benchmark-owned evaluator is immutable. Bilevel evolution is only
  enabled for evaluator-free tasks or an explicitly separate research
  evaluator.

## Conformance matrix

The evaluator is one component, not the harness. Fixed-evaluator benchmarks run
the complete inner search loop; evaluator-free tasks additionally run the
outer evaluator loop.

| Public behavior | Local implementation | Required local evidence |
|---|---|---|
| Context Agent + Experience Bank | implemented | Context call transcript and bank snapshot |
| Asynchronous Proposal Agents | implemented | queue, worker, and bounded-capacity events |
| Fresh solution sandbox | implemented; local backend is advisory | unique workspace and execution-backend attestation |
| Source/log/result return | implemented | immutable attempt directory and hashes |
| Historical best | implemented | replayable incumbent curve |
| Voluntary stop | implemented | structured stop decision in Context output |
| Hard budget stop | implemented | frozen budget and terminal reason |
| Evaluator creation | implemented for evaluator-free tasks | versioned evaluator artifact and creation transcript |
| Evaluator evolution | implemented for evaluator-free tasks | audit, replay comparison, and promotion record |

The independent Critic, immutable harness snapshot, provider-neutral ADK, and
fail-closed replay gate are local safety choices. Tencent's public description
does not establish whether its private harness uses those exact mechanisms.
Conversely, Tencent's prompts, model selection, session persistence, resource
scheduler, compression policy, and compute budget remain unobservable and are
not claimed as reproduced.
