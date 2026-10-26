"""Local wake-word detection, utterance capture, transcription, and speech.

Only the transcribed command is handed to the coordinator. Idle microphone
audio stays in a bounded in-memory buffer and is never sent to a service.
The optional native speech libraries are imported only when used.
"""

from __future__ import annotations

import argparse
import asyncio
from array import array
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from typing import Awaitable, Callable
import wave


RATE = 16000
FRAME_SAMPLES = 1600
FRAME_BYTES = FRAME_SAMPLES * 4
MODEL_SUFFIX = "epoch-12-avg-2-chunk-16-left-64.int8.onnx"
LOG = logging.getLogger(__name__)


class PCMFramer:
    """Turn arbitrary pipe reads into complete mono float32 audio frames."""

    def __init__(self, frame_bytes: int = FRAME_BYTES):
        if frame_bytes <= 0 or frame_bytes % 4:
            raise ValueError("Float32 frames must contain a positive multiple of four bytes")
        self.frame_bytes = frame_bytes
        self.pending = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self.pending.extend(data)
        frames = []
        while len(self.pending) >= self.frame_bytes:
            frames.append(bytes(self.pending[:self.frame_bytes]))
            del self.pending[:self.frame_bytes]
        return frames


def rms(frame: bytes) -> float:
    samples = array("f")
    samples.frombytes(frame)
    if not samples:
        return 0.0
    return math.sqrt(sum(x * x for x in samples if math.isfinite(x)) / len(samples))


def pcm16(frame: bytes) -> bytes:
    """Convert native float32 PipeWire audio to little-endian WAV samples."""
    floats = array("f")
    floats.frombytes(frame)
    integers = array("h", (
        round(max(-1.0, min(1.0, value)) * 32767) if math.isfinite(value) else 0
        for value in floats
    ))
    if sys.byteorder != "little":
        integers.byteswap()
    return integers.tobytes()


@dataclass
class UtteranceGate:
    """Energy endpointing measured in audio time, independent of pipe timing."""

    threshold: float = 0.012
    silence_seconds: float = 0.9
    max_seconds: float = 25.0
    speech_timeout_seconds: float = 7.0
    min_speech_seconds: float = 0.25
    elapsed: float = 0.0
    speech: float = 0.0
    silence: float = 0.0

    def feed(self, energy: float, seconds: float) -> str | None:
        self.elapsed += seconds
        if energy >= self.threshold:
            self.speech += seconds
            self.silence = 0.0
        else:
            self.silence += seconds
        if self.elapsed >= self.max_seconds:
            return "complete" if self.has_speech else "empty"
        if self.has_speech and self.silence + 1e-9 >= self.silence_seconds:
            return "complete"
        if not self.has_speech and self.elapsed >= self.speech_timeout_seconds:
            return "empty"
        return None

    @property
    def has_speech(self) -> bool:
        return self.speech + 1e-9 >= self.min_speech_seconds


