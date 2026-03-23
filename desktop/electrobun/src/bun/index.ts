import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

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

type RendererReadyPayload = {
  userAgent: string;
};

type RendererAutomationSnapshot = {
  statusBadgeText: string;
  statusBadgeClassName: string;
  statusLine: string;
  toggleButtonText: string;
  toggleButtonDisabled: boolean;
  clearHistoryButtonDisabled: boolean;
  busyOverlayVisible: boolean;
  bridgeUrl: string;
  bridgeCommand: string;
  errorText: string;
  historyCount: number;
  historyTexts: string[];
};

type StartupDiagnostics = {
  bridgeUrl: string;
  hotkey: string;
  e2eEnabled: boolean;
  automationPort: number | null;
  automationReady: boolean;
  automationReadyAt: string | null;
  bunReady: boolean;
  bunReadyAt: string;
  trayCreated: boolean;
  trayCreatedAt: string | null;
  hotkeyRegistered: boolean;
  hotkeyRegisteredAt: string | null;
  rendererReady: boolean;
  rendererReadyAt: string | null;
  rendererRpcReady: boolean;
  rendererUserAgent: string | null;
  lastAutomationAction: string | null;
  shutdownReason: string | null;
  shutdownAt: string | null;
  lastError: string | null;
};

type AutomationActionId =
  | "show-window"
  | "start-recording"
  | "stop-recording"
  | "toggle-recording"
  | "clear-history"
  | "tray/open"
  | "tray/toggle"
  | "tray/quit"
  | "hotkey/trigger"
  | "quit";

type AutomationState = {
  enabled: boolean;
  port: number | null;
  tray: {
    created: boolean;
    actions: string[];
  };
  hotkey: {
    accelerator: string;
    registered: boolean;
  };
  renderer: {
    ready: boolean;
    rpcReady: boolean;
    userAgent: string | null;
    snapshot: RendererAutomationSnapshot | null;
  };
  bridge: BridgeViewState;
  diagnostics: StartupDiagnostics;
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
      reportRendererReady: { params: RendererReadyPayload; response: { success: true } };
    };
    messages: {};
  }>;
  webview: RPCSchema<{
    requests: {
      getAutomationSnapshot: { params: {}; response: RendererAutomationSnapshot };
    };
    messages: {};
  }>;
};

const BRIDGE_URL = Bun.env.PARAKEET_BRIDGE_URL || "http://127.0.0.1:8765";
const HOTKEY = Bun.env.PARAKEET_HOTKEY || "CommandOrControl+Alt+R";
const BRIDGE_START_COMMAND = Bun.env.PARAKEET_BRIDGE_COMMAND || "parakeet bridge --host 127.0.0.1 --port 8765";
const GUI_E2E_ENABLED = Bun.env.PARAKEET_GUI_E2E === "1";
const GUI_E2E_PORT = Number(Bun.env.PARAKEET_GUI_E2E_PORT || "0") || 0;
const DEFAULT_STARTUP_LOG_DIR = Bun.env.LOCALAPPDATA
  ? join(Bun.env.LOCALAPPDATA, "parakeet.desktop.local", "stable", "logs")
  : "";
const GUI_LOG_PATH = Bun.env.PARAKEET_GUI_LOG_PATH || (DEFAULT_STARTUP_LOG_DIR ? join(DEFAULT_STARTUP_LOG_DIR, "startup.log") : "");
const GUI_STARTUP_DIAGNOSTICS_PATH = Bun.env.PARAKEET_GUI_STARTUP_DIAGNOSTICS_PATH
  || (DEFAULT_STARTUP_LOG_DIR ? join(DEFAULT_STARTUP_LOG_DIR, "startup-diagnostics.json") : "");
const GUI_AUTO_EXIT_MS = Number(Bun.env.PARAKEET_GUI_AUTO_EXIT_MS || "0") || 0;
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

function timestamp(): string {
  return new Date().toISOString();
}

