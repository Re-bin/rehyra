from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import Inspiration


def context_schema(requested: int) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["stop", "stop_reason", "inspirations"],
        "properties": {
            "stop": {"type": "boolean"},
            "stop_reason": {"type": "string"},
            "inspirations": {
                "type": "array",
                "minItems": 0,
                "maxItems": requested,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "title",
                        "hypothesis",
                        "instructions",
                        "risk",
                        "parent_attempt_id",
                        "reference_attempt_ids",
                    ],
                    "properties": {
                        "title": {"type": "string"},
                        "hypothesis": {"type": "string"},
                        "instructions": {"type": "string"},
                        "risk": {"type": "string"},
                        "parent_attempt_id": {"type": "string"},
                        "reference_attempt_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
        },
    }


def context_prompt(
    *,
    goal: str,
    bank_summary: dict[str, Any],
    requested: int,
    remaining_proposals: int,
) -> str:
    return (
        "You are the Context Agent in a public-spec Hyra research loop.\n"
        "You do not edit solutions. You are a portfolio manager that converts the "
        "entire Experience Bank into diverse, executable research inspirations.\n\n"
        f"FINAL GOAL:\n{goal}\n\n"
        f"REQUESTED INSPIRATIONS: {requested}\n"
        f"REMAINING PROPOSAL BUDGET: {remaining_proposals}\n\n"
        "EXPERIENCE BANK:\n"
        f"{json.dumps(bank_summary, indent=2, ensure_ascii=False)}\n\n"
        "Read-only source snapshots listed under source_snapshots are available "
        "inside the current workspace. Inspect them when code structure matters. "
        "Validated construction artifacts listed under runtime_state_snapshots are "
        "also available; use them to understand what the evaluated code actually "
        "produced rather than inferring a construction from its hash. "
        "The bank also includes bounded, path-redacted execution log excerpts. "
        "Choose only completed attempt IDs. Prefer a diverse portfolio: continue "
        "strong parents, repair informative failures, and test genuinely different "
        "hypotheses. Treat evaluator review flags and Proposal Agent diagnostics as "
        "evidence, not decoration. A proposal may reference several attempts but "
        "must name one parent whose workspace will be copied. Set stop=true only "
        "when further proposals are not worth their cost and explain why. Otherwise "
        "return the requested number of inspirations. Return only schema-compliant JSON."
    )


def proposal_prompt(
    *,
    goal: str,
    inspiration: Inspiration,
    bank_context: dict[str, Any],
    submission_relpath: Path,
) -> str:
    return (
        "You are one ephemeral Proposal Agent in a public-spec Hyra research loop. "
        "Work only inside the current workspace. Do not use the network and do not "
        "inspect files outside this workspace.\n\n"
        f"FINAL GOAL:\n{goal}\n\n"
        "INSPIRATION:\n"
        f"{json.dumps(inspiration.as_dict(), indent=2, ensure_ascii=False)}\n\n"
        "RELEVANT EXPERIENCE:\n"
        f"{json.dumps(bank_context, indent=2, ensure_ascii=False)}\n\n"
        "The selected parent's files are the workspace root. Files listed under "
        "source_snapshots are ephemeral copies of other selected attempts under "
        ".hyra_context/references/. Inspect them when recombining implementations. "
        "They are evidence, not part of the candidate, and the harness removes "
        ".hyra_context before projection and evaluation; never depend on it at "
        "candidate runtime.\n"
        "If RELEVANT EXPERIENCE declares an available runtime_state, its snapshot "
        "is an ephemeral inspectable copy of the validated parent-lineage artifact. "
        "Use it for analysis and affordable local tests. The evaluator injects the "
        "authoritative frozen original through the task-declared runtime interface. "
        "The harness removes the snapshot before evaluation, so retain a no-state "
        "fallback and never depend on its temporary path.\n"
        "If the task declares an editable region, only code between its markers is "
        "submitted: the harness restores the selected parent's prefix and suffix "
        "before execution. Keep exactly one ordered marker pair.\n"
        "The harness may also enforce a submission-only mutation policy. In that "
        "mode, every file except the declared submission is restored from the "
        "selected parent before evaluation, so put all intended changes in the "
        "submission file.\n"
        f"The final candidate must remain executable at {submission_relpath.as_posix()}.\n"
        "Inspect the parent solution, implement the hypothesis, and run affordable "
        "local checks. Never edit or replace the external evaluator. Do not falsify "
        "metadata or resource counts. Preserve a working parent when the experiment "
        "fails. Finish with a concise diagnostic summary; workspace files are the "
        "actual submission."
    )