def clean_transcript(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^(?:hey\s+)?arvis\b[\s,.:;!?-]*", "", text, flags=re.IGNORECASE)
    # Whisper sometimes labels non-speech instead of returning an empty string.
    if re.fullmatch(r"[\[(](?:blank_audio|silence|music|no speech)[\])]", text, re.I):
        return ""
    return text.strip()


def parse_voxtype_output(text: str) -> str:
    """Voxtype's file command prints progress with println!, even when quiet."""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    lines = text.splitlines()
    if any(line.startswith("Loading audio file:") for line in lines):
        if "No speech detected, skipping transcription." in lines:
            return ""
        prefixes = ("Loading audio file:", "Audio format:", "Processing ",
                    "Resampling from ", "VAD:")
        lines = [line for line in lines if not line.startswith(prefixes)]
    return clean_transcript("\n".join(lines))


class VoiceService:
    """One microphone owner; wake, command capture, and playback never overlap.

    ``config`` is the application's ``voice`` section. ``on_text`` receives a
    complete utterance. All callbacks execute on the asyncio event loop.
    ``set_muted`` and ``interrupt`` are asynchronous, just like start/stop.
    """

    def __init__(self, config: dict, on_text: Callable[[str], Awaitable[None]],
                 on_event: Callable[[dict], Awaitable[None]],
                 is_busy: Callable[[], bool]):
        self.config = config
        self.on_text = on_text
        self.on_event = on_event
        self.is_busy = is_busy
        self.enabled = bool(config.get("enabled", True))
        self.wake_enabled = bool(config.get("wake_enabled", True))
        self.muted = bool(config.get("muted", False))
        self.running = False
        self.state = "idle"
        default_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        self.model_dir = Path(config.get("model_dir", default_data / "arvis/voice")).expanduser()
        self._listener: asyncio.Task | None = None
        self._processing: asyncio.Task | None = None
        self._speaking: asyncio.Task | None = None
        self._microphone: asyncio.subprocess.Process | None = None
        self._children: set[asyncio.subprocess.Process] = set()
        self._changed = asyncio.Event()
        self._spotter = None
        self._wake_stream = None
        self._numpy = None
        self._capture: tuple[UtteranceGate, list[bytes]] | None = None
        self._pre_roll: deque[bytes] = deque(maxlen=max(0, math.ceil(self._number("pre_roll_seconds", .35, 0, 2) * 10)))
        self._cooldown_until = 0.0

    @property
    def wake_ready(self) -> bool:
        return self._spotter is not None

    def _number(self, name: str, default: float, minimum: float, maximum: float) -> float:
        value = float(self.config.get(name, default))
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"voice.{name} must be between {minimum} and {maximum}")
        return value

    async def _event(self, state: str, message: str | None = None) -> None:
        self.state = state
        event = {"type": "voice", "state": state, "muted": self.muted,
                 "wake_ready": self.wake_ready}
        if message:
            event["message"] = message
        try:
            await self.on_event(event)
        except Exception:
            LOG.exception("Voice status callback failed")

    async def start(self) -> None:
        if self.running:
            return
        if not self.enabled:
            await self._event("unavailable", "Voice is disabled in configuration.")
            return
        self.running = True
        self._listener = asyncio.create_task(self._listen(), name="arvis-microphone")

    async def stop(self) -> None:
        self.running = False
        self._changed.set()
        await self.interrupt()
        if self._listener:
            self._listener.cancel()
            with suppress(asyncio.CancelledError):
                await self._listener
            self._listener = None
        await self._kill(self._microphone)
        self._microphone = None

    async def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        if self.muted:
            await self.interrupt()
            # Release the microphone, rather than merely ignoring its samples.
            await self._kill(self._microphone)
        self._changed.set()
        await self._event("muted" if self.muted else "idle")

    async def activate(self) -> None:
        """Capture one command without needing a wake model (hotkey fallback)."""
        if not self.running:
            await self.start()
        if not self.running:
            return
        if self.muted:
            await self._event("muted", "Unmute the microphone before speaking.")
            return
        if self._capture is not None:
            # A second hotkey press ends capture, including long utterances.
            await self._finish_capture()
            return
        if self._occupied():
            await self._event(self.state, "Arvis is working. Interrupt it before starting a new voice request.")
            return
        self._begin_capture()
        self._changed.set()
        await self._event("listening")

    def _occupied(self) -> bool:
        return (self.is_busy()
                or self._processing is not None and not self._processing.done()
                or self._speaking is not None and not self._speaking.done())

    def _begin_capture(self) -> None:
        gate = UtteranceGate(
            threshold=self._number("energy_threshold", .012, .00001, 1),
            silence_seconds=self._number("silence_seconds", .9, .2, 5),
            max_seconds=self._number("max_seconds", 25, 1, 120),
            speech_timeout_seconds=self._number("speech_timeout_seconds", 7, 1, 30),
            min_speech_seconds=self._number("min_speech_seconds", .25, .1, 2),
        )
        self._capture = (gate, list(self._pre_roll))
        self._pre_roll.clear()

    def _load_wake(self) -> None:
        import numpy
        import sherpa_onnx
        base = self.model_dir / "kws"
        paths = {name: base / f"{name}-{MODEL_SUFFIX}" for name in ("encoder", "decoder", "joiner")}
        for path in [*paths.values(), base / "tokens.txt", base / "keywords.txt"]:
            if not path.is_file():
                raise FileNotFoundError(f"Missing wake-word model: {path}. Run setup_voice.py.")
        self._spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(base / "tokens.txt"), keywords_file=str(base / "keywords.txt"),
            **{name: str(path) for name, path in paths.items()},
            sample_rate=RATE, num_threads=1, provider="cpu", max_active_paths=4,
            keywords_score=self._number("wake_score", 1.0, .01, 10),
            keywords_threshold=self._number("wake_threshold", .25, .01, .99),
        )
        self._wake_stream = self._spotter.create_stream()
        self._numpy = numpy

    def _reset_wake(self) -> None:
        if self._spotter and self._wake_stream:
            self._spotter.reset_stream(self._wake_stream)

    async def _listen(self) -> None:
        if self.wake_enabled:
            try:
                await asyncio.to_thread(self._load_wake)
            except Exception as exc:
                await self._event("unavailable", f"Wake word unavailable: {exc}. Manual activation remains available.")
        if not shutil.which("pw-record"):
            self.running = False
            self._capture = None
            await self._event("unavailable", "PipeWire pw-record is not installed.")
            return
        if self.wake_ready or not self.wake_enabled:
            await self._event("muted" if self.muted else "listening" if self._capture else "idle")
        while self.running:
            # Clear before checking flags: activation cannot get lost between a
            # condition check and waiting for a change on this event loop.
            self._changed.clear()
            if self.muted or not (self.wake_ready or self._capture is not None):
                await self._changed.wait()
                continue
            command = ["pw-record", "--raw", "--rate", str(RATE), "--channels", "1", "--format", "f32"]
            if self.config.get("input_target"):
                command += ["--target", str(self.config["input_target"])]
            command.append("-")
            stderr_task = None
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                                                            stderr=asyncio.subprocess.PIPE)
                self._microphone = proc
                stderr_task = asyncio.create_task(self._read_tail(proc.stderr))
                framer = PCMFramer()
                while self.running and not self.muted:
                    data = await asyncio.wait_for(proc.stdout.read(FRAME_BYTES), timeout=5)
                    if not data:
                        await proc.wait()
                        error = await stderr_task
                        raise RuntimeError(f"Microphone capture exited ({proc.returncode}): {error or 'no audio received'}")
                    if self.state == "unavailable" and self.wake_ready and not self._occupied():
                        await self._event("listening" if self._capture else "idle")
                    for frame in framer.feed(data):
                        await self._consume(frame)
                    if not self.wake_ready and self._capture is None:
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._capture = None
                if self.running and not self.muted:
                    await self._event("unavailable", f"Microphone unavailable: {exc}")
                    # One retry per change/short backoff, never a hot EOF loop.
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._changed.wait(), timeout=3)
            finally:
                await self._kill(proc)
                if stderr_task:
                    if not stderr_task.done():
                        stderr_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await stderr_task
                if self._microphone is proc:
                    self._microphone = None
                self._pre_roll.clear()
                self._reset_wake()

    async def _consume(self, frame: bytes) -> None:
        if self.muted:
            return
        if self._capture:
            gate, frames = self._capture
            frames.append(frame)
            end = gate.feed(rms(frame), len(frame) / (RATE * 4))
            if end:
                await self._finish_capture()
            return
        if self._occupied() or time.monotonic() < self._cooldown_until:
            self._pre_roll.clear()
            self._reset_wake()
            return
        self._pre_roll.append(frame)
        if not self.wake_ready:
            return
        self._wake_stream.accept_waveform(RATE, self._numpy.frombuffer(frame, dtype=self._numpy.float32))
        while self._spotter.is_ready(self._wake_stream):
            self._spotter.decode_stream(self._wake_stream)
            if self._spotter.get_result(self._wake_stream):
                self._reset_wake()
                self._begin_capture()
                await self._event("listening")
                break

    async def _finish_capture(self) -> None:
        if not self._capture:
            return
        gate, frames = self._capture
        self._capture = None
        self._reset_wake()
        if not gate.has_speech:
            self._cooldown()
            await self._event("idle", "No command heard.")
            return
        await self._event("transcribing")
        self._processing = asyncio.create_task(self._transcribe(b"".join(frames)), name="arvis-transcription")

    async def _transcribe(self, audio: bytes) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="arvis-voice-") as temp:
                directory = Path(temp)
                wav_path = directory / "command.wav"
                with wave.open(str(wav_path), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(RATE)
                    wav.writeframes(pcm16(audio))
                # An isolated config prevents inherited remote transcription,
                # post-processing hooks, or dictation output side effects.
                config_path = directory / "voxtype.toml"
                config_path.write_text('engine = "whisper"\n[whisper]\nbackend = "local"\n'
                                       '[output]\nmode = "file"\nfile_path = '
                                       + json.dumps(str(directory / "unused-output.txt")) + '\n')
                command = ["voxtype", "--quiet", "--config", str(config_path), "--engine", "whisper",
                           "--whisper-mode", "local", "--model", str(self.config.get("transcription_model", "base.en")),
                           "--language", "en", "--threads", str(int(self._number("transcription_threads", 4, 1, 16))),
                           "--initial-prompt", "Arvis, Arman, Kits, Omarchy, Hyprland.",
                           "transcribe", str(wav_path)]
                result = await self._run(command, timeout=self._number("transcription_timeout_seconds", 90, 5, 300))
                text = parse_voxtype_output(result.decode("utf-8", errors="replace"))
            if text:
                await self._event("idle", "Command transcribed.")
                await self.on_text(text)
            else:
                await self._event("idle", "No command heard.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._event("unavailable", f"Local transcription failed: {exc}")
        finally:
            self._cooldown()
            self._changed.set()

    async def speak(self, text: str) -> None:
        if not self.enabled or not self.config.get("speak_enabled", True) or not text.strip():
            return
        if self._speaking and not self._speaking.done():
            self._speaking.cancel()
            with suppress(asyncio.CancelledError):
                await self._speaking
        self._speaking = asyncio.create_task(self._speak(text), name="arvis-speech")
        try:
            await self._speaking
        finally:
            self._cooldown()

    async def _speak(self, text: str) -> None:
        text = text[:int(self._number("tts_max_chars", 1800, 1, 10000))]
        self._capture = None
        self._pre_roll.clear()
        self._reset_wake()
        await self._event("speaking")
        try:
            custom = self.config.get("tts_command")
            if custom:
                if not isinstance(custom, list) or not all(isinstance(x, str) and x for x in custom):
                    raise ValueError("voice.tts_command must be an argv list; text is provided on stdin")
                await self._run(custom, data=text.encode(), timeout=180)
            else:
                tts = self.model_dir / "tts"
                if not (tts / "en_US-lessac-medium.onnx").is_file():
                    raise FileNotFoundError("Local voice model missing. Run setup_voice.py --with-tts.")
                with tempfile.TemporaryDirectory(prefix="arvis-speech-") as temp:
                    output = Path(temp) / "reply.wav"
                    await self._run([sys.executable, str(Path(__file__).resolve()), "_synthesize",
                                     "--model-dir", str(tts), "--output", str(output), "--speed",
                                     str(self._number("tts_speed", 1.05, .5, 2))], data=text.encode(), timeout=180)
                    await self._run(["pw-play", str(output)], timeout=180)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._event("unavailable", f"Speech output unavailable: {exc}")
            return
        finally:
            self._cooldown()
        await self._event("muted" if self.muted else "idle")

    async def interrupt(self) -> None:
        self._capture = None
        self._pre_roll.clear()
        for task in (self._processing, self._speaking):
            if task and not task.done() and task is not asyncio.current_task():
                task.cancel()
        for task in (self._processing, self._speaking):
            if task and task is not asyncio.current_task():
                with suppress(asyncio.CancelledError):
                    await task
        for proc in list(self._children):
            await self._kill(proc)
        self._cooldown()
        self._changed.set()
        await self._event("muted" if self.muted else "idle")

    def _cooldown(self) -> None:
        self._cooldown_until = time.monotonic() + self._number("cooldown_seconds", 1.5, 0, 10)
        self._pre_roll.clear()
        self._reset_wake()

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process | None) -> None:
        if proc is None or proc.returncode is not None:
            return
        with suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    @staticmethod
    async def _read_tail(stream: asyncio.StreamReader, limit: int = 8192) -> str:
        tail = bytearray()
        while data := await stream.read(4096):
            tail.extend(data)
            if len(tail) > limit:
                del tail[:-limit]
        return bytes(tail).decode("utf-8", errors="replace").strip()

    async def _run(self, command: list[str], *, data: bytes | None = None, timeout: float) -> bytes:
        proc = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self._children.add(proc)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(data), timeout=timeout)
            if proc.returncode != 0:
                raise RuntimeError(f"{Path(command[0]).name} exited ({proc.returncode}): "
                                   + stderr.decode("utf-8", errors="replace")[-2000:].strip())
            return stdout
        finally:
            await self._kill(proc)
            self._children.discard(proc)


