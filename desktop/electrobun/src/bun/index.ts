import { BrowserView, BrowserWindow, GlobalShortcut, Tray, type RPCSchema } from "electrobun/bun";

type SessionPayload = {
  schema_version: number;
  state: "stopped" | "idle" | "starting" | "recording" | "transcribing" | "error";
  started_at: number | null;
  last_completed_at: number | null;
  last_transcript: {
    schema_version: number;
    transcript: string;
    normalized_transcript?: string;
    device?: string;
    metadata?: Record<string, unknown>;
  } | null;
  last_error: string | null;
  model_loaded: boolean;
  model_loading: boolean;
  history: Array<{ id: string; completed_at: number; payload: Record<string, unknown> }>;
  config: Record<string, unknown>;
  stderr_tail: string[];
};

type BridgeViewState = {
  bridgeUrl: string;
  bridgeStartCommand: string;
  hotkey: string;
  connected: boolean;
  session: SessionPayload;
};

type DesktopRPC = {
  bun: RPCSchema<{
    requests: {
      getBridgeState: { params: {}; response: BridgeViewState };
      startRecording: { params: {}; response: BridgeViewState };
      stopRecording: { params: {}; response: BridgeViewState };
      toggleRecording: { params: {}; response: BridgeViewState };
      clearHistory: { params: {}; response: BridgeViewState };
      showWindow: { params: {}; response: { success: true } };
    };
    messages: {};
  }>;
  webview: RPCSchema<{
    requests: {};
    messages: {};
  }>;
};

const BRIDGE_URL = Bun.env.PARAKEET_BRIDGE_URL || "http://127.0.0.1:8765";
const HOTKEY = Bun.env.PARAKEET_HOTKEY || "CommandOrControl+Alt+R";
const BRIDGE_START_COMMAND = Bun.env.PARAKEET_BRIDGE_COMMAND || "parakeet bridge --host 127.0.0.1 --port 8765";
const emptySession = (): SessionPayload => ({
  schema_version: 1,
  state: "stopped",
  started_at: null,
  last_completed_at: null,
  last_transcript: null,
  last_error: null,
  model_loaded: false,
  model_loading: false,
  history: [],
  config: {},
  stderr_tail: [],
});

async function fetchBridgeJson(path: string, init?: RequestInit): Promise<any> {
  const response = await fetch(`${BRIDGE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });

  let payload: any = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    throw new Error(payload?.detail || payload?.error || `Bridge request failed: ${response.status}`);
  }

  return payload;
}

async function readBridgeState(): Promise<BridgeViewState> {
  try {
    const health = await fetchBridgeJson("/health");
    return {
      bridgeUrl: BRIDGE_URL,
      bridgeStartCommand: BRIDGE_START_COMMAND,
      hotkey: HOTKEY,
      connected: true,
      session: health.session as SessionPayload,
    };
  } catch (error) {
    return {
      bridgeUrl: BRIDGE_URL,
      bridgeStartCommand: BRIDGE_START_COMMAND,
      hotkey: HOTKEY,
      connected: false,
      session: {
        ...emptySession(),
        state: "error",
        last_error: error instanceof Error ? error.message : String(error),
      },
    };
  }
}

let mainWindow: BrowserWindow<any> | null = null;
let tray: Tray | null = null;

const rpc = BrowserView.defineRPC<DesktopRPC>({
  maxRequestTime: 30000,
  handlers: {
    requests: {
      getBridgeState: async () => {
        return await readBridgeState();
      },
      startRecording: async () => {
        await fetchBridgeJson("/session/start", { method: "POST", body: "{}" });
        return await readBridgeState();
      },
      stopRecording: async () => {
        await fetchBridgeJson("/session/stop", { method: "POST", body: "{}" });
        return await readBridgeState();
      },
      toggleRecording: async () => {
        await fetchBridgeJson("/session/toggle", { method: "POST", body: "{}" });
        return await readBridgeState();
      },
      clearHistory: async () => {
        await fetchBridgeJson("/session/clear-history", { method: "POST", body: "{}" });
        return await readBridgeState();
      },
      showWindow: async () => {
        ensureMainWindowSize();
        mainWindow?.show();
        mainWindow?.focus();
        return { success: true } as const;
      },
    },
    messages: {},
  },
});

const MIN_WINDOW_WIDTH = 500;
const MIN_WINDOW_HEIGHT = 1080;

async function toggleFromBackground() {
  try {
    await fetchBridgeJson("/session/toggle", { method: "POST", body: "{}" });
  } catch (error) {
    console.error(`Background toggle failed: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function ensureMainWindowSize() {
  if (!mainWindow) return;
  const { width, height } = mainWindow.getSize();
  if (width < MIN_WINDOW_WIDTH || height < MIN_WINDOW_HEIGHT) {
    mainWindow.setSize(Math.max(width, MIN_WINDOW_WIDTH), Math.max(height, MIN_WINDOW_HEIGHT));
  }
}

console.log("Creating BrowserWindow...");
mainWindow = new BrowserWindow({
  title: "Parakeet Dictation GUI",
  url: "views://mainview/index.html",
  frame: {
    width: MIN_WINDOW_WIDTH,
    height: MIN_WINDOW_HEIGHT,
    x: 220,
    y: 120,
  },
  rpc,
});
ensureMainWindowSize();
console.log("BrowserWindow created");

tray = new Tray({ title: "Parakeet" });
console.log("Tray created");

mainWindow.on("close", () => {
  GlobalShortcut.unregisterAll();
  tray?.remove();
  process.exit(0);
});
tray.setMenu([
  { type: "normal", label: "Open Parakeet", action: "open" },
  { type: "normal", label: `Toggle Recording (${HOTKEY})`, action: "toggle" },
  { type: "divider" },
  { type: "normal", label: "Quit", action: "quit" },
]);
tray.on("tray-clicked", async (event: any) => {
  const action = event.data?.action;
  switch (action) {
    case "open":
      ensureMainWindowSize();
      mainWindow?.show();
      mainWindow?.focus();
      break;
    case "toggle":
      await toggleFromBackground();
      break;
    case "quit":
      GlobalShortcut.unregisterAll();
      tray?.remove();
      process.exit(0);
  }
});

console.log("Registering global hotkey...");
const registeredHotkey = GlobalShortcut.register(HOTKEY, () => {
  void toggleFromBackground();
});
if (!registeredHotkey) {
  console.warn(`Failed to register global hotkey: ${HOTKEY}`);
}
console.log(`GlobalShortcut register result: ${registeredHotkey}`);

console.log("Parakeet desktop app started");
console.log(`Bridge URL: ${BRIDGE_URL}`);
console.log(`Hotkey: ${HOTKEY}`);
