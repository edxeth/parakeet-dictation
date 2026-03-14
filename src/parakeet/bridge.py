"""Opt-in localhost bridge for controlling Parakeet dictation from a desktop app."""

from __future__ import annotations

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import signal
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from parakeet.audio import list_input_devices
from parakeet.dictation import (
    _load_model,
    _load_runtime_dependencies,
    _transcribe_once,
    record_audio_interruptible,
)
from parakeet.doctor import collect_doctor_report
from parakeet.errors import ExitCode, ModelError
from parakeet.output import copy_transcript_to_clipboard, render_transcription
from parakeet.types import DictationConfig, TranscriptionResult


BRIDGE_SCHEMA_VERSION = 1


class BridgeStateError(RuntimeError):
    """Raised when a bridge request is invalid for the current session state."""


class _DiagnosticStream:
    def __init__(self, controller: "DictationBridgeController") -> None:
        self._controller = controller

    def write(self, text: str) -> int:
        normalized = text.replace("\x1b[2K", "").replace("\r", "\n")
        if "Loading model" in normalized:
            self._controller._append_diagnostic("⏳ Loading model")
            return len(text)
        if "Generating" in normalized:
            self._controller._append_diagnostic("🤖 Generating...")
            return len(text)
        for line in normalized.splitlines():
            stripped = line.strip()
            if stripped:
                self._controller._append_diagnostic(stripped)
        return len(text)

    def flush(self) -> None:
        return None


