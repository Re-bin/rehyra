from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .providers import (
    AgentAccess,
    AgentProvider,
    AgentRequest,
    AgentRole,
    make_provider,
    record_agent_call,
)
from .contracts import AgentCall, AgentConfig, Inspiration
from .prompts import (
    context_prompt,
    context_schema,
    evaluator_architect_prompt,
    evaluator_architect_schema,
    evaluator_critic_prompt,
    evaluator_critic_schema,
    proposal_prompt,
)


class AgentBackend(Protocol):
    """Role-level harness interface.

    Tests may supply this protocol directly. Production uses ``HarnessAgents``
    over an ADK provider.
    """

    async def context(
        self,
        *,
        goal: str,
        bank_summary: dict[str, Any],
        requested: int,
        remaining_proposals: int,
        work_dir: Path,
    ) -> AgentCall: ...

    async def proposal(
        self,
        *,
        goal: str,
        inspiration: Inspiration,
        bank_context: dict[str, Any],
        submission_relpath: Path,
        workspace: Path,
        output_dir: Path,
    ) -> AgentCall: ...


class HarnessAgents:
    """Hyra roles composed over a provider-neutral ADK."""

    def __init__(self, config: AgentConfig, provider: AgentProvider) -> None:
        self.config = config
        self.provider = provider

    async def _run(self, request: AgentRequest) -> AgentCall:
        call = await self.provider.run(request)
        record_agent_call(request.output_dir, call)
        return call

    async def context(
        self,
        *,
        goal: str,
        bank_summary: dict[str, Any],
        requested: int,
        remaining_proposals: int,
        work_dir: Path,
    ) -> AgentCall:
        return await self._run(
            AgentRequest(
                role=AgentRole.CONTEXT,
                prompt=context_prompt(
                    goal=goal,
                    bank_summary=bank_summary,
                    requested=requested,
                    remaining_proposals=remaining_proposals,
                ),
                workspace=work_dir,
                output_dir=work_dir,
                timeout_seconds=self.config.context_timeout_seconds,
                access=AgentAccess.READ_ONLY,
                output_schema=context_schema(requested),
            )
        )

    async def proposal(
        self,
        *,
        goal: str,
        inspiration: Inspiration,
        bank_context: dict[str, Any],
        submission_relpath: Path,
        workspace: Path,
        output_dir: Path,
    ) -> AgentCall:
        call = await self._run(
            AgentRequest(
                role=AgentRole.PROPOSAL,
                prompt=proposal_prompt(
                    goal=goal,
                    inspiration=inspiration,
                    bank_context=bank_context,
                    submission_relpath=submission_relpath,
                ),
                workspace=workspace,
                output_dir=output_dir,
                timeout_seconds=self.config.proposal_timeout_seconds,
                access=AgentAccess.WORKSPACE_WRITE,
            )
        )
        if call.output is None:
            return call
        output = {"diagnostic_summary": str(call.output.get("message", "")).strip()}
        return AgentCall(
            ok=call.ok,
            returncode=call.returncode,
            duration_seconds=call.duration_seconds,
            error=call.error,
            usage=call.usage,
            output=output,
        )

    async def evaluator_architect(
        self,
        *,
        goal: str,
        bank_summary: dict[str, Any],
        workspace: Path,
        output_dir: Path,
        evaluator_relpath: Path,
        initial: bool,
    ) -> AgentCall:
        return await self._run(
            AgentRequest(
                role=AgentRole.EVALUATOR_ARCHITECT,
                prompt=evaluator_architect_prompt(
                    goal=goal,
                    bank_summary=bank_summary,
                    evaluator_relpath=evaluator_relpath,
                    initial=initial,
                ),
                workspace=workspace,
                output_dir=output_dir,
                timeout_seconds=self.config.proposal_timeout_seconds,
                access=AgentAccess.WORKSPACE_WRITE,
                output_schema=evaluator_architect_schema(),
            )
        )

    async def evaluator_critic(
        self,
        *,
        goal: str,
        architect_output: dict[str, Any],
        bank_summary: dict[str, Any],
        workspace: Path,
        output_dir: Path,
        evaluator_relpath: Path,
    ) -> AgentCall:
        return await self._run(
            AgentRequest(
                role=AgentRole.EVALUATOR_CRITIC,
                prompt=evaluator_critic_prompt(
                    goal=goal,
                    architect_output=architect_output,
                    bank_summary=bank_summary,
                    evaluator_relpath=evaluator_relpath,
                ),
                workspace=workspace,
                output_dir=output_dir,
                timeout_seconds=self.config.context_timeout_seconds,
                access=AgentAccess.READ_ONLY,
                output_schema=evaluator_critic_schema(),
            )
        )


def make_backend(config: AgentConfig) -> HarnessAgents:
    return HarnessAgents(config, make_provider(config))
