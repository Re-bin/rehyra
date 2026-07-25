---
name: rehyra
description: Run, resume, inspect, or audit a fresh-workspace ReHyra recursive optimization campaign over a code workspace. Use when the user asks Codex to optimize an implementation against tests, benchmarks, scores, latency, cost, accuracy, or another executable objective; compare recursively generated candidates; continue a prior ReHyra run; or explain why candidates were promoted or rejected.
---

# ReHyra

Turn the user's goal into a frozen, evaluator-controlled search. Keep the
source workspace unchanged until the user explicitly asks to apply the winner.

## Locate the bundled runtime

Resolve the Codex Adapter directory two levels above the directory containing
this `SKILL.md`. Use its `rehyra_plugin.py` for every deterministic operation.
Do not assume the `rehyra` console script is globally installed.

Run the preflight first:

```text
python <adapter-dir>/rehyra_plugin.py doctor
```

## Start a campaign

1. Identify the workspace root and the requested outcome.
2. Find an executable evaluator before spending model calls. Prefer, in order:
   - a benchmark command named by the user;
   - an existing project benchmark or test command with a scalar metric;
   - a small deterministic evaluator added only to the experiment workspace;
   - ReHyra's generated-evaluator mode when no fixed oracle exists.
3. Define one primary objective, its direction, and hard qualification
   constraints. Never treat test pass/fail alone as an optimization score.
4. Run the evaluator on the untouched workspace at least twice. If its result
   is flaky, leaks future/private data, scores the source workspace instead of
   the candidate, or cannot distinguish obvious good and bad artifacts, stop
   and explain the blocker.
5. Select the candidate submission path. The Proposal Agent may edit the
   fresh proposal workspace, so qualification checks must protect behavior
   outside the primary metric. Plugin-created tasks freeze every other
   workspace file before evaluation. Use editable-region markers when the task
   contract requires an even narrower mutation surface inside the submission.
6. Choose a budget. Default to four proposals and concurrency two when the
   user did not specify either. Treat larger or costly campaigns as material
   work and state the budget before starting.
7. Create an experiment outside the source workspace:

```text
python <adapter-dir>/rehyra_plugin.py prepare \
  --workspace <workspace> \
  --output <absolute-experiment-directory> \
  --name <short-name> \
  --goal <goal> \
  --submission <relative-submission-path> \
  --objective-key <metric-key> \
  --direction <minimize-or-maximize> \
  --evaluator-program <program> \
  --evaluator-arg <arg-with-{workspace}-or-{submission}> \
  --max-proposals <budget> \
  --max-concurrency <concurrency>
```

Use `--evaluator-mode generated` instead of evaluator program/arguments only
when a fixed evaluator cannot be established. Label conclusions from a
generated evaluator as advisory unless independent calibration exists.

8. Inspect the returned JSON, then validate the task before running:

```text
python <adapter-dir>/rehyra_plugin.py cli describe-task <task.json>
python <adapter-dir>/rehyra_plugin.py cli run <run.toml> \
  --run-dir <run-directory>
```

Send short progress updates while the campaign runs. A failed proposal remains
evidence and still consumes its frozen slot.

## Resume or inspect

Re-run the same `cli run` command with the same run directory to recover an
interrupted campaign. Never delete or rewrite completed attempts.

Use:

```text
python <adapter-dir>/rehyra_plugin.py cli status <run-directory>
python <adapter-dir>/rehyra_plugin.py cli validate <run-directory>
python <adapter-dir>/rehyra_plugin.py best <run-directory>
```

For a larger continuation budget, create a new compatible experiment and use
ReHyra's `--seed-from` option. Preserve the earlier run as immutable evidence.

## Deliver the result

After completion:

1. Run `validate`.
2. Resolve the winner with `best`.
3. Compare the winning workspace against the original source workspace.
4. Report baseline, winner, absolute and relative change, qualification
   results, proposal count, failures, model usage, run directory, and the
   winning attempt. Include the sealed evidence root returned by `best`.
5. Show a reviewable patch. Apply it to the source workspace only when the
   user explicitly requests application.

Distinguish artifact discovery from reliable search performance. One winning
campaign does not establish low variance or general superiority.