class DictationBridgeController:
    def __init__(
        self,
        *,
        cpu: bool = False,
        input_device: int | str | None = None,
        vad: bool = False,
        max_silence_ms: int = 1200,
        min_speech_ms: int = 300,
        vad_mode: int = 2,
        debug: bool = False,
        log_file: str = "transcriber.debug.log",
        clipboard: bool = True,
        transcript_timeout: float = 300.0,
        stderr_tail_limit: int = 200,
        runtime_loader: Callable[[bool], tuple[Any, Any, Any, Any]] = _load_runtime_dependencies,
        model_loader: Callable[..., tuple[Any, bool, float, float]] = _load_model,
        recorder: Callable[..., bytes | None] = record_audio_interruptible,
        transcriber: Callable[..., tuple[TranscriptionResult, str, float, float]] = _transcribe_once,
    ) -> None:
        self.cpu = cpu
        self.input_device = input_device
        self.vad = vad
        self.max_silence_ms = max_silence_ms
        self.min_speech_ms = min_speech_ms
        self.vad_mode = vad_mode
        self.debug = debug
        self.log_file = log_file
        self.clipboard = clipboard
        self._transcript_timeout = transcript_timeout
        self._stderr_tail_limit = stderr_tail_limit
        self._runtime_loader = runtime_loader
        self._model_loader = model_loader
        self._recorder = recorder
        self._transcriber = transcriber

        self._lock = threading.RLock()
        self._runtime_ready = threading.Event()
        self._session_finished = threading.Event()
        self._stop_requested = threading.Event()
        self._shutdown_requested = threading.Event()
        self._session_thread: threading.Thread | None = None
        self._state = "stopped"
        self._started_at: float | None = None
        self._last_completed_at: float | None = None
        self._last_transcript: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._stderr_tail: list[str] = []
        self._history: list[dict[str, Any]] = []

        self._nemo_asr: Any | None = None
        self._pyaudio_module: Any | None = None
        self._pyperclip_module: Any | None = None
        self._torch_module: Any | None = None
        self._model: Any | None = None
        self._model_loaded = False
        self._model_loading = False
        self._warmup_thread: threading.Thread | None = None
        self._diagnostic_stream = _DiagnosticStream(self)

    def _append_diagnostic(self, text: str) -> None:
        should_echo = False
        with self._lock:
            if not text:
                return
            if self._stderr_tail and self._stderr_tail[-1] == text:
                return
            self._stderr_tail.append(text)
            should_echo = True
            if len(self._stderr_tail) > self._stderr_tail_limit:
                self._stderr_tail = self._stderr_tail[-self._stderr_tail_limit :]
            if text == "🎤 Recording..." and self._state == "starting":
                self._state = "recording"
        if should_echo:
            print(text, file=sys.stdout, flush=True)

    def _config(self) -> DictationConfig:
        return DictationConfig(
            cpu=self.cpu,
            input_device=self.input_device,
            vad=self.vad,
            max_silence_ms=self.max_silence_ms,
            min_speech_ms=self.min_speech_ms,
            vad_mode=self.vad_mode,
            format="json",
            output_file=None,
            clipboard=self.clipboard,
            debug=self.debug,
            log_file=self.log_file,
            list_devices=False,
        )

    def _ensure_runtime_loaded(self) -> None:
        if self._runtime_ready.is_set():
            return
        self._append_diagnostic("Starting...")
        nemo_asr, pyaudio_module, pyperclip_module, torch_module = self._runtime_loader(self.debug)
        self._nemo_asr = nemo_asr
        self._pyaudio_module = pyaudio_module
        self._pyperclip_module = pyperclip_module
        self._torch_module = torch_module
        self._runtime_ready.set()

    def _ensure_model_loaded(self, config: DictationConfig) -> None:
        if self._model_loaded:
            return
        if self._nemo_asr is None or self._torch_module is None:
            raise RuntimeError("Bridge runtime dependencies are not loaded")
        model, _use_cuda, _load_start, _load_end = self._model_loader(
            config,
            self._nemo_asr,
            self._torch_module,
            status_stream=self._diagnostic_stream,
        )
        self._model = model
        self._model_loaded = True

    def _warmup_worker(self) -> None:
        try:
            config = self._config()
            self._ensure_runtime_loaded()
            if self._shutdown_requested.is_set():
                return
            self._ensure_model_loaded(config)
        except Exception as exc:  # pragma: no cover - defensive warmup guard
            with self._lock:
                self._last_error = str(exc)
        finally:
            with self._lock:
                self._model_loading = False

    def start_model_warmup(self) -> None:
        with self._lock:
            if self._model_loaded or self._model_loading:
                return
            self._model_loading = True
            self._last_error = None
            self._warmup_thread = threading.Thread(target=self._warmup_worker, daemon=True)
            self._warmup_thread.start()

    def _complete_session(self, transcription: TranscriptionResult) -> None:
        payload = json.loads(render_transcription(transcription, "json"))
        completed_at = time.time()
        history_item = {
            "id": f"tx-{int(completed_at * 1000)}",
            "completed_at": completed_at,
            "payload": payload,
        }
        with self._lock:
            self._last_transcript = payload
            self._last_completed_at = completed_at
            self._last_error = None
            self._state = "idle"
            self._started_at = None
            self._history.append(history_item)

    def _complete_cancelled_before_recording(self) -> None:
        with self._lock:
            if self._last_transcript and self._last_transcript.get("metadata", {}).get("cancelled_before_recording"):
                self._state = "idle"
                self._started_at = None
                self._last_error = None
                return
        self._complete_session(
            TranscriptionResult(
                text="",
                metadata={"cancelled_before_recording": True},
            )
        )

    def _copy_to_clipboard(self, transcription: TranscriptionResult) -> None:
        if not self.clipboard or self._pyperclip_module is None:
            return
        warning = copy_transcript_to_clipboard(transcription, self._pyperclip_module)
        if warning is not None:
            self._append_diagnostic(f"Clipboard warning: {warning}")

    def _session_worker(self) -> None:
        config = self._config()
        temp_path: str | None = None
        try:
            self._ensure_runtime_loaded()
            if self._stop_requested.is_set() or self._shutdown_requested.is_set():
                self._complete_cancelled_before_recording()
                return

            self._ensure_model_loaded(config)
            if self._stop_requested.is_set() or self._shutdown_requested.is_set():
                self._complete_cancelled_before_recording()
                return

            with self._lock:
                self._state = "recording"

            if self._pyaudio_module is None:
                raise RuntimeError("PyAudio runtime is unavailable")

            audio_data = self._recorder(
                config,
                self._pyaudio_module,
                sample_rate=16000,
                stop_requested=lambda: self._stop_requested.is_set() or self._shutdown_requested.is_set(),
                status_stream=self._diagnostic_stream,
            )

            if self._shutdown_requested.is_set():
                with self._lock:
                    self._state = "stopped"
                    self._started_at = None
                return

            if not audio_data:
                self._complete_cancelled_before_recording()
                return

            with self._lock:
                self._state = "transcribing"

            if self._model is None:
                raise RuntimeError("Model is not loaded")
            transcription, temp_path, _infer_start, _infer_end = self._transcriber(
                config,
                self._model,
                audio_data,
                16000,
                status_stream=self._diagnostic_stream,
            )
            self._copy_to_clipboard(transcription)
            self._complete_session(transcription)
        except ModelError as exc:
            with self._lock:
                self._last_error = str(exc)
                self._state = "error"
                self._started_at = None
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            with self._lock:
                self._last_error = str(exc)
                self._state = "error"
                self._started_at = None
        finally:
            if temp_path:
                try:
                    import os
                    os.unlink(temp_path)
                except Exception:
                    pass
            self._session_finished.set()

    def shutdown(self) -> None:
        self._shutdown_requested.set()
        self._stop_requested.set()
        thread = self._session_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        with self._lock:
            self._state = "stopped"
            self._started_at = None

    def start_session(self) -> dict[str, Any]:
        with self._lock:
            if self._state in {"starting", "recording", "transcribing"}:
                raise BridgeStateError(f"Cannot start while session is {self._state}")
            if self._model_loading and not self._model_loaded:
                raise BridgeStateError("Model is still loading")
            if self._session_thread is not None and self._session_thread.is_alive():
                raise BridgeStateError("Cannot start while the previous session is still winding down")
            self._stop_requested.clear()
            self._session_finished.clear()
            self._state = "starting"
            self._started_at = time.time()
            self._last_error = None
            self._session_thread = threading.Thread(target=self._session_worker, daemon=True)
            self._session_thread.start()
            return self.get_session_payload()

    def stop_session(self) -> dict[str, Any]:
        with self._lock:
            if self._state not in {"starting", "recording"}:
                raise BridgeStateError(f"Cannot stop while session is {self._state}")
            current_state = self._state
            self._stop_requested.set()
            if current_state == "starting" and not self._model_loaded:
                self._complete_cancelled_before_recording()
                return self.get_session_payload()
        thread = self._session_thread
        if thread is not None:
            thread.join(timeout=min(self._transcript_timeout, 10.0))
        if not self._session_finished.is_set():
            with self._lock:
                self._state = "error"
                self._last_error = "Timed out waiting for session to stop"
            raise TimeoutError(self._last_error)
        return self.get_session_payload()

    def toggle_session(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
        if state in {"starting", "recording"}:
            return self.stop_session()
        if state == "transcribing":
            raise BridgeStateError("Session is still transcribing")
        return self.start_session()

    def clear_history(self) -> dict[str, Any]:
        with self._lock:
            self._history.clear()
            self._last_transcript = None
            self._last_completed_at = None
            return self.get_session_payload()

    def health_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": BRIDGE_SCHEMA_VERSION,
                "ok": True,
                "bridge": {
                    "backend": "parakeet-dictation-bridge",
                    "model_loaded": self._model_loaded,
                    "model_loading": self._model_loading,
                },
                "session": self.get_session_payload(),
            }

    def get_session_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": BRIDGE_SCHEMA_VERSION,
                "state": self._state,
                "started_at": self._started_at,
                "last_completed_at": self._last_completed_at,
                "last_transcript": self._last_transcript,
                "last_error": self._last_error,
                "model_loaded": self._model_loaded,
                "model_loading": self._model_loading,
                "history": list(self._history),
                "config": {
                    "cpu": self.cpu,
                    "input_device": self.input_device,
                    "vad": self.vad,
                    "max_silence_ms": self.max_silence_ms,
                    "min_speech_ms": self.min_speech_ms,
                    "vad_mode": self.vad_mode,
                    "debug": self.debug,
                    "clipboard": self.clipboard,
                },
                "stderr_tail": list(self._stderr_tail[-20:]),
            }


