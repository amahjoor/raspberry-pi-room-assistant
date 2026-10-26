"""Browser contracts and real Chromium integration against a local fixture."""

import asyncio
import base64
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock

from arvis_desktop.browser import BrowserWorker, _download_name, _http_url


def payload(result):
    return json.loads(result["contentItems"][0]["text"])


class BrowserContractTests(unittest.IsolatedAsyncioTestCase):
    def test_specs_match_dynamic_tool_contract(self):
        specs = BrowserWorker.specs()
        self.assertEqual(len({x["name"] for x in specs}), len(specs))
        self.assertIn("browser_screenshot", {x["name"] for x in specs})
        for spec in specs:
            self.assertEqual(spec["type"], "function")
            self.assertFalse(spec["inputSchema"]["additionalProperties"])
            self.assertEqual(spec["inputSchema"]["type"], "object")
            self.assertIn("untrusted", spec["description"].lower())

    def test_browser_urls_are_explicit_web_urls(self):
        for url in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,hi", "chrome://settings",
                    "https://", "http://user:pass@example.test", "https://example.test\n/", "http://example.test:bad"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _http_url(url)
        self.assertEqual(_http_url("http://127.0.0.1:8080/project"), "http://127.0.0.1:8080/project")

    def test_personal_browser_profile_cannot_be_selected(self):
        with tempfile.TemporaryDirectory() as state:
            with self.assertRaises(ValueError):
                BrowserWorker({"state_dir": state, "browser_profile_dir": str(Path.home() / ".config/chromium")})

    def test_download_names_cannot_escape_directory(self):
        self.assertEqual(_download_name("../../evil.txt"), "evil.txt")
        self.assertEqual(_download_name("..\\..\\evil.txt"), "evil.txt")
        self.assertEqual(_download_name("...."), "download")
        self.assertNotIn("\n", _download_name("file\n.txt"))
        self.assertLessEqual(len(_download_name("音" * 200).encode("utf-8")), 160)

    async def test_invalid_tool_and_url_do_not_launch_browser(self):
        with tempfile.TemporaryDirectory() as state:
            worker = BrowserWorker({"state_dir": state})
            worker._start = AsyncMock()
            self.assertFalse((await worker.call("browser_open", {"url": "file:///etc/passwd"}))["success"])
            self.assertFalse((await worker.call("browser_open", {"url": "https://example.test", "script": "bad"}))["success"])
            self.assertFalse((await worker.call("execute_javascript", {}))["success"])
            worker._start.assert_not_called()
            await worker.close()

    async def test_cancel_interrupts_active_work_and_allows_explicit_restart(self):
        with tempfile.TemporaryDirectory() as state:
            worker = BrowserWorker({"state_dir": state})
            worker._start = AsyncMock()
            worker._stop_runtime = AsyncMock()
            started = asyncio.Event()

            async def dispatch(*_):
                started.set()
                await asyncio.Event().wait()

            worker._dispatch = dispatch
            active = asyncio.create_task(worker.call("browser_inspect", {}))
            await started.wait()
            queued = asyncio.create_task(worker.call("browser_inspect", {}))
            await asyncio.sleep(0)
            await worker.cancel()
            self.assertTrue(active.cancelled())
            self.assertFalse((await queued)["success"])
            worker._dispatch = AsyncMock(return_value={"success": True, "contentItems": []})
            self.assertTrue((await worker.call("browser_inspect", {}))["success"])
            await worker.close()


_HTML = b"""<!doctype html><html><head><title>Arvis browser fixture</title></head>
<body><h1>Independent browser</h1>
<form onsubmit="event.preventDefault();document.querySelector('#result').textContent='Saved '+document.querySelector('#display').value">
<label for="display">Display name</label><input id="display">
<label for="secret">Password</label><input id="secret" type="password" value="hidden-fixture-secret">
<button type="submit">Save profile</button></form><p id="result">Not saved</p>
<button onclick="console.error('fixture browser error')">Report error</button>
<a href="/popup" target="_blank">Open reference</a>
<a href="/report.txt" download="report.txt">Download report</a>
<button id="visual" style="position:absolute;left:450px;top:100px;width:140px;height:45px"
 onclick="document.querySelector('#result').textContent='Visual click worked'">Visual target</button>
<div style="height:1800px"></div><p>Bottom of fixture</p></body></html>"""


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/popup":
            content, kind = b"<html><title>Reference tab</title><body>Reference opened</body></html>", "text/html"
        elif self.path == "/report.txt":
            content, kind = b"Arvis local download fixture\n", "text/plain"
        elif self.path == "/blank":
            content, kind = b"<html><title>Second tab</title><body>Another independent tab</body></html>", "text/html"
        else:
            content, kind = _HTML, "text/html"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_):
        pass


