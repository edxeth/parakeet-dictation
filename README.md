# Parakeet Dictation (GPU/CPU)

GPU‑accelerated or CPU‑only microphone transcription using NVIDIA NeMo Parakeet TDT 0.6B v3, with a clean CLI and a debug mode for device/timing diagnostics.

## Features

- GPU/CPU switch via `--cpu`, with explicit device placement and verification using PyTorch device checks.
- `--debug` prints device, timings, and CUDA memory stats and truncates `transcriber.debug.log` on each run to keep logs fresh.
- `--list-devices` enumerates input devices and `--input-device` selects one for recording.
- Copies transcript to clipboard (disable with `--no-clipboard`).

## Requirements

- Python 3.10+
- PortAudio for microphone access (PyAudio wheels bind to it)
- For GPU: a CUDA-enabled PyTorch build and compatible NVIDIA drivers
- NVIDIA NeMo Toolkit (ASR) and its runtime dependencies

## OS Support

- Linux (Ubuntu/Debian) and WSL2 Ubuntu: supported; this is the primary/validated target.
- Windows (native): not supported currently. The CLI uses `select` on `stdin` and NeMo does not provide Windows wheels; use WSL2 Ubuntu instead.
- macOS: not supported currently. NeMo does not publish macOS wheels; consider running inside a Linux VM/container, or use an alternative engine (e.g., Faster‑Whisper) if native macOS support is required.

## Linux / WSL2 prerequisites

On Debian/Ubuntu (including WSL2), install system audio dependencies first:
```
sudo apt update
sudo apt install -y portaudio19-dev pulseaudio libasound2-plugins
```

Clipboard helpers (optional, for `pyperclip`):
```
sudo apt install -y xclip   # X11
# or
sudo apt install -y wl-clipboard  # Wayland
```

Notes for WSL2/WSLg: audio is bridged via the PulseAudio server at `unix:/mnt/wslg/PulseServer`, so starting a separate PulseAudio daemon is not needed and is typically refused. RDP sink/source devices may show as suspended until audio flows; that's expected under the WSLg bridge.

## Install (uv or pip)

Create and activate a virtual environment:
```
uv venv
source .venv/bin/activate
```

Install PyTorch first (choose one):
```
# GPU (CUDA 12.1)
uv pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cu121

# CPU-only
uv pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu
```

Then install the rest from PyPI:
```
uv pip install 'nemo_toolkit[asr]' pyaudio 'numpy<2.0' pyperclip
```

Why two steps? The PyTorch wheels are hosted on a separate index; installing them first avoids accidentally directing all packages to the PyTorch index (which would fail for NeMo and others).

## Optional ALSA → Pulse config (Linux)

Direct ALSA to PulseAudio by placing this in `~/.asoundrc` if needed:
```
pcm.!default {
    type pulse
}
ctl.!default {
    type pulse
}
```

## Usage

Make the script executable and run:
```
chmod +x transcriber.py
python transcriber.py
```

Helpful options:
```
# Verbose diagnostics to file (device, timings, GPU memory), spinner preserved
python transcriber.py --debug

# Force CPU even if CUDA is available
python transcriber.py --cpu

# List and select audio input devices
python transcriber.py --list-devices
python transcriber.py --input-device 2

# Disable clipboard copy (avoid needing xclip/wl-clipboard)
python transcriber.py --no-clipboard
```

Help-first CLI prints instructions and exits without loading the model:
```
python transcriber.py -h
```

## Verify GPU usage

In `--debug`, the script prints `Model device: cuda:N` when on GPU and shows CUDA memory growth during inference.
If `--cpu` is used or only CPU wheels are installed, the device prints as `cpu` and CUDA memory remains zero.

## Model cache

Parakeet is downloaded once and cached under the Hugging Face Hub cache (default `~/.cache/huggingface/hub`), and subsequent runs load it locally.
This location can be customized via `HF_HOME` / `HF_HUB_CACHE` if desired.

## Notes

- The script truncates `transcriber.debug.log` on each `--debug` run to keep logs fresh.
- The spinner writes directly to the original terminal stream to avoid interference from redirected stdout/stderr during model/model‑load.
- Argparse parsing occurs before heavy imports so `-h/--help` returns immediately.

## Troubleshooting

- PyAudio error about sample rate: some devices only support 48kHz. Try selecting a device via `--input-device` (see `--list-devices`) or switch your default input device/sample rate in system settings.
- Clipboard copy fails on Linux: install `xclip` (X11) or `wl-clipboard` (Wayland), or run with `--no-clipboard`.
- GPU memory stays at 0 in `--debug`: you are likely running the CPU PyTorch build or `--cpu` is set. Reinstall a CUDA build of PyTorch and omit `--cpu`.

## Windows/macOS status

- Windows native: not supported (NeMo wheels and the `select`-based key handling are Linux‑only). Use WSL2 Ubuntu and follow the Linux instructions.
- macOS: not supported (no official NeMo/macOS wheels). Use a Linux VM/container or an alternative engine.

## License

MIT for this glue code; refer to upstream projects (NeMo, PyAudio, PyTorch) for their licenses.
