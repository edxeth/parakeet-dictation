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
  model_loading: boolean;
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
      clearHistory: { params: {}; response: BridgeViewState };
      showWindow: { params: {}; response: { success: true } };
    };
    messages: {};
  };
  webview: {
    requests: {};
    messages: {};
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

const busyOverlay = document.getElementById("busyOverlay") as HTMLDivElement;
const busyTitle = document.getElementById("busyTitle") as HTMLDivElement;
const busyMessage = document.getElementById("busyMessage") as HTMLDivElement;
const confirmOverlay = document.getElementById("confirmOverlay") as HTMLDivElement;
const confirmTitle = document.getElementById("confirmTitle") as HTMLDivElement;
const confirmMessage = document.getElementById("confirmMessage") as HTMLDivElement;
const confirmCancelButton = document.getElementById("confirmCancelButton") as HTMLButtonElement;
const confirmOkButton = document.getElementById("confirmOkButton") as HTMLButtonElement;
const appShell = document.getElementById("appShell") as HTMLDivElement;
const statusBadge = document.getElementById("statusBadge") as HTMLDivElement;
const hotkeyValue = document.getElementById("hotkeyValue") as HTMLDivElement;
const toggleButton = document.getElementById("toggleButton") as HTMLButtonElement;
const statusLine = document.getElementById("statusLine") as HTMLParagraphElement;
const bridgeUrl = document.getElementById("bridgeUrl") as HTMLDivElement;
const bridgeCommand = document.getElementById("bridgeCommand") as HTMLPreElement;
const historyMeta = document.getElementById("historyMeta") as HTMLDivElement;
const historyList = document.getElementById("historyList") as HTMLDivElement;
const showMoreButton = document.getElementById("showMoreButton") as HTMLButtonElement;
const errorBox = document.getElementById("errorBox") as HTMLPreElement;
const clearHistoryButton = document.getElementById("clearHistoryButton") as HTMLButtonElement;

window.addEventListener("error", (event) => {
  errorBox.textContent = event.message || "unknown renderer error";
});

let currentState: BridgeViewState | null = null;
let visibleHistoryCount = 10;

function formatTimestamp(timestamp: number | null): string {
  if (!timestamp) return "—";
  return new Date(timestamp * 1000).toLocaleTimeString();
}

function renderHotkey(accelerator: string): string {
  const isMac = navigator.platform.toLowerCase().includes("mac");
  const keyMap: Record<string, string> = {
    CommandOrControl: isMac ? "⌘" : "Ctrl",
    Command: "⌘",
    Control: "Ctrl",
    Alt: isMac ? "⌥" : "Alt",
    Option: "⌥",
    Shift: "⇧",
    Super: isMac ? "⌃" : "Win",
  };
  const parts = accelerator.split("+").map((part) => part.trim()).filter(Boolean);
  return `<span class="hotkey-display">${parts
    .map((part) => `<span class="hotkey-key">${keyMap[part] || part}</span>`)
    .join("")}</span>`;
}

function setBusy(busy: boolean) {
  toggleButton.disabled = busy;
  clearHistoryButton.disabled = busy;
}

function setOverlay(visible: boolean, title = "Working…", message = "Please wait.") {
  busyOverlay.classList.toggle("hidden", !visible);
  appShell.classList.toggle("locked", visible);
  busyOverlay.setAttribute("aria-hidden", visible ? "false" : "true");
  busyTitle.textContent = title;
  busyMessage.textContent = message;
}

function confirmAction(title: string, message: string): Promise<boolean> {
  confirmTitle.textContent = title;
  confirmMessage.textContent = message;
  confirmOverlay.classList.remove("hidden");
  confirmOverlay.setAttribute("aria-hidden", "false");

  return new Promise((resolve) => {
    const cleanup = () => {
      confirmOverlay.classList.add("hidden");
      confirmOverlay.setAttribute("aria-hidden", "true");
      confirmCancelButton.removeEventListener("click", onCancel);
      confirmOkButton.removeEventListener("click", onOk);
    };
    const onCancel = () => {
      cleanup();
      resolve(false);
    };
    const onOk = () => {
      cleanup();
      resolve(true);
    };
    confirmCancelButton.addEventListener("click", onCancel, { once: true });
    confirmOkButton.addEventListener("click", onOk, { once: true });
  });
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
    showMoreButton.classList.add("hidden");
    return;
  }

  const total = items.length;
  const visibleItems = items.slice().reverse().slice(0, visibleHistoryCount);
  historyMeta.textContent = `${total} transcript${total === 1 ? "" : "s"}`;
  historyList.innerHTML = visibleItems
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

  showMoreButton.classList.toggle("hidden", visibleHistoryCount >= total);
  historyList.querySelectorAll<HTMLButtonElement>(".history-copy").forEach((button) => {
    button.addEventListener("click", () => {
      void copyTranscript(decodeURIComponent(button.dataset.copy || ""));
    });
  });
}