def evaluator_architect_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "changed",
            "summary",
            "objective_key",
            "direction",
            "qualification_key",
            "evaluator_relpath",
        ],
        "properties": {
            "changed": {"type": "boolean"},
            "summary": {"type": "string"},
            "objective_key": {"type": "string"},
            "direction": {"enum": ["minimize", "maximize"]},
            "qualification_key": {"type": "string"},
            "evaluator_relpath": {"type": "string"},
        },
    }


def evaluator_architect_prompt(
    *,
    goal: str,
    bank_summary: dict[str, Any],
    evaluator_relpath: Path,
    initial: bool,
) -> str:
    phase = "create the initial evaluator" if initial else "audit and improve the evaluator"
    objective_key = str(bank_summary.get("objective_key", "score"))
    direction = str(bank_summary.get("direction", "maximize"))
    qualification_key = str(
        bank_summary.get("qualification_key", "qualified")
    )
    return (
        "You are the Evaluator Architect in the public Hyra bilevel loop. "
        f"Your job is to {phase}, not to modify candidate solutions.\n\n"
        f"FINAL GOAL:\n{goal}\n\n"
        "EXPERIENCE BANK:\n"
        f"{json.dumps(bank_summary, indent=2, ensure_ascii=False)}\n\n"
        "Candidate source snapshots listed by the bank are available under "
        "experience_sources/. Use them to test generality and hacking resistance. "
        "The output contract is frozen: emit a boolean under the exact key "
        f"{qualification_key!r}, a finite scalar under the exact key "
        f"{objective_key!r}, and preserve direction {direction!r}. "
        f"Write a self-contained Python evaluator at {evaluator_relpath.as_posix()}. "
        "It must accept the candidate submission path as argv[1], run deterministically, "
        "and print one JSON object on its final stdout line. That object must contain a "
        "boolean qualification field and one finite scalar primary objective. Improve "
        "correctness, useful feedback, efficiency, headroom, and reward-hacking "
        "resistance. Do not encode a preferred candidate or inspect future/private data. "
        "If the existing evaluator remains adequate, leave it unchanged and set "
        "changed=false. Return only schema-compliant metadata."
    )


def evaluator_critic_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["approve", "blockers", "risks", "recommended_tests"],
        "properties": {
            "approve": {"type": "boolean"},
            "blockers": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "recommended_tests": {"type": "array", "items": {"type": "string"}},
        },
    }


def evaluator_critic_prompt(
    *,
    goal: str,
    architect_output: dict[str, Any],
    bank_summary: dict[str, Any],
    evaluator_relpath: Path,
) -> str:
    return (
        "You are an independent read-only Evaluator Critic. You did not create this "
        "evaluator and cannot modify it. Fail closed on invalid metrics, candidate "
        "specific rules, leakage, nondeterminism, reward hacking, or artificial "
        "score saturation that hides unresolved objective headroom. If the declared "
        "goal is already provably solved, lack of further headroom is a reason for "
        "the search to stop, not a defect in an otherwise correct evaluator.\n\n"
        f"FINAL GOAL:\n{goal}\n\n"
        f"EVALUATOR FILE: {evaluator_relpath.as_posix()}\n\n"
        "ARCHITECT CLAIM:\n"
        f"{json.dumps(architect_output, indent=2, ensure_ascii=False)}\n\n"
        "EXPERIENCE BANK:\n"
        f"{json.dumps(bank_summary, indent=2, ensure_ascii=False)}\n\n"
        "Read any available experience_sources/ artifacts when checking whether "
        "the evaluator overfits or encodes a preferred candidate. "
        "Inspect the evaluator and return only the schema-compliant audit."
    )
