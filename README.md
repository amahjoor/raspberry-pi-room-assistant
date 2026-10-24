# Raspberry Pi room presence and wake word

Arvis watches a PIR motion sensor, a webcam, and a microphone on a Raspberry Pi, then drives Kasa smart plugs and LEDs. Voice commands go through a wake-word detector (Porcupine or openWakeWord) and an OpenAI key for speech-to-text and the reply. You can run it with mock hardware and no Pi.

`requirements.txt` asks for Python 3.9 through 3.12 because of MediaPipe. GPIO packages are commented out until you are on the Pi. Spotify variables in `env.example` are not used by `src/`.

## Run it, including without the hardware

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env
python -m src.main --mock-hardware
```

Put a real key in `OPENAI_API_KEY`. `--no-mock-hardware` uses the plugs, LEDs, and sensor. `--debug` turns on debug logs. The `arvis` console script in `setup.py` points at the same entry point (`pip install -e .`).

## License

[MIT](LICENSE)