def _synthesize(model_dir: Path, output: Path, speed: float, text: str) -> None:
    """Runs in a child so stopping speech also stops native model generation."""
    import sherpa_onnx
    from piper_phonemize import phonemize_espeak
    config = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
        vits=sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(model_dir / "en_US-lessac-medium.onnx"),
            tokens=str(model_dir / "tokens.txt")),
        num_threads=2, provider="cpu"))
    if not config.validate():
        raise ValueError("Invalid local TTS model files")
    tts = sherpa_onnx.OfflineTts(config)
    # sherpa >=2.0 removes its bundled text frontend. Use the separate upstream
    # phonemizer rather than data_dir (which v2 silently ignores).
    sentences = phonemize_espeak(text, tts.lang or "en-us")
    generation = sherpa_onnx.GenerationConfig()
    generation.sid = 0
    generation.speed = speed
    generation.phoneme_codepoints = [[ord(c) for c in sentence] for sentence in sentences]
    result = tts.generate("", generation)
    if result.sample_rate <= 0 or not len(result.samples):
        raise RuntimeError("Local speech model produced no audio")
    samples = array("f", result.samples)
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(result.sample_rate)
        wav.writeframes(pcm16(samples.tobytes()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arvis internal local speech worker")
    parser.add_argument("operation", choices=["_synthesize"])
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--speed", type=float, default=1.05)
    args = parser.parse_args()
    _synthesize(args.model_dir, args.output, args.speed, sys.stdin.read(10000))
