"""Opt-in localhost bridge for controlling Parakeet dictation from a desktop app."""

from __future__ import annotations

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from parakeet.audio import list_input_devices
from parakeet.doctor import collect_doctor_report


BRIDGE_SCHEMA_VERSION = 1


class BridgeStateError(RuntimeError):
    """Raised when a bridge request is invalid for the current session state."""


class DictationBridgeController:
    def __init__(
        self,
        *,
        python_executable: str = sys.executable,
        cpu: bool = False,
        input_device: int | str | None = None,
        vad: bool = False,
        max_silence_ms: int = 1200,
        min_speech_ms: int = 300,
        vad_mode: int = 2,
        debug: bool = False,
        log_file: str = "transcriber.debug.log",
        clipboard: bool = False,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        transcript_timeout: float = 300.0,
        stderr_tail_limit: int = 200,
    ) -> None:
        self.python_executable = python_executable
        self.cpu = cpu
        self.input_device = input_device
        self.vad = vad
        self.max_silence_ms = max_silence_ms
        self.min_speech_ms = min_speech_ms
        self.vad_mode = vad_mode
        self.debug = debug
        self.log_file = log_file
        self.clipboard = clipboard
        self._popen_factory = popen_factory
        self._transcript_timeout = transcript_timeout
        self._stderr_tail_limit = stderr_tail_limit

        self._lock = threading.RLock()
        self._result_ready = threading.Condition(self._lock)
        self._process: Any | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._state = "stopped"
        self._started_at: float | None = None
        self._last_completed_at: float | None = None
        self._last_transcript: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._stderr_tail: list[str] = []
        self._transcript_counter = 0
        self._capture_started = False

    def _build_command(self) -> list[str]:
        command = [
            self.python_executable,
            "-u",
            "-m",
            "parakeet.cli",
            "dictation",
            "--format",
            "json",
            "--bridge-mode",
        ]
        if self.cpu:
            command.append("--cpu")
        if self.input_device is not None:
            command.extend(["--input-device", str(self.input_device)])
        if self.vad:
            command.append("--vad")
        if self.max_silence_ms != 1200:
            command.extend(["--max-silence-ms", str(self.max_silence_ms)])
        if self.min_speech_ms != 300:
            command.extend(["--min-speech-ms", str(self.min_speech_ms)])
        if self.vad_mode != 2:
            command.extend(["--vad-mode", str(self.vad_mode)])
        if self.debug:
            command.append("--debug")
            if self.log_file:
                command.extend(["--log-file", self.log_file])
        if self.clipboard:
            command.append("--clipboard")
        else:
            command.append("--no-clipboard")
        return command

    def _spawn_process(self) -> Any:
        return self._popen_factory(
            self._build_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )

    def ensure_running(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return

            self._process = self._spawn_process()
            self._state = "idle"
            self._last_error = None
            self._stderr_tail = []
            self._capture_started = False
            self._stdout_thread = threading.Thread(target=self._stdout_reader, daemon=True)
            self._stderr_thread = threading.Thread(target=self._stderr_reader, daemon=True)
            self._stdout_thread.start()
            self._stderr_thread.start()

    def shutdown(self) -> None:
        process = None
        with self._lock:
            process = self._process
            self._process = None
            self._state = "stopped"
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _stdout_reader(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        while True:
            line = process.stdout.readline()
            if line == "":
                break
            payload_text = line.strip()
            if not payload_text:
                continue
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                with self._lock:
                    self._last_error = f"Unexpected stdout from dictation subprocess: {payload_text}"
                continue

            with self._result_ready:
                self._last_transcript = payload
                self._last_completed_at = time.time()
                self._state = "idle"
                self._started_at = None
                self._last_error = None
                self._capture_started = False
                self._transcript_counter += 1
                self._result_ready.notify_all()

        with self._result_ready:
            if self._process is process and process.poll() not in {None, 0}:
                stderr_preview = self.stderr_tail(limit=20)
                self._last_error = (
                    f"Dictation subprocess exited with code {process.poll()}"
                    + (f": {stderr_preview}" if stderr_preview else "")
                )
                self._state = "error"
                self._process = None
                self._result_ready.notify_all()
            elif self._process is process and process.poll() == 0:
                self._process = None
                self._result_ready.notify_all()

    def _stderr_reader(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return

        while True:
            line = process.stderr.readline()
            if line == "":
                break
            text = line.rstrip("\n")
            if not text:
                continue
            with self._lock:
                self._stderr_tail.append(text)
                if "🎤 Recording..." in text:
                    self._capture_started = True
                if len(self._stderr_tail) > self._stderr_tail_limit:
                    self._stderr_tail = self._stderr_tail[-self._stderr_tail_limit :]

    def stderr_tail(self, *, limit: int = 50) -> str:
        with self._lock:
            return "\n".join(self._stderr_tail[-limit:])

    def _signal_stop(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            raise BridgeStateError("Dictation subprocess is not running")
        if hasattr(signal, "SIGUSR1"):
            os.kill(process.pid, signal.SIGUSR1)
        else:  # pragma: no cover - non-Linux fallback
            process.terminate()

    def start_session(self) -> dict[str, Any]:
        with self._lock:
            if self._state in {"recording", "transcribing"}:
                raise BridgeStateError(f"Cannot start while session is {self._state}")
            self.ensure_running()
            self._state = "recording"
            self._started_at = time.time()
            self._last_error = None
            return self.get_session_payload()

    def stop_session(self) -> dict[str, Any]:
        with self._result_ready:
            if self._state != "recording":
                raise BridgeStateError(f"Cannot stop while session is {self._state}")

            if not self._capture_started:
                process = self._process
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except Exception:
                        process.kill()
                self._process = None
                self._state = "idle"
                self._started_at = None
                self._last_completed_at = time.time()
                self._last_error = None
                self._capture_started = False
                self._last_transcript = {
                    "schema_version": 1,
                    "transcript": "",
                    "metadata": {"cancelled_before_recording": True},
                }
                return self.get_session_payload()

            counter_before = self._transcript_counter
            self._signal_stop()
            self._state = "transcribing"
            deadline = time.time() + self._transcript_timeout
            while self._transcript_counter == counter_before:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self._last_error = "Timed out waiting for transcription result"
                    self._state = "error"
                    raise TimeoutError(self._last_error)
                self._result_ready.wait(timeout=remaining)
            return self.get_session_payload()

    def toggle_session(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
        if state == "recording":
            return self.stop_session()
        if state == "transcribing":
            raise BridgeStateError("Session is still transcribing")
        return self.start_session()

    def health_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": BRIDGE_SCHEMA_VERSION,
                "ok": True,
                "bridge": {
                    "backend": "parakeet-dictation-bridge",
                    "python": self.python_executable,
                    "dictation_pid": None if self._process is None else self._process.pid,
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
        clipboard=bool(getattr(namespace, "clipboard", False)),
    )


def run_bridge_server(namespace: Any) -> int:
    host = str(getattr(namespace, "host", "127.0.0.1"))
    port = int(getattr(namespace, "port", 8765))
    controller = build_bridge_controller_from_namespace(namespace)
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
