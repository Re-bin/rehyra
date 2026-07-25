# Agent Provider Contract

The harness depends on an agent capability, not on an agent implementation.
Task authors never import Codex, Nanobot, a model SDK, or this protocol.

## User boundary

A benchmark author supplies only:

- a natural-language goal;
- a seed solution workspace;
- the relative submission path;
- an optional solution entrypoint;
- a fixed evaluator command, or `evaluator_mode = "generated"`;
- an objective key, direction, and qualification key;
- an optional opaque runtime-state interface.

An experiment author independently selects the Agent provider:

```toml
[agent]
backend = "command"
model = "provider-owned"
command_argv = ["my-agent-adapter", "--non-interactive"]
```

Changing this section must not require a task or scheduler change.

## Provider interface

In-process providers implement one method:

```python
class AgentProvider(Protocol):
    @property
    def descriptor(self) -> dict[str, Any]: ...

    async def run(self, request: AgentRequest) -> AgentCall: ...
```

`AgentRequest` contains:

- `role`: `context`, `proposal`, `evaluator_architect`, or
  `evaluator_critic`;
- a complete natural-language prompt;
- the workspace and evidence-output directory;
- a timeout;
- `read_only` or `workspace_write` access;
- an optional JSON output schema.

The harness owns role prompts, schemas, evidence recording, retries, scoring,
selection, and scheduling. A provider owns only how one request reaches an
agent runtime.

For Proposal calls, the workspace root contains the selected parent. Bounded
source snapshots from additional reference attempts are temporarily
materialized under `.hyra_context/references/`. A validated parent runtime
artifact, when one exists, is similarly exposed under
`.hyra_context/runtime_state/` for inspection and local experimentation. This
keeps the provider interface unchanged while allowing implementation-level
recombination and real artifact-level continuation. The harness records the
whole bundle manifest and removes the reserved directory before candidate
projection, execution, hashing, or promotion.

For plugin-created tasks, the declared submission is the only durable mutation.
After the Proposal call, the harness saves that file as raw evidence, rebuilds
the workspace from the selected qualified parent, and overlays only the saved
submission. Project tests, evaluator helpers, and unrelated source files are
therefore fixed inputs to promotion even though the Agent explored them in its
disposable workspace.

Context calls receive the same evidence semantics in a read-only workspace:
bounded solution-source snapshots plus up to eight distinct validated runtime
artifacts, capped at 64 MiB total. Runtime artifacts are deduplicated by their
owning attempt and checksum-verified before exposure.

## Universal command adapter

The built-in command provider executes `command_argv` directly without a
shell. It sends the prompt on standard input and sets:

| Variable | Meaning |
|---|---|
| `HYRA_AGENT_ROLE` | Current role |
| `HYRA_AGENT_ACCESS` | `read_only` or `workspace_write` |
| `HYRA_AGENT_WORKSPACE` | Workspace assigned to this call |
| `HYRA_AGENT_OUTPUT_PATH` | File for the final response |
| `HYRA_AGENT_SCHEMA_PATH` | JSON schema for structured roles, when present |
| `HYRA_AGENT_EXTERNAL_TOOLS` | `disabled` unless the harness explicitly opts in |

For structured roles the adapter writes one JSON object to
`HYRA_AGENT_OUTPUT_PATH`. For a Proposal Agent it writes a plain-text final
diagnostic; the candidate itself is the edited workspace.

This is sufficient to connect an ADK, a local CLI, an RPC client, GLM, or an
internal agent platform. Provider-specific transcripts and usage may be
recorded beside the generic call record, but they cannot change harness
semantics.

All Hyra roles default to external tools disabled. The Codex provider turns off
cached/live web search, browser, apps, plugins, MCP-style tools, and sandboxed
command networking. A least-privilege permission profile grants minimal
system/runtime reads and read or write access only to the assigned workspace;
other attempt directories, task data, and public result artifacts are
unreadable. It then scans the JSONL transcript and fails the call if a forbidden
event appears despite the configuration. A generic command adapter receives the
same policy through `HYRA_AGENT_EXTERNAL_TOOLS` and is responsible for enforcing
it in its own runtime boundary.

## Runtime construction state

Some optimization environments expose a validated construction in addition to
source code. A task may declare two environment-variable names:

```json
{
  "runtime_state": {
    "capture_env": "CAPTURE_STATE_PATH",
    "input_env": "INPUT_STATE_PATH",
    "description": "Interface visible to the Proposal Agent",
    "max_bytes": 4194304
  }
}
```

The evaluator writes the candidate construction to the capture path. The
harness hashes it and advances it along a lineage only when that candidate
beats the lineage's current state owner. A later candidate receives the
selected parent's state at evaluation time.

The authoritative state remains outside the Agent workspace. A hashed,
ephemeral copy is exposed during the Proposal call so the Agent can inspect and
locally test against the actual incumbent rather than guessing from a hash.
The evaluator still injects the frozen original, and the temporary copy is
removed before execution. This preserves the benchmark's runtime behavior
without coupling the core to SimpleTES or another task.

State descriptors cannot be supplied by evaluator JSON, cannot escape the run
directory, cannot be symlinks, and cannot exceed the declared size. Retries
delete stale capture files. Resume reuses the frozen input state and verifies
its hash before execution.

## Separation guarantees

```text
benchmark bundle  ──┐
                    ├── harness roles/scheduler ── AgentProvider
evaluator process ──┘                              ├── Codex
                                                  ├── Nanobot
                                                  └── command/ADK
```

- Benchmark adapters live under `benchmarks/tasks/`; the core contains no task names.
- Providers live in `rehyra/providers.py`; they contain no benchmark logic.
- The scheduler sees role-level calls, not provider SDK objects.
- The evaluator, not the Agent, is promotion authority.
- Every provider produces the same generic request, call, workspace, and hash
  evidence needed for offline audit.
- Candidate workspaces reject symbolic links and special filesystem entries
  before execution. This prevents a solution from turning a later Experience
  Bank copy or evaluator read into an out-of-workspace data access.

The model-free test suite exercises the command contract, schema rejection,
process-group cancellation, Nanobot substitution, runtime-state lineage,
failure retention, and crash recovery.
