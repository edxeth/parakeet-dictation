import Electrobun, { Electroview } from "electrobun/view";

type TranscriptPayload = {
  schema_version: number;
  transcript: string;
  normalized_transcript?: string;
  device?: string;
  metadata?: Record<string, unknown>;
};

type TranscriptHistoryItem = {
  id: string;
  completed_at: number;
  payload: TranscriptPayload;
};

type SessionPayload = {
  schema_version: number;
  state: "stopped" | "idle" | "starting" | "recording" | "transcribing" | "error";
  started_at: number | null;
  last_completed_at: number | null;
  last_transcript: TranscriptPayload | null;
  last_error: string | null;
  model_loaded: boolean;
  history: TranscriptHistoryItem[];
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
  bun: {
    requests: {
      getBridgeState: { params: {}; response: BridgeViewState };
      startRecording: { params: {}; response: BridgeViewState };
      stopRecording: { params: {}; response: BridgeViewState };
      toggleRecording: { params: {}; response: BridgeViewState };
      showWindow: { params: {}; response: { success: true } };
    };
    messages: {
      bridgeStateUpdated: { params: BridgeViewState };
      bridgeError: { params: { message: string } };
    };
  };
  webview: {
    requests: {};
    messages: {
      rendererBooted: { params: { href: string } };
      rendererError: { params: { message: string } };
    };
  };
};

const rpc = Electroview.defineRPC<DesktopRPC>({
  maxRequestTime: 30000,
  handlers: {
    requests: {},
    messages: {},
  },
});

const electrobun = new Electrobun.Electroview({ rpc });

window.addEventListener("error", (event) => {
  try {
    (electrobun.rpc as any)?.send?.rendererError({ message: event.message || "unknown renderer error" });
  } catch {
    // ignore transport bootstrap failures while reporting renderer errors
  }
});

const statusBadge = document.getElementById("statusBadge") as HTMLDivElement;
const hotkeyValue = document.getElementById("hotkeyValue") as HTMLDivElement;
const toggleButton = document.getElementById("toggleButton") as HTMLButtonElement;
const statusLine = document.getElementById("statusLine") as HTMLParagraphElement;
const bridgeUrl = document.getElementById("bridgeUrl") as HTMLDivElement;
const bridgeCommand = document.getElementById("bridgeCommand") as HTMLPreElement;
const historyMeta = document.getElementById("historyMeta") as HTMLDivElement;
const historyList = document.getElementById("historyList") as HTMLDivElement;
const errorBox = document.getElementById("errorBox") as HTMLPreElement;
const refreshButton = document.getElementById("refreshButton") as HTMLButtonElement;

let currentState: BridgeViewState | null = null;

function formatTimestamp(timestamp: number | null): string {
  if (!timestamp) return "—";
  return new Date(timestamp * 1000).toLocaleTimeString();
}

function setBusy(busy: boolean) {
  toggleButton.disabled = busy;
  refreshButton.disabled = busy;
}

async function copyTranscript(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (error) {
    errorBox.textContent = error instanceof Error ? error.message : String(error);
  }
}

function renderHistory(items: TranscriptHistoryItem[]) {
  if (!items.length) {
    historyMeta.textContent = "No transcripts yet.";
    historyList.innerHTML = `<div class="history-empty">No transcripts yet.</div>`;
    return;
  }

  historyMeta.textContent = `${items.length} transcript${items.length === 1 ? "" : "s"}`;
  historyList.innerHTML = items
    .slice()
    .reverse()
    .map((item) => {
      const payload = item.payload;
      const cancelled = Boolean(payload.metadata?.cancelled_before_recording);
      const body = payload.transcript || (cancelled ? "Cancelled before recording started." : "");
      return `
        <div class="history-item">
          <div class="history-header">
            <div class="history-time">${formatTimestamp(item.completed_at)}${payload.device ? ` • ${payload.device}` : ""}</div>
            <div class="history-actions">
              <button class="history-copy" data-copy="${encodeURIComponent(payload.transcript || "")}">Copy</button>
            </div>
          </div>
          <div class="history-body">${body || "<empty transcript>"}</div>
        </div>
      `;
    })
    .join("");

  historyList.querySelectorAll<HTMLButtonElement>(".history-copy").forEach((button) => {
    button.addEventListener("click", () => {
      void copyTranscript(decodeURIComponent(button.dataset.copy || ""));
    });
  });
}