const startupDiagnostics: StartupDiagnostics = {
  bridgeUrl: BRIDGE_URL,
  hotkey: HOTKEY,
  e2eEnabled: GUI_E2E_ENABLED,
  automationPort: GUI_E2E_PORT > 0 ? GUI_E2E_PORT : null,
  automationReady: false,
  automationReadyAt: null,
  bunReady: true,
  bunReadyAt: timestamp(),
  trayCreated: false,
  trayCreatedAt: null,
  hotkeyRegistered: false,
  hotkeyRegisteredAt: null,
  rendererReady: false,
  rendererReadyAt: null,
  rendererRpcReady: false,
  rendererUserAgent: null,
  lastAutomationAction: null,
  shutdownReason: null,
  shutdownAt: null,
  lastError: null,
};

function ensureFileParent(path: string) {
  mkdirSync(dirname(path), { recursive: true });
}

function writeStartupDiagnostics() {
  if (!GUI_STARTUP_DIAGNOSTICS_PATH) {
    return;
  }
  ensureFileParent(GUI_STARTUP_DIAGNOSTICS_PATH);
  writeFileSync(GUI_STARTUP_DIAGNOSTICS_PATH, `${JSON.stringify(startupDiagnostics, null, 2)}\n`);
}

function updateStartupDiagnostics(patch: Partial<StartupDiagnostics>) {
  Object.assign(startupDiagnostics, patch);
  writeStartupDiagnostics();
}

function appendGuiLog(level: "INFO" | "WARN" | "ERROR", message: string) {
  const line = `[${timestamp()}] [${level}] ${message}`;
  if (level === "ERROR") {
    console.error(message);
  } else if (level === "WARN") {
    console.warn(message);
  } else {
    console.log(message);
  }
  if (GUI_LOG_PATH) {
    ensureFileParent(GUI_LOG_PATH);
    appendFileSync(GUI_LOG_PATH, `${line}\n`);
  }
}

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

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
    },
  });
}

function isAutomationAction(value: string): value is AutomationActionId {
  return [
    "show-window",
    "start-recording",
    "stop-recording",
    "toggle-recording",
    "clear-history",
    "tray/open",
    "tray/toggle",
    "tray/quit",
    "hotkey/trigger",
    "quit",
  ].includes(value);
}

async function readRendererAutomationSnapshot(): Promise<RendererAutomationSnapshot | null> {
  if (!startupDiagnostics.rendererRpcReady || !mainWindow?.webview.rpc) {
    return null;
  }
  try {
    return await mainWindow.webview.rpc.request.getAutomationSnapshot({});
  } catch (error) {
    appendGuiLog("WARN", `Failed to read renderer automation snapshot: ${error instanceof Error ? error.message : String(error)}`);
    return null;
  }
}

async function readAutomationState(): Promise<AutomationState> {
  return {
    enabled: GUI_E2E_ENABLED,
    port: GUI_E2E_PORT > 0 ? GUI_E2E_PORT : null,
    tray: {
      created: startupDiagnostics.trayCreated,
      actions: ["open", "toggle", "quit"],
    },
    hotkey: {
      accelerator: HOTKEY,
      registered: startupDiagnostics.hotkeyRegistered,
    },
    renderer: {
      ready: startupDiagnostics.rendererReady,
      rpcReady: startupDiagnostics.rendererRpcReady,
      userAgent: startupDiagnostics.rendererUserAgent,
      snapshot: await readRendererAutomationSnapshot(),
    },
    bridge: await readBridgeState(),
    diagnostics: { ...startupDiagnostics },
  };
}

let mainWindow: BrowserWindow<any> | null = null;
let tray: Tray | null = null;
let autoExitTimer: ReturnType<typeof setTimeout> | null = null;
let automationServer: ReturnType<typeof Bun.serve> | null = null;
let shuttingDown = false;

