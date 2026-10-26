"""Voice boundary tests do not access a microphone, network, or ML model."""

import asyncio
from array import array
import importlib.util
import io
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from arvis_desktop.voice import PCMFramer, UtteranceGate, VoiceService, clean_transcript, parse_voxtype_output, pcm16, rms


class AudioLogicTests(unittest.TestCase):
    def test_pipe_chunks_preserve_samples_and_partial_frame(self):
        source = array("f", [i / 100 for i in range(20)]).tobytes()
        framer = PCMFramer(16)
        result = []
        for start in range(0, len(source), 7):
            result.extend(framer.feed(source[start:start + 7]))
        self.assertEqual(b"".join(result), source)
        self.assertEqual(framer.pending, b"")
        self.assertEqual(framer.feed(b"abc"), [])
        self.assertEqual(framer.pending, b"abc")

    def test_silence_waits_for_real_speech(self):
        gate = UtteranceGate(speech_timeout_seconds=1)
        for _ in range(9):
            self.assertIsNone(gate.feed(0, .1))
        self.assertEqual(gate.feed(0, .2), "empty")

    def test_short_noise_does_not_create_command(self):
        gate = UtteranceGate(speech_timeout_seconds=1)
        self.assertIsNone(gate.feed(.1, .1))
        self.assertEqual(gate.feed(0, 1), "empty")

    def test_speech_ends_after_silence_or_maximum(self):
        gate = UtteranceGate(silence_seconds=.3)
        self.assertIsNone(gate.feed(.1, .4))
        self.assertIsNone(gate.feed(0, .2))
        self.assertEqual(gate.feed(0, .1), "complete")
        gate = UtteranceGate(max_seconds=1)
        self.assertEqual(gate.feed(.1, 1), "complete")

    def test_invalid_audio_does_not_propagate_nan(self):
        data = array("f", [-2, 0, 2, float("nan")]).tobytes()
        self.assertGreater(rms(data), 0)
        self.assertEqual(struct.unpack("<4h", pcm16(data)), (-32767, 0, 32767, 0))

    def test_transcript_removes_only_leading_activation(self):
        self.assertEqual(clean_transcript("Hey Arvis, open Kits."), "open Kits.")
        self.assertEqual(clean_transcript("Arvis."), "")
        self.assertEqual(clean_transcript("Tell me about Arvis."), "Tell me about Arvis.")
        self.assertEqual(clean_transcript("[BLANK_AUDIO]"), "")

    def test_voxtype_diagnostics_never_become_a_command(self):
        header = 'Loading audio file: "/tmp/command.wav"\nAudio format: 16000 Hz, 1 channel(s), Int\nProcessing 16000 samples (1.00s)...\n'
        self.assertEqual(parse_voxtype_output(header + "\nArvis, open Kits.\nThen check it.\n"),
                         "open Kits.\nThen check it.")
        self.assertEqual(parse_voxtype_output(header + "No speech detected, skipping transcription.\n"), "")
        self.assertEqual(parse_voxtype_output("Processing my request takes time."), "Processing my request takes time.")


class VoiceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def service(self, **config):
        return VoiceService(config, AsyncMock(), AsyncMock(), lambda: False)

    async def test_interrupt_cancels_child_and_discards_capture(self):
        voice = self.service()
        voice._begin_capture()
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def work():
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        voice._processing = asyncio.create_task(work())
        await started.wait()
        await voice.interrupt()
        self.assertTrue(stopped.is_set())
        self.assertIsNone(voice._capture)
        self.assertEqual(voice.state, "idle")

    async def test_muting_releases_microphone(self):
        voice = self.service()
        microphone = object()
        voice._microphone = microphone
        with patch.object(voice, "_kill", new_callable=AsyncMock) as kill:
            await voice.set_muted(True)
        kill.assert_awaited_once_with(microphone)
        self.assertTrue(voice.muted)
        self.assertEqual(voice.state, "muted")

    async def test_muted_activation_does_not_capture(self):
        voice = self.service(muted=True)
        voice.running = True
        await voice.activate()
        self.assertIsNone(voice._capture)
        self.assertEqual(voice.state, "muted")

    async def test_stopping_subprocess_reaps_it(self):
        voice = self.service()
        task = asyncio.create_task(voice._run(
            [sys.executable, "-c", "import time; time.sleep(60)"], timeout=70))
        for _ in range(50):
            if voice._children:
                break
            await asyncio.sleep(.01)
        self.assertEqual(len(voice._children), 1)
        proc = next(iter(voice._children))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(proc.returncode)
        self.assertFalse(voice._children)

    async def test_transcription_uses_isolated_local_config_and_no_typing(self):
        voice = self.service()
        inspected = []

        async def run(command, **kwargs):
            inspected.extend(command)
            config = Path(command[command.index("--config") + 1]).read_text()
            self.assertIn('mode = "file"', config)
            self.assertIn('backend = "local"', config)
            self.assertTrue(Path(command[-1]).is_file())
            return b"Arvis, open Kits."

        voice._run = run
        await voice._transcribe(array("f", [.1] * 1600).tobytes())
        self.assertIn("transcribe", inspected)
        self.assertNotIn("record", inspected)
        self.assertEqual(inspected[inspected.index("--whisper-mode") + 1], "local")
        voice.on_text.assert_awaited_once_with("open Kits.")
        self.assertFalse(Path(inspected[-1]).exists())


class SafeExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = Path(__file__).resolve().parents[1] / "scripts/setup_voice.py"
        spec = importlib.util.spec_from_file_location("setup_voice", script)
        cls.setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.setup)

    def archive(self, path, name, kind=tarfile.REGTYPE):
        with tarfile.open(path, "w:bz2") as archive:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.REGTYPE:
                info.size = 4
                archive.addfile(info, io.BytesIO(b"data"))
            else:
                info.linkname = "/tmp/outside"
                archive.addfile(info)

    def test_rejects_traversal_and_links(self):
        for name, kind in [("model/../../outside", tarfile.REGTYPE),
                           ("/outside", tarfile.REGTYPE),
                           ("other/file", tarfile.REGTYPE),
                           ("model/link", tarfile.SYMTYPE),
                           ("model/hardlink", tarfile.LNKTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                self.archive(path / "model.tar.bz2", name, kind)
                with self.assertRaises(ValueError):
                    self.setup.extract_model(path / "model.tar.bz2", path, "model")

    def test_extracts_regular_file_and_checks_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.archive(path / "model.tar.bz2", "model/weights.onnx")
            with self.assertRaises(ValueError):
                self.setup.extract_model(path / "model.tar.bz2", path / "small", "model", max_bytes=3)
            root = self.setup.extract_model(path / "model.tar.bz2", path / "valid", "model")
            self.assertEqual((root / "weights.onnx").read_bytes(), b"data")

    def test_checksum_supports_upstream_filename_first_order(self):
        filename = self.setup.KWS + ".tar.bz2"
        digest = self.setup.PINNED_SHA256[filename]
        for manifest in [f"{filename}\t{digest}\n", f"{digest}  {filename}\n"]:
            with patch.object(self.setup, "request", return_value=io.BytesIO(manifest.encode())):
                self.assertEqual(self.setup.published_hash("kws-models", filename), digest)
        with patch.object(self.setup, "request", return_value=io.BytesIO(f"{filename}\t{'0' * 64}\n".encode())):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.setup.published_hash("kws-models", filename)


if __name__ == "__main__":
    unittest.main()
