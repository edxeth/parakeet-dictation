# Parakeet Electrobun Desktop App

Small Windows desktop control surface for `parakeet bridge`.

## What it does

- shows bridge connectivity and dictation state
- starts/stops recording from a small window
- registers a global hotkey
- displays the latest transcript returned by the WSL bridge
- adds a tray menu for open/toggle/quit

## Startup model

This app does **not** auto-start Parakeet in WSL.

You explicitly run the backend bridge in WSL first:

```bash
source .venv/bin/activate
parakeet bridge --host 127.0.0.1 --port 8765
```

On Ubuntu/WSL/Linux hosts running the Electrobun app, install the tray/runtime dependency once:

```bash
sudo apt install -y libayatana-appindicator3-1
```

Then launch the desktop app from this folder:

```bash
bun install
bun run start
```

## Environment overrides

- `PARAKEET_BRIDGE_URL` — default `http://127.0.0.1:8765`
- `PARAKEET_BRIDGE_COMMAND` — command shown in the UI as the WSL bridge startup command
- `PARAKEET_HOTKEY` — default `CommandOrControl+Alt+Space`

Example:

```bash
PARAKEET_HOTKEY="CommandOrControl+Alt+R" bun run start
```

## Verify the app scaffold

```bash
bun install
bun run check
```

`bun run check` performs bundle-time verification of:
- `src/mainview/index.ts` for the browser target
- `src/bun/index.ts` for the Bun target

## Notes

- the bridge is localhost-only
- only one recording session is supported at a time
- the bridge subprocess reuses the existing packaged `parakeet dictation` flow in JSON mode
- if the bridge is offline, the desktop app stays usable and shows the command needed to start the backend