function exitApp(reason: string, exitCode = 0) {
  if (shuttingDown) {
    return;
  }
  shuttingDown = true;
  if (autoExitTimer) {
    clearTimeout(autoExitTimer);
    autoExitTimer = null;
  }
  updateStartupDiagnostics({
    shutdownReason: reason,
    shutdownAt: timestamp(),
  });
  appendGuiLog("INFO", `Shutting down app: ${reason}`);
  automationServer?.stop(true);
  GlobalShortcut.unregisterAll();
  tray?.remove();
  process.exit(exitCode);
}

function scheduleAutoExit() {
  if (!GUI_E2E_ENABLED || GUI_AUTO_EXIT_MS <= 0 || autoExitTimer) {
    return;
  }
  appendGuiLog("INFO", `Scheduling E2E auto-exit in ${GUI_AUTO_EXIT_MS}ms`);
  autoExitTimer = setTimeout(() => {
    exitApp("e2e-auto-exit");
  }, GUI_AUTO_EXIT_MS);
}

process.on("uncaughtException", (error) => {
  const message = error instanceof Error ? error.stack || error.message : String(error);
  updateStartupDiagnostics({ lastError: message });
  appendGuiLog("ERROR", `Uncaught exception: ${message}`);
  exitApp("uncaught-exception", 1);
});

process.on("unhandledRejection", (reason) => {
  const message = reason instanceof Error ? reason.stack || reason.message : String(reason);
  updateStartupDiagnostics({ lastError: message });
  appendGuiLog("ERROR", `Unhandled rejection: ${message}`);
  exitApp("unhandled-rejection", 1);
});

writeStartupDiagnostics();
appendGuiLog("INFO", "Creating BrowserWindow...");
mainWindow = new BrowserWindow({
  title: "Parakeet Dictation GUI",
  url: "views://mainview/index.html",
  frame: {
    width: 500,
    height: 1080,
    x: 220,
    y: 120,
  },
  rpc: BrowserView.defineRPC<DesktopRPC>({
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
          showMainWindow();
          return { success: true } as const;
        },
        reportRendererReady: async ({ userAgent }) => {
          updateStartupDiagnostics({
            rendererReady: true,
            rendererReadyAt: timestamp(),
            rendererRpcReady: true,
            rendererUserAgent: userAgent,
          });
          appendGuiLog("INFO", `Renderer ready via RPC: ${userAgent}`);
          scheduleAutoExit();
          return { success: true } as const;
        },
      },
      messages: {},
    },
  }),
});
appendGuiLog("INFO", "BrowserWindow created");

const MIN_WINDOW_WIDTH = 500;
const MIN_WINDOW_HEIGHT = 1080;

