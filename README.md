# Local AI Dictation

Local microphone dictation for Linux with:
- **Whisper** and **Parakeet** backends
- a localhost **bridge**
- a native **Electrobun GUI**
- **Waybar / Hyprland** integration

Default backend: **Whisper**.

## Main commands

```bash
local-ai-dictation dictation
local-ai-dictation bridge
local-ai-dictation bridge-toggle
local-ai-dictation gui
local-ai-dictation backend get
local-ai-dictation backend set whisper
local-ai-dictation backend set parakeet
local-ai-dictation backend toggle --restart-bridge
local-ai-dictation devices --json
local-ai-dictation doctor --json
local-ai-dictation benchmark --fixture tests/fixtures/short_16k.wav --runs 2 --json
```

## Backends

### Whisper
- default
- lighter on VRAM
- current model: `deepdml/faster-distil-whisper-large-v3.5`

### Parakeet
- heavier on VRAM
- current model: `nvidia/parakeet-tdt-0.6b-v3`

## Install

```bash
uv python install 3.12
uv venv --python 3.12
source .venv/bin/activate

# GPU PyTorch
uv pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cu130

# install app
uv pip install -e .
```

System packages commonly needed on Linux:
- `uv`
- `bun`
- `portaudio`
- `wl-clipboard` or `xclip`
- desktop deps for Electrobun/WebKitGTK

## Config

Config file:

```text
~/.config/local-ai-dictation/config.toml
```

Example:

```toml
backend = "whisper"
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

Supported env vars:
- `LOCAL_AI_DICTATION_BACKEND`
- `LOCAL_AI_DICTATION_CPU`
- `LOCAL_AI_DICTATION_INPUT_DEVICE`
- `LOCAL_AI_DICTATION_VAD`
- `LOCAL_AI_DICTATION_MAX_SILENCE_MS`
- `LOCAL_AI_DICTATION_MIN_SPEECH_MS`
- `LOCAL_AI_DICTATION_VAD_MODE`
- `LOCAL_AI_DICTATION_FORMAT`
- `LOCAL_AI_DICTATION_OUTPUT_FILE`
- `LOCAL_AI_DICTATION_CLIPBOARD`
- `LOCAL_AI_DICTATION_DEBUG`

## GUI

Run:

```bash
local-ai-dictation gui
```

The GUI shows:
- current running model
- bridge command
- transcript history
- model switch button

Switching models from the GUI restarts the bridge live.
The desktop GUI now subscribes to the bridge `/events` SSE stream for live state updates instead of polling bridge state on a timer.

## Hyprland / Waybar

Current local integration uses:
- `~/.local/bin/local-ai-dictation-bridge`
- `~/.config/hypr/scripts/local-ai-dictation-toggle.sh`
- `~/.config/hypr/scripts/local-ai-dictation-switch-model.sh`
- `~/.config/waybar/scripts/local-ai-dictation-status.py`

Behavior:
- **left click** Waybar icon: start / stop recording
- **right click** Waybar icon: switch model and restart bridge
- Waybar shows: `󰍬 Whisper` or `󰍬 Parakeet`
- Waybar no longer polls bridge state on an interval. The bridge pushes `RTMIN+8` to Waybar on state changes, and the module refreshes on startup and on that signal.
- The bridge still keeps `/health` for one-shot checks and recovery paths.

Hyprland startup currently launches the bridge with:

```ini
exec-once = /home/devkit/.local/bin/local-ai-dictation-bridge &
```

## Notes

- On Linux Wayland, the desktop app still does **not** own a true global app hotkey yet. `Ctrl+Alt+R` works when the Local AI Dictation window is focused, but a compositor-level shortcut is still needed for truly global activation until GlobalShortcuts portal support is added.
- Switching backend clears the old model from VRAM by restarting the bridge process.
- Whisper is the safer backend on 6 GB cards.
- Parakeet currently has a larger load-time VRAM spike before settling.

## Verification

```bash
python -m compileall src tests
.venv/bin/python -m pytest -q tests/test_bridge.py
cd desktop/electrobun && bun run check
```
