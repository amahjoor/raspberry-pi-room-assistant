"""Coordinator tests use fake components and in-memory IPC, not live devices."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from arvis_desktop.daemon import Arvis


class FakeBackend:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.calls = []
        self.interrupts = 0
        self.closed = False

    async def ask(self, text, images=None):
        self.calls.append(text)
        if text == "first":
            self.started.set()
            try:
                await asyncio.Future()
            finally:
                self.cancelled.set()
        return f"Completed {text}"

    async def interrupt(self):
        self.interrupts += 1

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.cancellations = 0
        self.closed = False

    async def cancel(self):
        self.cancellations += 1

    async def close(self):
        self.closed = True


class FakeWriter:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class DaemonTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = Arvis({"state_dir": str(root / "state"), "workspace": str(root / "work"),
                          "project_roots": [str(root / "projects")], "notifications": False,
                          "voice": {"enabled": False}})
        self.backend = FakeBackend()
        self.browser = FakeBrowser()
        self.native = FakeBrowser()
        self.app.backend = self.backend
        self.app.browser = self.browser
        self.app.native = self.native
        self.app._select_panel_monitor = AsyncMock()

    async def asyncTearDown(self):
        await self.app.close()
        self.temp.cleanup()

    def start_worker(self):
        self.app._tasks = [asyncio.create_task(self.app._worker())]

    async def test_stop_cancels_active_job_discards_queued_jobs_and_can_run_again(self):
        self.start_worker()
        await self.app.submit("first")
        await asyncio.wait_for(self.backend.started.wait(), 1)
        await self.app.submit("queued job")
        await self.app.cancel()
        await asyncio.wait_for(self.app.queue.join(), 1)
        self.assertTrue(self.backend.cancelled.is_set())
        self.assertEqual(self.backend.calls, ["first"])
        self.assertEqual(self.browser.cancellations, 1)
        self.assertEqual(self.native.cancellations, 1)
        self.assertTrue(self.app._run_allowed.is_set())
        await self.app.submit("next job")
        await asyncio.wait_for(self.app.queue.join(), 1)
        self.assertEqual(self.backend.calls, ["first", "next job"])
        self.assertEqual(self.app.state["history"][-1]["text"], "Completed next job")

    async def test_component_stop_error_does_not_prevent_other_stops(self):
        class BrokenVoice:
            async def interrupt(self):
                raise RuntimeError("audio device disappeared")

            async def stop(self):
                pass

        self.app.voice = BrokenVoice()
        self.start_worker()
        await self.app.submit("first")
        await asyncio.wait_for(self.backend.started.wait(), 1)
        await self.app.cancel()
        await asyncio.wait_for(self.app.queue.join(), 1)
        self.assertTrue(self.backend.cancelled.is_set())
        self.assertEqual(self.browser.cancellations, 1)
        self.assertEqual(self.backend.interrupts, 1)
        self.assertTrue(self.app._run_allowed.is_set())

    async def test_new_job_waits_until_stop_finishes(self):
        stop_started, finish_stop = asyncio.Event(), asyncio.Event()

        async def slow_stop():
            stop_started.set()
            await finish_stop.wait()

        self.browser.cancel = slow_stop
        self.start_worker()
        stop = asyncio.create_task(self.app.cancel())
        await asyncio.wait_for(stop_started.wait(), 1)
        await self.app.submit("new request")
        await asyncio.sleep(0.02)
        self.assertEqual(self.backend.calls, [])
        finish_stop.set()
        await stop
        await asyncio.wait_for(self.app.queue.join(), 1)
        self.assertEqual(self.backend.calls, ["new request"])

    async def test_second_stop_discards_job_already_dequeued_at_gate(self):
        first_stop_started = asyncio.Event()
        finish_first_stop = asyncio.Event()
        resume_worker = asyncio.Event()
        stop_count = 0

        async def browser_stop():
            nonlocal stop_count
            stop_count += 1
            if stop_count == 1:
                first_stop_started.set()
                await finish_first_stop.wait()

        # Hold the worker after the first stop releases its gate, reproducing
        # scheduling where a second stop completes before that worker resumes.
        original_wait = self.app._run_allowed.wait

        async def delayed_gate_wait():
            await original_wait()
            await resume_worker.wait()

        self.browser.cancel = browser_stop
        self.app._run_allowed.wait = delayed_gate_wait
        self.start_worker()
        first_stop = asyncio.create_task(self.app.cancel())
        await asyncio.wait_for(first_stop_started.wait(), 1)
        await self.app.submit("submitted between stops")
        await asyncio.sleep(0)
        self.assertTrue(self.app.queue.empty())  # Worker holds the pending job.
        self.assertIsNone(self.app.current_task)
        second_stop = asyncio.create_task(self.app.cancel())
        await asyncio.sleep(0)
        finish_first_stop.set()
        await asyncio.gather(first_stop, second_stop)
        resume_worker.set()
        await asyncio.wait_for(self.app.queue.join(), 1)
        self.assertEqual(self.backend.calls, [])
        self.app._run_allowed.wait = original_wait
        await self.app.submit("after both stops")
        await asyncio.wait_for(self.app.queue.join(), 1)
        self.assertEqual(self.backend.calls, ["after both stops"])

    async def test_approval_only_resolves_after_explicit_user_response(self):
        task = asyncio.create_task(self.app.request_approval("item/commandExecution/requestApproval", {"command": "echo test"}))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertEqual(len(self.app.state["pending"]), 1)
        key = self.app.state["pending"][0]["id"]
        await self.app.dispatch("respond", {"id": key, "approved": True})
        self.assertEqual(await task, {"decision": "accept"})
        self.assertEqual(self.app.state["pending"], [])
        with self.assertRaisesRegex(ValueError, "already been resolved"):
            await self.app.dispatch("respond", {"id": key, "approved": True})

    async def test_stop_declines_pending_approval(self):
        task = asyncio.create_task(self.app.request_approval("item/fileChange/requestApproval", {}))
        await asyncio.sleep(0)
        await self.app.cancel()
        self.assertEqual(await task, {"decision": "decline"})
        self.assertEqual(self.app.approvals, {})

    async def test_cancel_during_approval_panel_selection_cleans_pending(self):
        selecting = asyncio.Event()

        async def select_monitor():
            selecting.set()
            await asyncio.Future()

        self.app._select_panel_monitor = select_monitor
        task = asyncio.create_task(self.app.request_approval("item/fileChange/requestApproval", {}))
        await asyncio.wait_for(selecting.wait(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.app.approvals, {})
        self.assertEqual(self.app.state["pending"], [])

    async def test_queue_limit_rejects_without_recording_an_extra_request(self):
        for index in range(12):
            await self.app.submit(f"Job {index}")
        with self.assertRaisesRegex(ValueError, "queue is full"):
            await self.app.submit("Rejected")
        self.assertEqual(self.app.queue.qsize(), 12)
        self.assertEqual(len(self.app.state["history"]), 12)

    async def test_ipc_reports_bad_requests_and_closes_connection(self):
        for request in [b"not json\n", b'{"method":"nonexistent"}\n', b"x" * 65537 + b"\n"]:
            with self.subTest(request=request[:30]):
                reader = asyncio.StreamReader(limit=100000)
                reader.feed_data(request)
                reader.feed_eof()
                writer = FakeWriter()
                await self.app.handle_client(reader, writer)
                response = json.loads(writer.data)
                self.assertFalse(response["ok"])
                self.assertTrue(writer.closed)
        self.assertTrue(self.app.queue.empty())

    async def test_ipc_submission_is_queued_once_without_executing_in_handler(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"method":"ask","params":{"text":"Inspect the app"}}\n')
        reader.feed_eof()
        writer = FakeWriter()
        await self.app.handle_client(reader, writer)
        response = json.loads(writer.data)
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["queued"], 1)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.app.state["history"][0]["text"], "Inspect the app")


if __name__ == "__main__":
    unittest.main()