async function toggleFromBackground() {
  try {
    await fetchBridgeJson("/session/toggle", { method: "POST", body: "{}" });
  } catch (error) {
    appendGuiLog("ERROR", `Background toggle failed: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function ensureMainWindowSize() {
  if (!mainWindow) return;
  const { width, height } = mainWindow.getSize();
  if (width < MIN_WINDOW_WIDTH || height < MIN_WINDOW_HEIGHT) {
    mainWindow.setSize(Math.max(width, MIN_WINDOW_WIDTH), Math.max(height, MIN_WINDOW_HEIGHT));
  }
}

function showMainWindow() {
  ensureMainWindowSize();
  mainWindow?.show();
  mainWindow?.focus();
}

async function triggerHotkeyCallback() {
  appendGuiLog("INFO", `Hotkey callback invoked: ${HOTKEY}`);
  await toggleFromBackground();
}

function triggerQuit(action: string) {
  setTimeout(() => {
    exitApp(action);
  }, 0);
}

async function runAutomationAction(action: AutomationActionId): Promise<AutomationState> {
  updateStartupDiagnostics({ lastAutomationAction: action });
  appendGuiLog("INFO", `Automation action: ${action}`);

  switch (action) {
    case "show-window":
    case "tray/open":
      showMainWindow();
      break;
    case "start-recording":
      await fetchBridgeJson("/session/start", { method: "POST", body: "{}" });
      break;
    case "stop-recording":
      await fetchBridgeJson("/session/stop", { method: "POST", body: "{}" });
      break;
    case "toggle-recording":
      await fetchBridgeJson("/session/toggle", { method: "POST", body: "{}" });
      break;
    case "clear-history":
      await fetchBridgeJson("/session/clear-history", { method: "POST", body: "{}" });
      break;
    case "tray/toggle":
      await toggleFromBackground();
      break;
    case "hotkey/trigger":
      await triggerHotkeyCallback();
      break;
    case "tray/quit":
      triggerQuit("automation:tray/quit");
      break;
    case "quit":
      triggerQuit("automation:quit");
      break;
  }

  return await readAutomationState();
}

function startAutomationServer() {
  if (!GUI_E2E_ENABLED || GUI_E2E_PORT <= 0 || automationServer) {
    return;
  }

  appendGuiLog("INFO", `Starting automation server on http://127.0.0.1:${GUI_E2E_PORT}`);
  try {
    automationServer = Bun.serve({
      hostname: "127.0.0.1",
      port: GUI_E2E_PORT,
      fetch: async (request) => {
        const url = new URL(request.url);
        try {
          if (request.method === "GET" && url.pathname === "/health") {
            return jsonResponse({ ok: true });
          }
          if (request.method === "GET" && url.pathname === "/state") {
            return jsonResponse({ ok: true, state: await readAutomationState() });
          }
          if (request.method === "POST" && url.pathname.startsWith("/actions/")) {
            const action = decodeURIComponent(url.pathname.slice("/actions/".length));
            if (!isAutomationAction(action)) {
              return jsonResponse({ ok: false, error: `Unknown automation action: ${action}` }, 404);
            }
            return jsonResponse({ ok: true, state: await runAutomationAction(action) });
          }
          return jsonResponse({ ok: false, error: `Unknown automation route: ${url.pathname}` }, 404);
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          updateStartupDiagnostics({ lastError: message });
          appendGuiLog("ERROR", `Automation request failed: ${message}`);
          return jsonResponse({ ok: false, error: message }, 500);
        }
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    updateStartupDiagnostics({ lastError: message });
    appendGuiLog("ERROR", `Failed to start automation server: ${message}`);
    return;
  }

  updateStartupDiagnostics({
    automationPort: GUI_E2E_PORT,
    automationReady: true,
    automationReadyAt: timestamp(),
  });
  appendGuiLog("INFO", `Automation server listening on http://127.0.0.1:${GUI_E2E_PORT}`);
}

ensureMainWindowSize();

tray = new Tray({ title: "Parakeet" });
updateStartupDiagnostics({ trayCreated: true, trayCreatedAt: timestamp() });
appendGuiLog("INFO", "Tray created");

mainWindow.on("close", () => {
  exitApp("window-close");
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
      showMainWindow();
      break;
    case "toggle":
      await toggleFromBackground();
      break;
    case "quit":
      exitApp("tray-quit");
      break;
  }
});

appendGuiLog("INFO", "Registering global hotkey...");
const registeredHotkey = GlobalShortcut.register(HOTKEY, () => {
  void triggerHotkeyCallback();
});
updateStartupDiagnostics({
  hotkeyRegistered: registeredHotkey,
  hotkeyRegisteredAt: timestamp(),
});
if (!registeredHotkey) {
  appendGuiLog("WARN", `Failed to register global hotkey: ${HOTKEY}`);
} else {
  appendGuiLog("INFO", `Global hotkey registered: ${HOTKEY}`);
}

startAutomationServer();

appendGuiLog("INFO", "Parakeet desktop app started");
appendGuiLog("INFO", `Bridge URL: ${BRIDGE_URL}`);
appendGuiLog("INFO", `Hotkey: ${HOTKEY}`);
