# Parakeet Electrobun Desktop App

Small desktop control surface for `parakeet bridge`, with native Linux support and an optional packaged Windows GUI + WSL bridge workflow.

## What it does

- shows bridge connectivity and dictation state
- starts/stops recording from a small window
- registers a global hotkey
- plays a short sound when recording starts and another when recording stops
- displays the latest transcript returned by the bridge
- adds a tray menu for open/toggle/quit

## Startup model

### Native Linux workflow

This workflow is distro-agnostic once Bun, WebKitGTK, Ayatana AppIndicator, GStreamer good plugins, and the Python dependencies are installed.

Run these commands from the repo root:

1. Start the bridge:

```bash
uv run parakeet bridge --host 127.0.0.1 --port 8765
```

2. Start the GUI in another terminal:

```bash
uv run parakeet gui --host 127.0.0.1 --port 8765 --bridge-command "uv run parakeet bridge --host 127.0.0.1 --port 8765"
```

For direct Bun iteration:

```bash
bun install
bun run start
```

### Optional packaged Windows workflow

Run these commands from the repo root in WSL:

1. Package the Windows app:

```bash
.venv/bin/python -m parakeet.cli gui-package --json
```

This stages `desktop/electrobun/` to `%LOCALAPPDATA%\ParakeetDictation\staging\...` first so Windows Bun builds from a drive-backed path instead of the `\\wsl.localhost\...` repo path.

2. Start the bridge in WSL:

```bash
.venv/bin/python -m parakeet.cli bridge --host 127.0.0.1 --port 8765
```

3. Run the generated Windows installer from the `gui-package` output and launch the installed app on Windows.

4. For the single unattended local verification entrypoint, run:

```bash
.venv/bin/python -m parakeet.cli gui-package-verify --json --timeout-seconds 240
```

`gui-package-verify` packages the app and validates packaged smoke, localhost automation, bridge recovery, main-window controls, tray actions, and global hotkey wiring against the real deterministic WSL bridge.

## Environment overrides

- `PARAKEET_BRIDGE_URL` — default `http://127.0.0.1:8765`
- `PARAKEET_BRIDGE_COMMAND` — command shown in the UI as the bridge startup command
- `PARAKEET_HOTKEY` — default `Control+Alt+R` on Linux, `CommandOrControl+Alt+R` on Windows
- native Linux global hotkeys are for X11 sessions; on Wayland, use your compositor to bind `parakeet bridge-toggle`

Example:

```bash
PARAKEET_HOTKEY="Control+Alt+R" bun run start
# Wayland compositor workaround example:
parakeet bridge-toggle --host 127.0.0.1 --port 8765
```

## Verify the app scaffold

```bash
bun install
bun run check
uv run parakeet gui --help
.venv/bin/python -m parakeet.cli gui-package --json
.venv/bin/python -m parakeet.cli gui-package-verify --json --timeout-seconds 240
```

`bun run check` performs bundle-time verification of:
- `src/mainview/index.ts` for the browser target
- `src/bun/index.ts` for the Bun target

`gui-package-verify` is the repo-supported unattended Windows packaging + E2E check.

## Notes

- supported native dev target: Linux desktop GUI + localhost bridge
- supported packaged release target: Windows 11 x64 GUI + WSL bridge
- WebView2 is required on Windows
- native Windows backend execution is not supported in this release
- ARM64-native packaging is not a supported target yet
- the bridge is localhost-only
- bridge startup stays user-controlled on Linux and Windows
- only one recording session is supported at a time
- the bridge subprocess reuses the existing packaged `parakeet dictation` flow in JSON mode
- if the bridge is offline, the desktop app stays usable and shows the command needed to start the backend