function renderState(viewState: BridgeViewState) {
  currentState = viewState;
  const sessionState = viewState.connected ? viewState.session.state : "offline";
  statusBadge.textContent = viewState.connected ? viewState.session.state : "Disconnected";
  statusBadge.className = `badge ${sessionState}`;

  hotkeyValue.textContent = viewState.hotkey;
  bridgeUrl.textContent = viewState.bridgeUrl;
  bridgeCommand.textContent = viewState.bridgeStartCommand;
  renderHistory(viewState.session.history || []);

  if (!viewState.connected) {
    toggleButton.textContent = "Bridge offline";
    statusLine.textContent = "Start the WSL bridge command below, then use the button or hotkey.";
  } else if (viewState.session.state === "starting") {
    toggleButton.textContent = "Cancel loading";
    statusLine.textContent = viewState.session.model_loaded
      ? "Preparing to record…"
      : "Loading model. Wait until recording starts before speaking, or click again to cancel.";
  } else if (viewState.session.state === "recording") {
    toggleButton.textContent = "Stop recording";
    statusLine.textContent = `Recording in progress since ${formatTimestamp(viewState.session.started_at)}.`;
  } else if (viewState.session.state === "transcribing") {
    toggleButton.textContent = "Transcribing…";
    statusLine.textContent = "Audio captured. Waiting for transcript…";
  } else if (viewState.session.state === "error") {
    toggleButton.textContent = "Try again";
    statusLine.textContent = "Bridge reachable but the backend reported an error.";
  } else {
    toggleButton.textContent = viewState.session.model_loaded ? "Start recording" : "Load + record";
    statusLine.textContent = viewState.session.model_loaded
      ? "Model ready. Press the button or the hotkey to begin."
      : "Ready. First recording will load the model; after that it stays warm.";
  }

  const errorLines = [];
  if (viewState.session.last_error) errorLines.push(viewState.session.last_error);
  if (viewState.session.stderr_tail.length) errorLines.push(...viewState.session.stderr_tail.slice(-8));
  errorBox.textContent = errorLines.length ? errorLines.join("\n") : "No bridge errors.";
}

async function refreshState() {
  setBusy(true);
  try {
    const state = await electrobun.rpc!.request.getBridgeState({});
    renderState(state);
  } finally {
    setBusy(false);
  }
}

async function toggleRecording() {
  setBusy(true);
  try {
    const current = await electrobun.rpc!.request.getBridgeState({});
    const next = current.connected && ["starting", "recording"].includes(current.session.state)
      ? await electrobun.rpc!.request.stopRecording({})
      : await electrobun.rpc!.request.startRecording({});
    renderState(next);
  } catch (error) {
    errorBox.textContent = error instanceof Error ? error.message : String(error);
  } finally {
    setBusy(false);
  }
}

toggleButton.addEventListener("click", () => {
  void toggleRecording();
});
refreshButton.addEventListener("click", () => {
  void refreshState();
});
window.addEventListener("keydown", (event) => {
  const pressedR = event.key.toLowerCase() === "r";
  if (pressedR && event.ctrlKey && event.altKey) {
    event.preventDefault();
    void toggleRecording();
  }
});

(electrobun.rpc as any)?.addMessageListener("bridgeStateUpdated", (state: BridgeViewState) => {
  renderState(state);
});

(electrobun.rpc as any)?.addMessageListener("bridgeError", (payload: { message: string }) => {
  errorBox.textContent = payload.message;
});

try {
  (electrobun.rpc as any)?.send?.rendererBooted({ href: window.location.href });
} catch {
  // ignore if transport is not ready yet
}

void refreshState();
