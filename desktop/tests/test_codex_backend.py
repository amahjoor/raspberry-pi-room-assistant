"""Exercise the real JSONL subprocess transport without a model or credentials."""

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

from arvis_desktop.codex_backend import CodexBackend, CodexError, approval_response


FAKE_SERVER = r'''
import json, sys
scenario, log_path = sys.argv[1:]
turn_id = ""
turn_number = 0
waiting = set()

def send(obj):
    print(json.dumps(obj), flush=True)

def respond(message, result):
    send({"id": message["id"], "result": result})

def complete(text="Done.", status="completed"):
    item = {"type":"agentMessage", "id":"answer", "phase":"final_answer", "text":text}
    send({"method":"item/agentMessage/delta", "params":{"threadId":"thread-1", "turnId":turn_id,"itemId":"answer","delta":text}})
    send({"method":"item/completed", "params":{"threadId":"thread-1", "turnId":turn_id,"item":item}})
    send({"method":"turn/completed", "params":{"threadId":"thread-1","turn":{"id":turn_id,"status":status,"items":[item], "error":{"message":"Model failed."} if status=="failed" else None}}})

for line in sys.stdin:
    message = json.loads(line)
    with open(log_path,"a") as log:
        log.write(json.dumps(message)+"\n")
    method = message.get("method")
    if method == "initialize":
        respond(message, {"userAgent":"fake-codex"})
    elif method == "initialized":
        pass
    elif method in ("thread/start", "thread/resume"):
        if scenario == "resume_error":
            send({"id":message["id"],"error":{"code":-32000,"message":"Unknown conversation."}})
        else:
            respond(message,{"thread":{"id":"thread-1"}})
    elif method == "turn/start":
        turn_number += 1
        turn_id = "turn-"+str(turn_number)
        turn = {"id":turn_id,"status":"inProgress","items":[]}
        send({"method":"turn/started", "params":{"threadId":"thread-1","turn":turn}})
        if scenario == "early_complete":
            complete("Finished before RPC response.")
        respond(message,{"turn":turn})
        if scenario in ("normal", "resume"):
            send({"method":"item/completed", "params":{"threadId":"thread-1", "turnId":turn_id,"item":{"id":"progress","type":"agentMessage","phase":"commentary","text":"Checking your request."}}})
            complete("Final answer "+str(turn_number)+".")
        elif scenario == "tool":
            send({"id":"server-tool","method":"item/tool/call","params":{"threadId":"thread-1","turnId":turn_id,"callId":"call-1","tool":"browser_inspect","arguments":{"url":"https://example.test"}}})
        elif scenario in ("approval", "approval_invalid", "approval_timeout"):
            waiting = {"server-approval","server-unknown"}
            send({"id":"server-approval","method":"item/commandExecution/requestApproval","params":{"threadId":"thread-1","turnId":turn_id,"itemId":"cmd","command":"echo harmless"}})
            send({"id":"server-unknown","method":"future/unknownMethod","params":{}})
        elif scenario == "tool_hang":
            send({"id":"server-tool","method":"item/tool/call","params":{"threadId":"thread-1","turnId":turn_id,"callId":"call-1","tool":"browser_inspect","arguments":{}}})
        elif scenario == "tool_unknown":
            send({"id":"server-tool","method":"item/tool/call","params":{"threadId":"thread-1","turnId":turn_id,"callId":"call-1","tool":"unregistered","arguments":{}}})
        elif scenario == "tool_stale":
            send({"id":"server-tool","method":"item/tool/call","params":{"threadId":"other-thread","turnId":turn_id,"callId":"call-1","tool":"browser_inspect","arguments":{}}})
        elif scenario == "failed":
            complete("", "failed")
        elif scenario == "crash":
            sys.exit(0)
        elif scenario == "bad_protocol":
            print("not-json", flush=True)
        elif scenario == "error_notification":
            send({"method":"error","params":{"threadId":"thread-1","turnId":turn_id,"willRetry":False,"error":{"message":"Temporary upstream failure."}}})
    elif method == "test/ping":
        respond(message, {"pong":True})
    elif method == "turn/interrupt":
        respond(message, {})
        complete("", "interrupted")
    elif message.get("id") == "server-tool":
        if scenario != "tool_hang":
            complete(json.dumps(message.get("result")))
    elif message.get("id") in waiting:
        waiting.remove(message["id"])
        if not waiting:
            complete("Approval flow finished.")
'''


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.script = self.directory / "fake_server.py"
        self.script.write_text(FAKE_SERVER)
        self.log = self.directory / "protocol.jsonl"
        self.backends = []
        self.events = []
        self.calls = []

    async def asyncTearDown(self):
        for backend in self.backends:
            await backend.close()
        self.temp.cleanup()

    def messages(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    async def event(self, value):
        self.events.append(value)

    async def tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"title":"Example", "url":arguments.get("url")}

    def backend(self, scenario="normal", **overrides):
        config = {"workspace":str(self.directory), "command":[sys.executable,"-u",str(self.script),scenario,str(self.log)],"rpc_timeout":2,"turn_timeout":2,"approval_timeout":1}
        config.update(overrides.pop("config", {}))
        backend = CodexBackend(config, [{"type":"function","name":"browser_inspect","description":"Inspect page","inputSchema":{"type":"object"}}], overrides.get("call_tool", self.tool), overrides.get("on_event", self.event), overrides.get("request_approval"))
        self.backends.append(backend)
        return backend

    async def wait_for_turn(self, backend):
        async def wait():
            while backend.turn_id is None:
                await asyncio.sleep(0.005)
        await asyncio.wait_for(wait(), 2)

    async def test_real_transport_keeps_conversation_and_selects_final_answer(self):
        backend = self.backend()
        self.assertEqual(await backend.ask("Hello"), "Final answer 1.")
        self.assertEqual(await backend.ask("Continue"), "Final answer 2.")
        await backend._events.join()
        self.assertEqual(backend.thread_id, "thread-1")
        requests = self.messages()
        self.assertEqual([r["method"] for r in requests if r.get("method") == "thread/start"], ["thread/start"])
        start = next(r["params"] for r in requests if r.get("method") == "thread/start")
        self.assertEqual(start["sandbox"], "workspace-write")
        self.assertEqual(start["approvalPolicy"], "on-request")
        self.assertEqual(start["approvalsReviewer"], "user")
        self.assertEqual(start["dynamicTools"][0]["type"], "function")
        self.assertTrue(any(e["type"] == "thread" for e in self.events))
        self.assertTrue(any(e["type"] == "delta" for e in self.events))

    async def test_dynamic_tool_can_make_rpc_without_deadlocking_reader(self):
        async def tool(name, arguments):
            pong = await backend._rpc("test/ping", {})
            self.assertEqual(pong, {"pong":True})
            return {"success":True,"contentItems":[{"type":"inputText","text":"Title inspected."},{"type":"inputImage","imageUrl":"data:image/png;base64,AA=="}]}
        backend = self.backend("tool", call_tool=tool)
        answer = json.loads(await backend.ask("Inspect example"))
        self.assertTrue(answer["success"])
        self.assertEqual(answer["contentItems"][1]["type"], "inputImage")

    async def test_approval_and_unknown_request_are_served_independently(self):
        async def approve(method, params):
            pong = await backend._rpc("test/ping", {})
            self.assertTrue(pong["pong"])
            return approval_response(method, params, True)
        backend = self.backend("approval", request_approval=approve)
        self.assertEqual(await backend.ask("Run it"), "Approval flow finished.")
        requests = self.messages()
        approval = next(r for r in requests if r.get("id") == "server-approval")
        unknown = next(r for r in requests if r.get("id") == "server-unknown")
        self.assertEqual(approval["result"], {"decision":"accept"})
        self.assertEqual(unknown["error"]["code"], -32601)

    async def test_missing_malformed_and_timed_out_approvals_decline(self):
        async def malformed(method, params):
            return True
        async def slow(method, params):
            await asyncio.sleep(10)
        for callback in [None, malformed, slow]:
            with self.subTest(callback=callback):
                self.log.unlink(missing_ok=True)
                backend = self.backend("approval", request_approval=callback, config={"approval_timeout":0.03})
                await backend.ask("Needs approval")
                approval = next(r for r in self.messages() if r.get("id") == "server-approval")
                self.assertEqual(approval["result"], {"decision":"decline"})
                await backend.close()

    async def test_unregistered_and_stale_tools_are_not_executed(self):
        for scenario in ["tool_unknown", "tool_stale"]:
            with self.subTest(scenario=scenario):
                backend = self.backend(scenario)
                answer = json.loads(await backend.ask("Inspect"))
                self.assertFalse(answer["success"])
                self.assertEqual(self.calls, [])
                await backend.close()

    async def test_interruption_cancels_active_tool_and_leaves_no_pending_turn(self):
        tool_started, tool_cancelled = asyncio.Event(), asyncio.Event()
        async def slow_tool(name, arguments):
            tool_started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                tool_cancelled.set()
                raise
        backend = self.backend("tool_hang", call_tool=slow_tool)
        task = asyncio.create_task(backend.ask("Long task"))
        await asyncio.wait_for(tool_started.wait(), 2)
        await backend.interrupt()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(tool_cancelled.is_set())
        self.assertIsNone(backend.turn_id)
        self.assertEqual(backend._pending, {})

    async def test_cancelling_caller_also_interrupts_server(self):
        backend = self.backend("hang")
        task = asyncio.create_task(backend.ask("Long task"))
        await self.wait_for_turn(backend)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(any(r.get("method") == "turn/interrupt" for r in self.messages()))

    async def test_complete_notification_can_precede_turn_start_response(self):
        backend = self.backend("early_complete")
        self.assertEqual(await backend.ask("Fast task"), "Finished before RPC response.")

    async def test_failed_turn_is_not_reported_as_success(self):
        backend = self.backend("failed")
        with self.assertRaisesRegex(CodexError, "Model failed"):
            await backend.ask("Fail")

    async def test_disconnection_protocol_failure_and_fatal_error_stop_child(self):
        for scenario in ["crash", "bad_protocol", "error_notification"]:
            with self.subTest(scenario=scenario):
                backend = self.backend(scenario)
                with self.assertRaises(CodexError):
                    await backend.ask("Fail")
                self.assertIsNotNone(backend._process.returncode)

    async def test_slow_ui_does_not_block_protocol(self):
        async def slow_event(event):
            await asyncio.sleep(60)
        backend = self.backend(on_event=slow_event)
        self.assertEqual(await backend.ask("Hello"), "Final answer 1.")

    async def test_resume_never_silently_discards_failed_conversation(self):
        backend = self.backend("resume", config={"thread_id":"thread-1"})
        await backend.ask("Remember?")
        requests = self.messages()
        self.assertTrue(any(r.get("method") == "thread/resume" for r in requests))
        self.assertFalse(any(r.get("method") == "thread/start" for r in requests))
        await backend.close()
        self.log.unlink()
        broken = self.backend("resume_error", config={"thread_id":"missing"})
        with self.assertRaisesRegex(CodexError, "Unknown conversation"):
            await broken.start()
        self.assertFalse(any(r.get("method") == "thread/start" for r in self.messages()))

    async def test_task_timeout_requests_interrupt(self):
        backend = self.backend("hang", config={"turn_timeout":0.05})
        with self.assertRaisesRegex(CodexError, "timed out"):
            await backend.ask("Wait forever")
        self.assertTrue(any(r.get("method") == "turn/interrupt" for r in self.messages()))


