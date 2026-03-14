from __future__ import annotations

import importlib
import json
from pathlib import Path
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
TranscriptionResult = importlib.import_module("parakeet.types").TranscriptionResult


def _runtime_loader(debug: bool):
    return object(), object(), object(), object()


def _make_model_loader(delay: float = 0.0, calls: list[int] | None = None):
    def _loader(config, nemo_asr, torch_module, *, status_stream=None):
        if calls is not None:
            calls.append(1)
        if status_stream is not None:
            status_stream.write("⏳ Loading model\n")
        if delay:
            time.sleep(delay)
        return object(), False, 0.0, 0.0

    return _loader


def _recorder(config, pyaudio_module, sample_rate=16000, *, stop_requested=None, status_stream=None):
    if status_stream is not None:
        status_stream.write("🎤 Recording...\n")
    deadline = time.time() + 2.0
    while stop_requested is not None and not stop_requested():
        if time.time() >= deadline:
            break
        time.sleep(0.01)
    return b"audio"


def _transcriber(config, model, audio_data, sample_rate, *, status_stream=None):
    if status_stream is not None:
        status_stream.write("🤖 Generating...\n")
    return TranscriptionResult(text="fake transcript", device="cpu"), "/tmp/fake.wav", 0.0, 0.0


def test_bridge_start_rejects_double_start():
    controller = DictationBridgeController(
        runtime_loader=_runtime_loader,
        model_loader=_make_model_loader(),
        recorder=_recorder,
        transcriber=_transcriber,
        transcript_timeout=1.0,
    )

    started = controller.start_session()
    assert started["state"] == "starting"

    try:
        controller.start_session()
        raise AssertionError("expected BridgeStateError")
    except BridgeStateError as exc:
        assert "starting" in str(exc)
    finally:
        controller.shutdown()


def test_bridge_stop_during_model_load_cancels_cleanly():
    controller = DictationBridgeController(
        runtime_loader=_runtime_loader,
        model_loader=_make_model_loader(delay=0.15),
        recorder=_recorder,
        transcriber=_transcriber,
        transcript_timeout=1.0,
    )

    controller.start_session()
    stopped = controller.stop_session()

    assert stopped["state"] == "idle"
    assert stopped["last_transcript"]["transcript"] == ""
    assert stopped["last_transcript"]["metadata"]["cancelled_before_recording"] is True

    controller.shutdown()


def test_bridge_reuses_loaded_model_across_sessions():
    model_calls: list[int] = []
    controller = DictationBridgeController(
        runtime_loader=_runtime_loader,
        model_loader=_make_model_loader(calls=model_calls),
        recorder=_recorder,
        transcriber=_transcriber,
        transcript_timeout=1.0,
    )

    controller.start_session()
    time.sleep(0.05)
    first = controller.stop_session()
    controller.start_session()
    time.sleep(0.05)
    second = controller.stop_session()

    assert first["last_transcript"]["transcript"] == "fake transcript"
    assert second["last_transcript"]["transcript"] == "fake transcript"
    assert len(model_calls) == 1
    assert second["model_loaded"] is True
    assert len(second["history"]) == 2

    cleared = controller.clear_history()
    assert cleared["history"] == []
    assert cleared["last_transcript"] is None

    controller.shutdown()


def test_bridge_server_health_and_session_endpoints():
    controller = DictationBridgeController(
        runtime_loader=_runtime_loader,
        model_loader=_make_model_loader(),
        recorder=_recorder,
        transcriber=_transcriber,
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
        assert health["bridge"]["model_loaded"] is False

        start_request = urllib.request.Request(
            f"http://127.0.0.1:{port}/session/start",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(start_request, timeout=2) as response:
            started = json.load(response)
        assert started["state"] == "starting"

        time.sleep(0.05)
        stop_request = urllib.request.Request(
            f"http://127.0.0.1:{port}/session/stop",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(stop_request, timeout=3) as response:
            stopped = json.load(response)
        assert stopped["state"] == "idle"
        assert stopped["last_transcript"]["transcript"] == "fake transcript"
    finally:
        server.shutdown()
        server.server_close()
        controller.shutdown()
        thread.join(timeout=1)
