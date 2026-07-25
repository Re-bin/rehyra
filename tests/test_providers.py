from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

from rehyra.providers import (
    AgentAccess,
    AgentRequest,
    AgentRole,
    CodexAgentProvider,
    CommandAgentProvider,
    NanobotAgentProvider,
    audit_agent_evidence,
    forbidden_external_events,
    load_recorded_structured_call,
    record_agent_call,
)
from rehyra.contracts import AgentCall, AgentConfig


SCRIPT = """\
import json
import os
import sys
from pathlib import Path

prompt = sys.stdin.read()
Path(os.environ["HYRA_AGENT_OUTPUT_PATH"]).write_text(
    json.dumps({"echo": prompt, "role": os.environ["HYRA_AGENT_ROLE"]})
)
"""


class CommandAgentProviderTests(unittest.TestCase):
    def test_codex_argv_disables_external_tools(self) -> None:
        root = Path("/tmp/hyra-adk-test")
        provider = CodexAgentProvider(
            AgentConfig(backend="codex", model="test-model")
        )
        argv = provider._argv(
            request=AgentRequest(
                role=AgentRole.PROPOSAL,
                prompt="work",
                workspace=root,
                output_dir=root / "output",
                timeout_seconds=10,
                access=AgentAccess.WORKSPACE_WRITE,
            ),
            last_message_path=root / "last.md",
            schema_path=None,
        )
        self.assertIn('web_search="disabled"', argv)
        self.assertIn('default_permissions="hyra_agent"', argv)
        self.assertTrue(
            any(
                item.startswith("permissions.hyra_agent.filesystem=")
                for item in argv
            )
        )
        self.assertIn("browser_use", argv)
        self.assertNotIn("--sandbox", argv)
        self.assertNotIn("--search", argv)

    def test_forbidden_external_events_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "events.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "web_search", "query": "answer"},
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "command_execution"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(forbidden_external_events(path), ("web_search",))

    def test_agent_evidence_audit_reports_external_tool_violation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            call_dir = run_dir / "context_calls" / "context-0001"
            call_dir.mkdir(parents=True)
            (call_dir / "request.json").write_text(
                json.dumps({"external_tools": "disabled"}),
                encoding="utf-8",
            )
            (call_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "web_search"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            audit = audit_agent_evidence(run_dir)
            self.assertFalse(audit["passed"])
            self.assertEqual(audit["request_count"], 1)
            self.assertEqual(audit["audited_transcript_count"], 1)
            self.assertIn("web_search", audit["violations"][0])

    def test_command_provider_rejects_schema_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            script = root / "agent.py"
            script.write_text(SCRIPT, encoding="utf-8")
            provider = CommandAgentProvider(
                AgentConfig(
                    backend="command",
                    model="fake",
                    command_argv=(sys.executable, str(script)),
                )
            )
            call = asyncio.run(
                provider.run(
                    AgentRequest(
                        role=AgentRole.CONTEXT,
                        prompt="hello",
                        workspace=root / "workspace",
                        output_dir=root / "output",
                        timeout_seconds=10,
                        access=AgentAccess.READ_ONLY,
                        output_schema={
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["stop"],
                            "properties": {"stop": {"type": "boolean"}},
                        },
                    )
                )
            )
            self.assertFalse(call.ok)
            self.assertIn("invalid agent output", call.error or "")

    def test_cancellation_kills_agent_process_group(self) -> None:
        async def scenario(root: Path) -> int:
            script = root / "sleeping_agent.py"
            script.write_text(
                "import os, time\n"
                "from pathlib import Path\n"
                "out = Path(os.environ['HYRA_AGENT_OUTPUT_PATH'])\n"
                "(out.parent / 'pid').write_text(str(os.getpid()))\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            provider = CommandAgentProvider(
                AgentConfig(
                    backend="command",
                    model="fake",
                    command_argv=(sys.executable, str(script)),
                )
            )
            task = asyncio.create_task(
                provider.run(
                    AgentRequest(
                        role=AgentRole.PROPOSAL,
                        prompt="wait",
                        workspace=root / "workspace",
                        output_dir=root / "output",
                        timeout_seconds=120,
                        access=AgentAccess.WORKSPACE_WRITE,
                    )
                )
            )
            pid_path = root / "output" / "pid"
            for _ in range(100):
                if pid_path.is_file():
                    break
                await asyncio.sleep(0.01)
            pid = int(pid_path.read_text(encoding="utf-8"))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return pid

        with tempfile.TemporaryDirectory() as raw:
            pid = asyncio.run(scenario(Path(raw)))
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_recorded_structured_call_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            expected = AgentCall(
                ok=True,
                returncode=0,
                duration_seconds=1.25,
                error=None,
                usage={"input_tokens": 7},
                output={"approve": False, "blockers": ["no headroom"]},
            )
            record_agent_call(root, expected)
            self.assertEqual(load_recorded_structured_call(root), expected)

    def test_shell_free_provider_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            script = root / "agent.py"
            script.write_text(SCRIPT, encoding="utf-8")
            provider = CommandAgentProvider(
                AgentConfig(
                    backend="command",
                    model="fake",
                    command_argv=(sys.executable, str(script)),
                )
            )
            call = asyncio.run(
                provider.run(
                    AgentRequest(
                        role=AgentRole.CONTEXT,
                        prompt="hello",
                        workspace=root / "workspace",
                        output_dir=root / "output",
                        timeout_seconds=10,
                        access=AgentAccess.READ_ONLY,
                        output_schema={
                            "type": "object",
                            "properties": {"echo": {"type": "string"}},
                        },
                    )
                )
            )
            self.assertTrue(call.ok)
            self.assertEqual(call.output, {"echo": "hello", "role": "context"})
            request = json.loads(
                (root / "output" / "request.json").read_text(encoding="utf-8")
            )
            self.assertEqual(request["role"], "context")
            self.assertEqual(request["prompt"], "hello")

    def test_nanobot_sdk_adapter_is_provider_neutral(self) -> None:
        class FakeResult:
            content = '{"stop": true}'
            usage = {"input_tokens": 4, "output_tokens": 2}

        class FakeNanobot:
            closed = False

            @classmethod
            def from_config(cls, **_kwargs):
                return cls()

            async def run(self, _prompt, **_kwargs):
                return FakeResult()

            async def aclose(self):
                self.closed = True

        module = types.ModuleType("nanobot")
        module.Nanobot = FakeNanobot
        previous = sys.modules.get("nanobot")
        sys.modules["nanobot"] = module
        try:
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                provider = NanobotAgentProvider(
                    AgentConfig(backend="nanobot", model="fake")
                )
                call = asyncio.run(
                    provider.run(
                        AgentRequest(
                            role=AgentRole.CONTEXT,
                            prompt="decide",
                            workspace=root / "workspace",
                            output_dir=root / "output",
                            timeout_seconds=10,
                            access=AgentAccess.READ_ONLY,
                            output_schema={"type": "object"},
                        )
                    )
                )
                self.assertTrue(call.ok)
                self.assertEqual(call.output, {"stop": True})
                self.assertEqual(call.usage["input_tokens"], 4)
        finally:
            if previous is None:
                sys.modules.pop("nanobot", None)
            else:
                sys.modules["nanobot"] = previous


if __name__ == "__main__":
    unittest.main()