function renderState(viewState: BridgeViewState) {
  if (currentState && viewState.session.history.length > currentState.session.history.length) {
    visibleHistoryCount = Math.max(visibleHistoryCount, 10);
  }
  currentState = viewState;
  const sessionState = viewState.connected ? viewState.session.state : "offline";
  statusBadge.textContent = viewState.connected ? viewState.session.state : "Disconnected";
  statusBadge.className = `badge ${sessionState}`;

  hotkeyValue.innerHTML = renderHotkey(viewState.hotkey);
  bridgeUrl.textContent = viewState.bridgeUrl;
  bridgeCommand.textContent = viewState.bridgeStartCommand;
  renderHistory(viewState.session.history || []);

  const lockedForBusyWork = viewState.connected && (viewState.session.model_loading || ["starting", "transcribing"].includes(viewState.session.state));
  if (viewState.session.model_loading) {
    setOverlay(true, "Loading model…", "The bridge is warming the model in the background. Recording will unlock automatically when it is ready.");
  } else if (viewState.session.state === "starting") {
    setOverlay(true, "Preparing to record…", "Recording is being prepared. Please wait a moment.");
  } else if (viewState.session.state === "transcribing") {
    setOverlay(true, "Transcribing…", "Audio has been captured. Please wait while Parakeet generates the transcript.");
  } else {
    setOverlay(false);
  }
  toggleButton.disabled = !viewState.connected || viewState.session.model_loading || viewState.session.state === "transcribing";
  clearHistoryButton.disabled = !viewState.session.history.length || lockedForBusyWork;

  if (!viewState.connected) {
    toggleButton.textContent = "Bridge offline";
    statusLine.textContent = "Start the WSL bridge command below, then use the button or hotkey.";
  } else if (viewState.session.model_loading) {
    toggleButton.textContent = "Loading model…";
    statusLine.textContent = "Bridge is warming the model. Recording will unlock automatically when ready.";
  } else if (viewState.session.state === "starting") {
    toggleButton.textContent = "Cancel loading";
    statusLine.textContent = "Preparing to record…";
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
  if (viewState.session.stderr_tail.length) errorLines.push(...viewState.session.stderr_tail.slice(-20));
  errorBox.textContent = errorLines.length ? errorLines.join("\n") : "No bridge errors.";
  errorBox.scrollTop = errorBox.scrollHeight;
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

async function clearHistory() {
  const shouldClear = await confirmAction(
    "Clear history?",
    "Clear all transcription history for this bridge session? This cannot be undone.",
  );
  if (!shouldClear) return;
  setBusy(true);
  try {
    const next = await electrobun.rpc!.request.clearHistory({});
    visibleHistoryCount = 10;
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
clearHistoryButton.addEventListener("click", () => {
  void clearHistory();
});
showMoreButton.addEventListener("click", () => {
  visibleHistoryCount += 10;
  if (currentState) {
    renderHistory(currentState.session.history || []);
  }
});
window.addEventListener("keydown", (event) => {
  const pressedR = event.key.toLowerCase() === "r";
  if (pressedR && event.ctrlKey && event.altKey) {
    event.preventDefault();
    void toggleRecording();
  }
});

setInterval(() => {
  void refreshState();
}, 1000);

void refreshState();
