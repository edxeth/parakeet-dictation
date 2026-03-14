# Parakeet Dictation

Packaged microphone dictation for Linux/WSL2 using NVIDIA NeMo Parakeet TDT 0.6B v3.

Milestone 1 ships a packaged `parakeet` CLI with:
- interactive `parakeet dictation`
- machine-readable `parakeet devices --json`
- deterministic `parakeet doctor --json`
- fixture-based `parakeet benchmark --json`
- optional WebRTC-VAD auto-stop
- a temporary `transcriber.py` compatibility wrapper

## Supported platforms

- Linux: supported
- WSL2 Ubuntu: supported
- Windows native: not supported
- macOS: not supported

The lifecycle remains user-controlled:
- no microphone access before explicit start
- no automatic background startup
- manual start/stop always remains available

## System prerequisites

On Debian/Ubuntu (including WSL2), install audio dependencies first:

```bash
sudo apt update
sudo apt install -y portaudio19-dev pulseaudio libasound2-plugins
```

Optional clipboard helpers:

```bash
sudo apt install -y xclip
# or
sudo apt install -y wl-clipboard
```

Notes for WSL2/WSLg:
- WSLg usually exposes PulseAudio at `unix:/mnt/wslg/PulseServer`
- you typically should not start a second PulseAudio daemon inside WSL

## Install from a fresh checkout

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Install PyTorch first:

```bash
# GPU (CUDA 12.1)
python -m pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cu121

# or CPU-only
python -m pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu
```

Then install this project in editable mode:

```bash
python -m pip install -e .
```

Optional test/build tools:

```bash
python -m pip install -e .[test] build
```

## CLI overview

With the virtualenv active, the packaged entry point is:

```bash
parakeet --help
```

Subcommands:
- `parakeet dictation`
- `parakeet devices`
- `parakeet doctor`
- `parakeet benchmark`
- `parakeet bridge`

## Dictation

Run `parakeet dictation` to start one interactive dictation session.

Verified command surfaces:

```bash
parakeet dictation --help
```

Common invocation patterns:

```bash
parakeet dictation --cpu
parakeet dictation --input-device 2
parakeet dictation --vad
parakeet dictation --vad --max-silence-ms 1200 --min-speech-ms 300 --vad-mode 2
parakeet dictation --format json --output-file transcript.json --no-clipboard
parakeet dictation --debug
```

Behavior notes:
- default flow is manual: press `Enter` to start, then `Enter` to stop
- with `--vad`, `Enter` still starts recording and manual stop is still available
- VAD auto-stop only becomes eligible after voiced audio is detected
- if no speech is detected, recording remains manual-stop only
- `--format json` prints exactly one JSON object to stdout for the transcript result
- `--output-file` mirrors the rendered transcript output to disk
- clipboard failures are warnings, not fatal errors

Legacy migration path still works:

```bash
python transcriber.py --help
```

Run `python transcriber.py` if you still need the legacy entry path. It is currently a thin compatibility wrapper that forwards into `parakeet dictation`.

## Devices

List input devices in machine-readable form:

```bash
parakeet devices --json
```

Contract notes:
- output includes `schema_version`
- `devices` is always an array
- zero-device cases return `devices: []`
- device ordering is stable by device id

## Doctor

Run fast readiness diagnostics without loading or downloading the model:

```bash
parakeet doctor --json
```

Opt in to local model cache/import checks:

```bash
parakeet doctor --check-model-cache --json
```

`doctor` reports:
- WSL detection
- Pulse/WSLg readiness
- audio-device enumeration
- clipboard availability
- CUDA readiness
- local model cache/import readiness when requested

Exit codes:
- `0`: ready
- `2`: recording blocked
- `3`: degraded but usable

## Desktop bridge and Windows app

For a small Windows control surface with a button, tray menu, and global hotkey, use the opt-in bridge plus the Electrobun app.

Start the backend bridge in WSL first:

```bash
parakeet bridge --host 127.0.0.1 --port 8765
```

If you run the Electrobun app on Linux/WSL, install its tray/runtime dependency once:

```bash
sudo apt install -y libayatana-appindicator3-1
```

Then launch the desktop app from `desktop/electrobun/`:

```bash
cd desktop/electrobun
bun install
bun run start
```

Desktop notes:
- the bridge is localhost-only and opt-in
- the desktop app does not auto-start WSL services for you
- only one recording session is supported at a time
- default hotkey is `CommandOrControl+Alt+Space`
- override bridge URL or hotkey with `PARAKEET_BRIDGE_URL`, `PARAKEET_BRIDGE_COMMAND`, and `PARAKEET_HOTKEY`

## Benchmark

Benchmark deterministic prerecorded WAV fixtures only:

```bash
parakeet benchmark --fixture tests/fixtures/short_16k.wav --runs 2 --json
```

Require an expected transcript sidecar and compute normalized exact match:

```bash
parakeet benchmark --fixture tests/fixtures/short_16k.wav --runs 2 --json --check-expected
```

Benchmark rules:
- uses local WAV fixtures only
- never accesses the microphone
- loads the engine once per invocation
- reports `load_ms`, `run_ms`, aggregate timing fields, transcript fields, and normalized match state
- expected sidecars use `tests/fixtures/name.expected.txt`

## Configuration

Config file location:

```text
~/.config/parakeet-dictation/config.toml
```

Precedence:
1. CLI flags
2. environment variables
3. config file
4. built-in defaults

Supported environment variables:
- `PARAKEET_CPU`
- `PARAKEET_INPUT_DEVICE`
- `PARAKEET_VAD`
- `PARAKEET_MAX_SILENCE_MS`
- `PARAKEET_MIN_SPEECH_MS`
- `PARAKEET_VAD_MODE`
- `PARAKEET_FORMAT`
- `PARAKEET_OUTPUT_FILE`
- `PARAKEET_CLIPBOARD`
- `PARAKEET_DEBUG`

Example config:

```toml
cpu = false
input_device = ""
vad = false
max_silence_ms = 1200
min_speech_ms = 300
vad_mode = 2
format = "text"
output_file = ""
clipboard = true
debug = false
```

## Verification from this repo

With the virtualenv active:

```bash
python -m compileall transcriber.py src tests
python -m pytest -q
python -m pip install -e .
python -m build
parakeet devices --json
parakeet doctor --json
parakeet doctor --check-model-cache --json
parakeet bridge --help
parakeet benchmark --fixture tests/fixtures/short_16k.wav --runs 2 --json --check-expected
cd desktop/electrobun && bun install && bun run check
```

## Troubleshooting

- No input devices: run `parakeet devices --json` and `parakeet doctor --json`
- Pulse/WSLg problems on WSL: check `PULSE_SERVER`, WSLg socket availability, and `pactl info`
- Clipboard copy unavailable: install `xclip` or `wl-clipboard`, or use `--no-clipboard`
- GPU not used: install a CUDA-enabled PyTorch build or run explicitly with `--cpu`
- Missing offline model cache: run `parakeet doctor --check-model-cache --json`

## License

MIT for this project glue code. Refer to upstream dependencies such as NeMo, PyTorch, and PyAudio for their licenses.