@unittest.skipUnless(importlib.util.find_spec("playwright") is not None, "optional playwright dependency not installed")
class BrowserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2)

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.worker = BrowserWorker({"state_dir": self.temp.name, "browser_timeout_ms": 5000})

    async def asyncTearDown(self):
        await self.worker.close()
        self.temp.cleanup()

    async def open_fixture(self):
        result = await self.worker.call("browser_open", {"url": self.url})
        self.assertTrue(result["success"], result)
        return payload(result)

    async def test_semantic_form_completion_refs_and_password_redaction(self):
        initial = await self.open_fixture()
        self.assertEqual(initial["title"], "Arvis browser fixture")
        self.assertNotIn("hidden-fixture-secret", json.dumps(initial))
        display = next(x for x in initial["controls"] if x["name"] == "Display name")
        old_save = next(x for x in initial["controls"] if x["name"] == "Save profile")["ref"]
        filled = await self.worker.call("browser_fill", {"ref": display["ref"], "text": "Ada"})
        self.assertTrue(filled["success"], filled)
        # New inspection invalidates stale refs rather than acting on another element.
        stale = await self.worker.call("browser_click", {"ref": old_save})
        self.assertFalse(stale["success"])
        saved = await self.worker.call("browser_click", {"role": "button", "name": "Save profile"})
        self.assertTrue(saved["success"], saved)
        self.assertIn("Saved Ada", json.dumps(payload(saved)["text"]))
        self.assertTrue(self.worker.profile_dir.is_relative_to(Path(self.temp.name)))

    async def test_visual_screenshot_coordinate_click_and_stale_image(self):
        await self.open_fixture()
        shot = await self.worker.call("browser_screenshot", {})
        self.assertTrue(shot["success"], shot)
        metadata = payload(shot)
        self.assertEqual(metadata["width"], 1280)
        image_url = shot["contentItems"][1]["imageUrl"]
        image = base64.b64decode(image_url.split(",", 1)[1])
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(self.worker.preview_path.read_bytes(), image)
        clicked = await self.worker.call("browser_click", {"x": 500, "y": 120, "screenshot_id": metadata["screenshot_id"]})
        self.assertTrue(clicked["success"], clicked)
        self.assertIn("Visual click worked", json.dumps(payload(clicked)["text"]))
        stale = await self.worker.call("browser_click", {"x": 500, "y": 120, "screenshot_id": metadata["screenshot_id"]})
        self.assertFalse(stale["success"])

    async def test_tabs_scrolling_console_errors_and_downloads(self):
        initial = await self.open_fixture()
        first_id = initial["tab_id"]
        error = await self.worker.call("browser_click", {"role": "button", "name": "Report error"})
        self.assertTrue(error["success"], error)
        self.assertIn("fixture browser error", json.dumps(payload(error)["console_errors"]))
        other = await self.worker.call("browser_open", {"url": self.url + "/blank", "new_tab": True})
        self.assertTrue(other["success"], other)
        self.assertNotEqual(payload(other)["tab_id"], first_id)
        selected = await self.worker.call("browser_tabs", {"action": "select", "tab_id": first_id})
        self.assertTrue(selected["success"], selected)
        downloaded = await self.worker.call("browser_click", {"role": "link", "name": "Download report"})
        self.assertTrue(downloaded["success"], downloaded)
        for _ in range(30):
            if self.worker._downloads and self.worker._downloads[-1]["status"] == "complete":
                break
            await asyncio.sleep(0.05)
        self.assertEqual(self.worker._downloads[-1]["status"], "complete")
        path = Path(self.worker._downloads[-1]["path"])
        self.assertTrue(path.is_relative_to(self.worker.download_dir))
        self.assertEqual(path.read_bytes(), b"Arvis local download fixture\n")
        scrolled = await self.worker.call("browser_scroll", {"y": 900})
        self.assertTrue(scrolled["success"], scrolled)
        self.assertGreater(await self.worker._pages[first_id].evaluate("window.scrollY"), 100)


if __name__ == "__main__":
    unittest.main()