class ApprovalReplyTests(unittest.TestCase):
    def test_once_only_permissions_do_not_grant_session_access(self):
        params = {"permissions":{"network":{"enabled":True}, "fileSystem":{"write":["/tmp/work"]}}}
        reply = approval_response("item/permissions/requestApproval", params, True)
        self.assertEqual(reply, {"permissions":params["permissions"], "scope":"turn"})
        self.assertEqual(approval_response("item/permissions/requestApproval", params, False)["permissions"], {})

    def test_questions_require_an_actual_answer(self):
        params = {"questions":[{"id":"choice","question":"Choose?"}]}
        self.assertEqual(approval_response("item/tool/requestUserInput", params, True), {"answers":{}})
        self.assertEqual(approval_response("item/tool/requestUserInput", params, True, "First"), {"answers":{"choice":{"answers":["First"]}}})

    def test_mcp_forms_do_not_fabricate_required_content_or_device_proof(self):
        params = {"mode":"form","requestedSchema":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}}
        self.assertEqual(approval_response("mcpServer/elicitation/request", params, True)["action"], "decline")
        self.assertEqual(approval_response("mcpServer/elicitation/request", params, True, "Arman")["content"], {"name":"Arman"})
        self.assertEqual(approval_response("mcpServer/elicitation/request", {"mode":"openai/userVerification"}, True)["action"], "decline")


if __name__ == "__main__":
    unittest.main()
