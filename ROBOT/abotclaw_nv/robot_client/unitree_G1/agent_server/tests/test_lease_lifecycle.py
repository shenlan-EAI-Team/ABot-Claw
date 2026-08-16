"""Regression tests for Lease <-> /code/execute lifecycle coupling."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from code_executor import CodeExecutor, ExecutionStatus
from config import LeaseConfig
from lease import LeaseManager


class LeaseLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_waits_for_bound_execution(self) -> None:
        stopped: list[tuple[str, str]] = []
        mgr = LeaseManager(
            LeaseConfig(idle_timeout_s=10, max_duration_s=10, check_interval_s=0.01),
            last_moved_at_fn=lambda: 0.0,
        )
        mgr.set_execution_callbacks(
            is_active=lambda execution_id: True,
            stop=lambda execution_id, reason: stopped.append((execution_id, reason)) or True,
        )

        first = await mgr.acquire("holder-a")
        lease_id = first["lease_id"]
        self.assertTrue(await mgr.bind_execution(lease_id, "exec-a"))
        self.assertEqual((await mgr.acquire("holder-b"))["status"], "queued")

        self.assertEqual((await mgr.release(lease_id))["status"], "stopping")
        self.assertEqual(mgr.status()["state"], "ending")
        await asyncio.sleep(0.02)
        self.assertEqual(stopped, [("exec-a", "released")])
        self.assertEqual(mgr.current_lease.lease_id, lease_id)

        self.assertEqual(
            (await mgr.finish_execution(lease_id, "exec-a"))["status"], "released"
        )
        self.assertEqual(mgr.current_lease.holder, "holder-b")
        await mgr.stop()

    async def test_bound_execution_skips_idle_but_obeys_hard_max(self) -> None:
        stopped: list[tuple[str, str]] = []
        mgr = LeaseManager(
            LeaseConfig(
                idle_timeout_s=0.02,
                max_duration_s=0.09,
                check_interval_s=0.005,
                waiting_ticket_ttl_s=10,
            ),
            last_moved_at_fn=lambda: 0.0,
        )
        mgr.set_execution_callbacks(
            is_active=lambda execution_id: True,
            stop=lambda execution_id, reason: stopped.append((execution_id, reason)) or True,
        )
        first = await mgr.acquire("holder-a")
        lease_id = first["lease_id"]
        self.assertTrue(await mgr.bind_execution(lease_id, "exec-a"))
        await mgr.start()

        await asyncio.sleep(0.05)
        self.assertIsNone(mgr.current_lease.ending_reason)
        self.assertEqual(stopped, [])

        await asyncio.sleep(0.07)
        self.assertEqual(mgr.current_lease.ending_reason, "max_duration")
        self.assertEqual(stopped, [("exec-a", "max_duration")])
        await mgr.finish_execution(lease_id, "exec-a")
        await mgr.stop()

    async def test_starting_reservation_is_exclusive_and_stoppable(self) -> None:
        executor = CodeExecutor()
        executor._log_execution_output = lambda result: None
        self.assertTrue(executor.reserve("exec-a", "result = 1"))
        self.assertTrue(executor.is_busy)
        self.assertEqual(executor.status, ExecutionStatus.STARTING)
        self.assertFalse(executor.reserve("exec-b", "result = 2"))

        self.assertTrue(executor.stop_execution("exec-a", reason="max_duration"))
        result = await executor.execute("result = 1", "exec-a", timeout=1)
        self.assertEqual(result.status, ExecutionStatus.STOPPED)
        self.assertEqual(result.stop_reason, "max_duration")
        self.assertFalse(executor.is_busy)


if __name__ == "__main__":
    unittest.main()