class _BridgeHandler(BaseHTTPRequestHandler):
    controller: DictationBridgeController

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler interface
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/health":
            self._write_json(200, self.controller.health_payload())
            return
        if parsed.path == "/session":
            self._write_json(200, self.controller.get_session_payload())
            return
        if parsed.path == "/devices":
            devices = list_input_devices()
            self._write_json(
                200,
                {
                    "schema_version": 1,
                    "devices": [asdict(device) for device in devices],
                },
            )
            return
        if parsed.path == "/doctor":
            report = collect_doctor_report(check_model_cache=_truthy_query(query.get("check_model_cache")))
            self._write_json(200, asdict(report))
            return
        self._write_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler interface
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/session/start":
                self._write_json(200, self.controller.start_session())
                return
            if parsed.path == "/session/stop":
                self._write_json(200, self.controller.stop_session())
                return
            if parsed.path == "/session/toggle":
                self._write_json(200, self.controller.toggle_session())
                return
            if parsed.path == "/session/clear-history":
                self._write_json(200, self.controller.clear_history())
                return
        except BridgeStateError as exc:
            self._write_json(409, {"error": "invalid_state", "detail": str(exc)})
            return
        except TimeoutError as exc:
            self._write_json(504, {"error": "timeout", "detail": str(exc)})
            return
        except Exception as exc:  # pragma: no cover - defensive transport layer
            self._write_json(500, {"error": "internal_error", "detail": str(exc)})
            return

        self._write_json(404, {"error": "not_found"})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003 - stdlib signature
        return

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _truthy_query(values: list[str] | None) -> bool:
    if not values:
        return False
    return values[0].strip().lower() in {"1", "true", "yes", "on"}


