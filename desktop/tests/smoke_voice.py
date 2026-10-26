#!/usr/bin/env python3
"""Exercise installed local models with synthetic audio; never open audio devices.

Run with the desktop voice venv. Requires its models, Voxtype, and ffmpeg.
Artifacts contain synthesized speech only and are written under --output-dir.
"""

import argparse
from array import array
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arvis_desktop.voice import FRAME_SAMPLES, RATE, VoiceService


def synthesize(text, voice_model, output):
    started = time.monotonic()
    worker = Path(__file__).resolve().parents[1] / "arvis_desktop/voice.py"
    completed = subprocess.run(
        [sys.executable, str(worker), "_synthesize", "--model-dir", str(voice_model),
         "--output", str(output), "--speed", "1.05"], input=text.encode(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.decode(errors="replace")[-4000:])
    return round(time.monotonic() - started, 3)


def resample(path, destination):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-ac", "1", "-ar", str(RATE),
                    "-c:a", "pcm_s16le", str(destination)], check=True, capture_output=True, timeout=30)
    with wave.open(str(destination)) as source:
        assert source.getnchannels() == 1 and source.getframerate() == RATE
        samples = array("h", source.readframes(source.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    return array("f", (x / 32768 for x in samples))


def detect(voice, samples):
    started = time.monotonic()
    stream = voice._spotter.create_stream()
    data = voice._numpy.asarray(samples, dtype=voice._numpy.float32)
    data = voice._numpy.concatenate((data, voice._numpy.zeros(RATE, dtype=voice._numpy.float32)))
    detections = []
    for start in range(0, len(data), FRAME_SAMPLES):
        stream.accept_waveform(RATE, data[start:start + FRAME_SAMPLES])
        while voice._spotter.is_ready(stream):
            voice._spotter.decode_stream(stream)
            word = voice._spotter.get_result(stream)
            if word:
                detections.append(word)
                voice._spotter.reset_stream(stream)
    return detections, round(time.monotonic() - started, 3)


async def main(args):
    commands = []
    events = []

    async def on_text(text):
        commands.append(text)

    async def on_event(event):
        events.append(event)

    base = args.model_dir.expanduser()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    voice = VoiceService({"model_dir": str(base), "speak_enabled": False}, on_text, on_event, lambda: False)
    # _load_wake imports/models only. Deliberately never call start()/speak().
    voice._load_wake()
    positive = out / "arvis-voice-test.wav"
    negative = out / "arvis-voice-negative.wav"
    positive_tts = synthesize("Arvis, what time is it?", base / "tts", positive)
    negative_tts = synthesize("The quick brown fox jumps over the lazy dog.", base / "tts", negative)
    positive_samples = resample(positive, out / "arvis-voice-test-16k.wav")
    negative_samples = resample(negative, out / "arvis-voice-negative-16k.wav")
    positive_words, positive_kws = detect(voice, positive_samples)
    negative_words, negative_kws = detect(voice, negative_samples)
    started = time.monotonic()
    await voice._transcribe(positive_samples.tobytes())
    report = {
        "microphone_opened": False, "audio_played": False,
        "positive": {"words": positive_words, "tts_seconds": positive_tts,
                     "kws_seconds": positive_kws, "audio_seconds": round(len(positive_samples) / RATE, 3)},
        "negative": {"words": negative_words, "tts_seconds": negative_tts,
                     "kws_seconds": negative_kws, "audio_seconds": round(len(negative_samples) / RATE, 3)},
        "transcription": commands, "transcription_seconds": round(time.monotonic() - started, 3),
        "errors": [e.get("message") for e in events if e["state"] == "unavailable"],
    }
    print(json.dumps(report, indent=2))
    expected_command = any(text.lower().strip(" .?!") == "what time is it" for text in commands)
    if "ARVIS" not in positive_words or negative_words or not expected_command or report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=Path.home() / ".local/share/arvis/voice")
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp"))
    asyncio.run(main(parser.parse_args()))
