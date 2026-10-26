"""Private native desktop contracts and a local Qt fixture integration test."""

import base64
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from arvis_desktop.native import NativeWorker, _key


def payload(result):
    return json.loads(result["contentItems"][0]["text"])


def tool_config():
    tools = Path.home() / ".local/share/arvis/native-tools/usr"
    return {"native_bin_dir": os.environ.get("ARVIS_NATIVE_BIN_DIR", str(tools / "bin")),
            "native_lib_dir": os.environ.get("ARVIS_NATIVE_LIB_DIR", str(tools / "lib"))}


class NativeContractTests(unittest.IsolatedAsyncioTestCase):
    def test_configuration_requires_explicit_argv(self):
        for config in ({"native_apps": {"app": "sh -c echo unsafe"}},
                       {"native_apps": {"../app": ["test"]}},
                       {"native_apps": {"app": []}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                NativeWorker(config)
        self.assertEqual(NativeWorker({}).apps, {})

    def test_input_env_never_inherits_host_display_or_bus(self):
        with tempfile.TemporaryDirectory() as state, patch.dict(os.environ, {
                "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-1", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/host/bus"}):
            worker = NativeWorker({"state_dir": state, "native_bin_dir": "/private/bin", "native_lib_dir": "/private/lib"})
            with self.assertRaises(RuntimeError):
                worker._env()
            worker._display, worker._xvfb = ":123", SimpleNamespace(returncode=None)
            env = worker._env("fixture")
            self.assertEqual(env["DISPLAY"], ":123")
            self.assertEqual(env["XAUTHORITY"], str(worker._auth))
            self.assertNotIn("WAYLAND_DISPLAY", env)
            self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)
            self.assertEqual(env["QT_QPA_PLATFORM"], "xcb")
            self.assertTrue(env["PATH"].startswith("/private/bin:"))
            self.assertTrue(env["LD_LIBRARY_PATH"].startswith("/private/lib"))
            self.assertTrue(Path(env["XDG_CONFIG_HOME"]).is_relative_to(Path(state)))

    def test_shortcuts_cannot_be_xdotool_commands(self):
        for bad in ("exec", "mousemove", "key Return", "--window", "ctrl+a click 1", "$(touch file)", ""):
            with self.subTest(key=bad), self.assertRaises(ValueError):
                _key(bad)
        for good in ("ctrl+a", "alt+F4", "Return", "Escape", "Tab", "Control_L+s", "Page_Down"):
            self.assertEqual(_key(good), good)

    def test_specs_require_screenshot_for_every_input(self):
        specs = {x["name"]: x for x in NativeWorker.specs()}
        for name in ("native_click", "native_type", "native_key", "native_scroll", "native_focus"):
            self.assertIn("screenshot_id", specs[name]["inputSchema"]["required"])
            self.assertFalse(specs[name]["inputSchema"]["additionalProperties"])
        self.assertNotIn("command", specs["native_launch"]["inputSchema"]["properties"])

    async def test_unknown_app_does_not_start_display(self):
        worker = NativeWorker({})
        worker._ensure = AsyncMock()
        result = await worker.call("native_launch", {"app": "arbitrary-app"})
        self.assertFalse(result["success"])
        worker._ensure.assert_not_called()

    async def test_input_uses_literal_stdin_and_stale_screenshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as state:
            worker = NativeWorker({"state_dir": state})
            worker._display, worker._xvfb = ":123", SimpleNamespace(returncode=None)
            worker._apps["fixture"] = SimpleNamespace(returncode=None)
            worker._last_screenshot = "current"
            worker._xdotool = AsyncMock()
            worker._capture = AsyncMock(return_value={"success": True, "contentItems": []})
            rejected = await worker.call("native_type", {"text": "bad", "screenshot_id": "stale"})
            self.assertFalse(rejected["success"])
            worker._xdotool.assert_not_called()
            text = "--window 123 exec arbitrary $(shell)"
            typed = await worker.call("native_type", {"text": text, "screenshot_id": "current"})
            self.assertTrue(typed["success"], typed)
            worker._xdotool.assert_awaited_once_with("type", "--clearmodifiers", "--delay", "0", "--file", "-", input_data=text.encode())


_QML = """import QtQuick
import QtQuick.Window
Window {
    id: root
    width: 640; height: 360; visible: true
    title: "Arvis native fixture"
    color: "#16202e"
    Text { x: 30; y: 20; text: "Arvis private desktop fixture"; color: "white"; font.pixelSize: 20 }
    Rectangle { x: 30; y: 60; width: 400; height: 44; color: "white" }
    TextInput {
        id: field; x: 35; y: 66; width: 390; height: 34
        color: "black"; font.pixelSize: 22; focus: true
        onTextChanged: root.title = "Arvis input: " + text
    }
    Rectangle {
        x: 30; y: 130; width: 170; height: 52; color: "#569cd6"
        Text { anchors.centerIn: parent; text: "Save"; color: "white"; font.pixelSize: 22 }
        MouseArea { anchors.fill: parent; onClicked: root.title = "Arvis clicked: " + field.text }
    }
}
"""


_native = NativeWorker(tool_config())
_qml = shutil.which("qml") or ("/usr/lib/qt6/bin/qml" if Path("/usr/lib/qt6/bin/qml").is_file() else None)
_available = all(_native._dependency_paths().values()) and bool(_qml)


@unittest.skipUnless(_available, "optional native display tools or Qt QML fixture runtime are unavailable")
class NativeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        fixture = Path(self.temp.name) / "fixture.qml"
        fixture.write_text(_QML)
        self.worker = NativeWorker({"state_dir": self.temp.name, **tool_config(),
                                    "native_apps": {"fixture": [_qml, str(fixture)]}})

    async def asyncTearDown(self):
        await self.worker.close()
        self.temp.cleanup()

    async def checked(self, name, args):
        response = await self.worker.call(name, args)
        self.assertTrue(response["success"], response["contentItems"][0]["text"])
        return response, payload(response)

    async def test_private_native_launch_see_click_type_and_cleanup(self):
        response, shot = await self.checked("native_launch", {"app": "fixture"})
        self.assertTrue(self.worker._display.startswith(":"))
        self.assertNotEqual(self.worker._display, os.environ.get("DISPLAY"))
        self.assertIn("Arvis native fixture", json.dumps(shot["windows"]))
        image = base64.b64decode(response["contentItems"][1]["imageUrl"].split(",", 1)[1])
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(image, self.worker.preview_path.read_bytes())
        _, clicked = await self.checked("native_click", {"x": 80, "y": 80, "screenshot_id": shot["screenshot_id"]})
        _, typed = await self.checked("native_type", {"text": "Ada", "screenshot_id": clicked["screenshot_id"]})
        self.assertIn("Arvis input: Ada", json.dumps(typed["windows"]))
        _, saved = await self.checked("native_click", {"x": 100, "y": 155, "screenshot_id": typed["screenshot_id"]})
        self.assertIn("Arvis clicked: Ada", json.dumps(saved["windows"]))
        stale = await self.worker.call("native_click", {"x": 100, "y": 155, "screenshot_id": shot["screenshot_id"]})
        self.assertFalse(stale["success"])
        display, app = self.worker._xvfb, self.worker._apps["fixture"]
        await self.worker.cancel()
        self.assertIsNotNone(display.returncode)
        self.assertIsNotNone(app.returncode)
        self.assertIsNone(self.worker._display)
        self.assertFalse(self.worker._auth.exists())


if __name__ == "__main__":
    unittest.main()
