# Method

## Public Hyra claims reproduced

The implementation follows the control flow in Tencent's public Hyra launch
description:

1. A Context Agent maintains and reads an Experience Bank.
2. It creates diverse inspiration contexts.
3. Multiple Proposal Agents asynchronously consume those contexts.
4. Each Proposal Agent creates a complete solution in a fresh workspace.
5. The solution is run and scored.
6. Source, logs, scores, failures, and artifacts return to the bank.
7. The historical best is returned when the budget is exhausted.
8. The Context Agent may voluntarily stop when another proposal is not worth
   its cost.
9. For a task without an evaluator, an Evaluator Architect creates the initial
   evaluator. An independent read-only Critic must approve it.
10. After an inner loop, the Architect may upgrade the evaluator. Historical
    artifacts are replayed before the version can become active.

## Choices not specified by Tencent

The public material does not specify these details, so this baseline freezes
them explicitly:

- Context Agent: a fresh Codex invocation over a compact bank summary.
- Proposal Agent identity: a fresh ephemeral Codex invocation per proposal.
- Parent selection: selected by Context Agent, invalid IDs fall back to the
  current incumbent.
- Queue policy: producer may schedule at most `queue_target` outstanding
  proposals before waiting for new completed evidence.
- Experience transfer: one full parent workspace, compact metadata, and bounded
  ephemeral source snapshots from additional referenced attempts. Reference
  snapshots are removed before candidate execution. If the task exposes a
  validated runtime construction, the selected parent's best lineage state is
  exposed as a hashed temporary evidence copy for Proposal inspection, while
  the authoritative frozen state is injected only when the candidate executes.
- Feedback contents: evaluator results, bounded path-redacted execution logs,
  the full solution workspace within explicit file/byte caps, and the Proposal
  Agent's final diagnostic summary; task adapters may add semantic review
  flags.
- Feedback privacy: raw evaluator commands and paths remain in immutable
  evidence but are removed from Context and Proposal inputs.
- Selection: qualified candidates only; then minimize or maximize the task's
  declared primary objective.
- Failure policy: failures are retained but can never become incumbent.
- Stopping: hard proposal count, call timeout, and evaluator timeout.
- Agent runtime: provider-neutral ADK request/response protocol. Codex and
  generic command adapters are infrastructure, not task code.
- Evaluator promotion: frozen objective/direction, Critic approval, executable
  seed qualification, and historical replay. Versions and replays are immutable.
- Initial evaluator repair: a Critic blocker is returned to a fresh Architect
  revision, with every rejected version retained. Three rejected revisions
  fail closed rather than starting solution search with an untrusted oracle.
- Entrypoint: task bundles may declare a shell-free `solve.sh` argv; it runs
  before the external evaluator.
- Mutation policy: benchmark tasks retain full-workspace candidates by default.
  Generic plugin tasks use `submission_only`; the harness records the raw
  submission, restores every other file from the selected qualified parent,
  and only then evaluates.
- Evidence sealing: a completed run writes one deterministic manifest over its
  contracts, attempts, transcripts, logs, events, report, and harness snapshot.
  The root hash can be published and supplied back to offline validation.

## Module boundaries

```text
Task SDK -------------------- goal, workspace, submission, evaluator
Agent ADK ------------------- provider-neutral role requests
Harness roles --------------- Context / Proposal / Architect / Critic prompts
Scheduler ------------------- asynchronous producer-consumer loop
Experience Bank ------------- attempts, logs, artifacts, incumbent evidence
Evaluator evolution --------- versions, Critic, replay, activation
Execution backend ----------- process/container/remote sandbox + attestation
Benchmark adapters ---------- pinned external evaluator normalization
Codex Adapter --------------- natural-language workflow over the core interface
```

No benchmark imports Codex, Nanobot, or the harness. No provider imports a
benchmark. The Codex Adapter imports ReHyra; ReHyra never imports the Adapter.

For causal tasks, run directories are placed outside the repository checkout.
This avoids the agent's normal workspace inspection walking into task data or
public artifacts. The local backend remains advisory because only an external
container or remote sandbox can attest that the process could not read another
absolute path.

The diagnostic channel is necessary. A scalar score can say that a candidate
is smaller and accurate while omitting that it moved trained values into
uncounted constants. Later Context calls must receive both the metric and the
reason the producing agent believes the artifact is legitimate or risky.

## AdderBoard frozen inputs

- AdderBoard verifier:
  `07061de8d833d04e9800a009721731586e102aef`
- M10S trained seed:
  `a05e46ed449e064081ea9f40a3a132b798ac965d`
- Seed submission:
  `submissions/submission_36p.py`
- Search category:
  trained
- Primary objective:
  actual unique trainable parameters, minimized
- Qualification:
  at least 99% public and shadow accuracy

## Intended comparisons

The ReHyra baseline is one arm in a later equal-budget comparison:

```text
Direct: one persistent agent repeatedly edits the incumbent
ReHyra: Context Agent plus always-ephemeral proposals
OR: persistent actor with selective ephemeral exploration
```

All arms should use the same model, seed workspace, evaluator, model-call
budget, evaluator-query budget, wall-clock cap, and final hidden verification.
