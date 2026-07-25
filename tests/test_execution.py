from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from rehyra.execution import ExecutionRequest, LocalProcessBackend


class ExecutionBackendTests(unittest.TestCase):
    def test_timeout_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result = asyncio.run(
                LocalProcessBackend().run(
                    ExecutionRequest(
                        argv=(
                            sys.executable,
                            "-c",
                            "import time; time.sleep(10)",
                        ),
                        cwd=Path(raw),
                        timeout_seconds=1,
                    )
                )
            )
            self.assertTrue(result.timed_out)
            self.assertIsNone(result.returncode)
            self.assertEqual(result.attestation.level, "advisory")
            self.assertLess(result.duration_seconds, 3)


if __name__ == "__main__":
    unittest.main()
