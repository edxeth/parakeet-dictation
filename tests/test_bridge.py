from __future__ import annotations

import importlib
import json
from pathlib import Path
import queue
import threading
import time
import urllib.request
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

bridge_module = importlib.import_module("parakeet.bridge")
BridgeStateError = bridge_module.BridgeStateError
DictationBridgeController = bridge_module.DictationBridgeController


class _QueueStream:
    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()

    def readline(self) -> str:
        return self._queue.get(timeout=2)

    def push(self, text: str) -> None:
        self._queue.put(text)

    def close(self) -> None:
        self._queue.put("")


_FAKE_PROCESS_REGISTRY: dict[int, "_FakeProcess"] = {}
_FAKE_NEXT_PID = 4242


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, text: str) -> int:
        self.writes.append(text)
        return len(text)

    def flush(self) -> None:
        return None


class _FakeProcess:
    def __init__(self, command: list[str]) -> None:
        global _FAKE_NEXT_PID
        self.command = command
        self.pid = _FAKE_NEXT_PID
        _FAKE_NEXT_PID += 1
        _FAKE_PROCESS_REGISTRY[self.pid] = self
        self.returncode: int | None = None
        self.stdout = _QueueStream()
        self.stderr = _QueueStream()
        self.stdin = _FakeStdin()
        self._transcript_count = 0
        threading.Thread(target=lambda: self.stderr.push("🎤 Recording...\n"), daemon=True).start()

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0
        self.stdout.close()
        self.stderr.close()
        _FAKE_PROCESS_REGISTRY.pop(self.pid, None)

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0

    def kill(self) -> None:
        self.terminate()

    def on_signal_stop(self) -> None:
        self._transcript_count += 1
        payload = {
            "schema_version": 1,
            "transcript": f"fake transcript {self._transcript_count}",
            "device": "cpu",
        }

        def _finish() -> None:
            self.stdout.push(json.dumps(payload) + "\n")
            self.returncode = 0
            self.stdout.close()
            self.stderr.close()
            _FAKE_PROCESS_REGISTRY.pop(self.pid, None)

        threading.Thread(target=_finish, daemon=True).start()


def _fake_popen_factory(commands: list[list[str]], kwargs_log: list[dict] | None = None):
    def _factory(command, **kwargs):
        commands.append(command)
        if kwargs_log is not None:
            kwargs_log.append(kwargs)
        return _FakeProcess(command)

    return _factory


def _fake_os_kill(pid: int, sig: int) -> None:
    process = _FAKE_PROCESS_REGISTRY[pid]
    process.on_signal_stop()


def test_bridge_controller_start_and_stop_round_trip(monkeypatch):
    commands: list[list[str]] = []
    kwargs_log: list[dict] = []
    monkeypatch.setattr("parakeet.bridge.os.kill", _fake_os_kill)
    controller = DictationBridgeController(
        popen_factory=_fake_popen_factory(commands, kwargs_log),
        transcript_timeout=1.0,
    )

    started = controller.start_session()
    time.sleep(0.05)
    stopped = controller.stop_session()

    assert started["state"] == "recording"
    assert stopped["state"] == "idle"
    assert stopped["last_transcript"]["transcript"] == "fake transcript 1"
    assert commands[0][:5] == [sys.executable, "-u", "-m", "parakeet.cli", "dictation"]
    assert "--format" in commands[0]
    assert "json" in commands[0]
    assert "--bridge-mode" in commands[0]
    assert "--no-clipboard" in commands[0]
    assert kwargs_log[0]["start_new_session"] is True

    controller.shutdown()


def test_bridge_controller_rejects_double_start(monkeypatch):
    monkeypatch.setattr("parakeet.bridge.os.kill", _fake_os_kill)
    controller = DictationBridgeController(
        popen_factory=_fake_popen_factory([]),
        transcript_timeout=1.0,
    )

    controller.start_session()
    try:
        try:
            controller.start_session()
            raise AssertionError("expected BridgeStateError")
        except BridgeStateError as exc:
            assert "recording" in str(exc)
    finally:
        controller.shutdown()


def test_bridge_controller_toggle_cycles_sessions(monkeypatch):
    monkeypatch.setattr("parakeet.bridge.os.kill", _fake_os_kill)
    controller = DictationBridgeController(
        popen_factory=_fake_popen_factory([]),
        transcript_timeout=1.0,
    )

    first = controller.toggle_session()
    time.sleep(0.05)
    second = controller.toggle_session()
    third = controller.toggle_session()

    assert first["state"] == "recording"
    assert second["state"] == "idle"
    assert second["last_transcript"]["transcript"] == "fake transcript 1"
    assert third["state"] == "recording"

    controller.shutdown()


def test_bridge_server_health_and_session_endpoints(monkeypatch):
    monkeypatch.setattr("parakeet.bridge.os.kill", _fake_os_kill)
    controller = DictationBridgeController(
        popen_factory=_fake_popen_factory([]),
        transcript_timeout=1.0,
    )
    server = bridge_module.make_bridge_server("127.0.0.1", 0, controller=controller)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            health = json.load(response)
        assert health["schema_version"] == 1
        assert health["session"]["state"] == "stopped"

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/session/toggle",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            started = json.load(response)
        assert started["state"] == "recording"

        with urllib.request.urlopen(request, timeout=2) as response:
            stopped = json.load(response)
        assert stopped["state"] == "idle"
        assert stopped["last_transcript"]["transcript"] == "fake transcript 1"
    finally:
        server.shutdown()
        server.server_close()
        controller.shutdown()
        thread.join(timeout=1)
