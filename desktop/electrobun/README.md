# Parakeet Electrobun Desktop App

Small Windows desktop control surface for `parakeet bridge`, with the supported release topology of packaged Windows GUI + WSL bridge.

## What it does

- shows bridge connectivity and dictation state
- starts/stops recording from a small window
- registers a global hotkey
- displays the latest transcript returned by the WSL bridge
- adds a tray menu for open/toggle/quit

## Startup model

### Supported packaged Windows workflow

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

### Linux/WSL development workflow

These commands are still useful for local development inside Linux/WSL:

```bash
parakeet gui          # GUI only, expects an already-running bridge
parakeet gui --bridge # GUI + bridge together
parakeet full         # alias for the combined flow
```

On Ubuntu/WSL/Linux hosts running the Electrobun app directly, install the tray/runtime dependency once:

```bash
sudo apt install -y libayatana-appindicator3-1
```

Then launch the desktop app from this folder:

```bash
bun install
bun run start
```

`parakeet gui` and `parakeet full` will automatically run `bun install` once if `node_modules/` is missing.

## Environment overrides

- `PARAKEET_BRIDGE_URL` — default `http://127.0.0.1:8765`
- `PARAKEET_BRIDGE_COMMAND` — command shown in the UI as the WSL bridge startup command
- `PARAKEET_HOTKEY` — default `CommandOrControl+Alt+R`

Example:

```bash
PARAKEET_HOTKEY="CommandOrControl+Alt+R" bun run start
```

## Verify the app scaffold

```bash
bun install
bun run check
.venv/bin/python -m parakeet.cli gui-package --json
.venv/bin/python -m parakeet.cli gui-package-verify --json --timeout-seconds 240
```

`bun run check` performs bundle-time verification of:
- `src/mainview/index.ts` for the browser target
- `src/bun/index.ts` for the Bun target

`gui-package-verify` is the repo-supported unattended Windows packaging + E2E check.

## Notes

- supported release target: Windows 11 x64 GUI + WSL bridge
- WebView2 is required on Windows
- native Windows backend execution is not supported in this release
- ARM64-native packaging is not a supported target yet
- the bridge is localhost-only
- bridge startup stays user-controlled in the supported packaged workflow
- only one recording session is supported at a time
- the bridge subprocess reuses the existing packaged `parakeet dictation` flow in JSON mode
- if the bridge is offline, the desktop app stays usable and shows the command needed to start the backend