def make_bridge_server(
    host: str,
    port: int,
    *,
    controller: DictationBridgeController,
) -> ThreadingHTTPServer:
    handler = type("ParakeetBridgeHandler", (_BridgeHandler,), {"controller": controller})
    return ThreadingHTTPServer((host, port), handler)


def build_bridge_controller_from_namespace(namespace: Any) -> DictationBridgeController:
    return DictationBridgeController(
        cpu=bool(getattr(namespace, "cpu", False)),
        input_device=getattr(namespace, "input_device", None),
        vad=bool(getattr(namespace, "vad", False)),
        max_silence_ms=int(getattr(namespace, "max_silence_ms", 1200)),
        min_speech_ms=int(getattr(namespace, "min_speech_ms", 300)),
        vad_mode=int(getattr(namespace, "vad_mode", 2)),
        debug=bool(getattr(namespace, "debug", False)),
        log_file=str(getattr(namespace, "log_file", "transcriber.debug.log")),
        clipboard=bool(getattr(namespace, "clipboard", True)),
    )


def run_bridge_server(namespace: Any) -> int:
    host = str(getattr(namespace, "host", "127.0.0.1"))
    port = int(getattr(namespace, "port", 8765))
    controller = build_bridge_controller_from_namespace(namespace)
    controller.start_model_warmup()
    server = make_bridge_server(host, port, controller=controller)

    shutdown_requested = threading.Event()
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _request_shutdown(signum: int, frame: Any) -> None:
        shutdown_requested.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    print(f"Parakeet bridge listening on http://{host}:{port}")
    print("Run the Windows app, then use its button or hotkey to start/stop recording.")

    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if not shutdown_requested.is_set():
            server.shutdown()
        server.server_close()
        controller.shutdown()
    return 0
