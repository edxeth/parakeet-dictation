from __future__ import annotations

import importlib
from itertools import repeat
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


audio_module = importlib.import_module("parakeet.audio")
dictation_module = importlib.import_module("parakeet.dictation")
DictationConfig = importlib.import_module("parakeet.types").DictationConfig

VAD_FRAME_BYTES = audio_module.VAD_FRAME_BYTES
VAD_FRAME_SAMPLES = audio_module.VAD_FRAME_SAMPLES
record_until_vad_stop = audio_module.record_until_vad_stop


class _FakeVadBackend:
    name = "webrtc"

    def __init__(self, speech_frames: set[bytes]):
        self.speech_frames = speech_frames

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        assert sample_rate == 16000
        return frame in self.speech_frames


class _FakeStream:
    def __init__(self, frames: list[bytes]):
        self._frames = frames
        self._index = 0
        self.stop_called = False
        self.close_called = False

    def read(self, frame_samples: int, exception_on_overflow: bool = False) -> bytes:
        assert frame_samples == VAD_FRAME_SAMPLES
        if self._index >= len(self._frames):
            return self._frames[-1]
        frame = self._frames[self._index]
        self._index += 1
        return frame

    def stop_stream(self) -> None:
        self.stop_called = True

    def close(self) -> None:
        self.close_called = True


class _ManualStopAfter:
    def __init__(self, allowed_reads: int):
        self.allowed_reads = allowed_reads
        self.calls = 0

    def __call__(self) -> bool:
        current = self.calls
        self.calls += 1
        return current >= self.allowed_reads


class _FakePyAudioModule:
    paInt16 = object()

    def __init__(self, stream: _FakeStream):
        self._stream = stream
        self.open_kwargs: dict[str, object] | None = None
        self.terminated = False

    def PyAudio(self):
        return self

    def open(self, **kwargs):
        self.open_kwargs = kwargs
        return self._stream

    def terminate(self) -> None:
        self.terminated = True


SPEECH_FRAME = b"\x01\x00" * (VAD_FRAME_BYTES // 2)
SILENCE_FRAME = b"\x00\x00" * (VAD_FRAME_BYTES // 2)


def test_vad_auto_stop_only_after_min_speech_and_silence_window():
    stream = _FakeStream(
        [
            SPEECH_FRAME,
            SPEECH_FRAME,
            SPEECH_FRAME,
            SILENCE_FRAME,
            SILENCE_FRAME,
            SPEECH_FRAME,
        ]
    )

    audio_data = record_until_vad_stop(
        stream,
        vad=_FakeVadBackend({SPEECH_FRAME}),
        stop_requested=lambda: False,
        min_speech_ms=90,
        max_silence_ms=60,
    )

    assert audio_data == b"".join(
        [
            SPEECH_FRAME,
            SPEECH_FRAME,
            SPEECH_FRAME,
            SILENCE_FRAME,
            SILENCE_FRAME,
        ]
    )


def test_vad_no_speech_session_stays_manual_stop_only():
    stream = _FakeStream(list(repeat(SILENCE_FRAME, 6)))

    audio_data = record_until_vad_stop(
        stream,
        vad=_FakeVadBackend({SPEECH_FRAME}),
        stop_requested=_ManualStopAfter(4),
        min_speech_ms=90,
        max_silence_ms=60,
    )

    assert audio_data == b"".join([SILENCE_FRAME] * 4)


def test_vad_manual_stop_remains_available_even_with_voiced_audio():
    stream = _FakeStream(list(repeat(SPEECH_FRAME, 6)))

    audio_data = record_until_vad_stop(
        stream,
        vad=_FakeVadBackend({SPEECH_FRAME}),
        stop_requested=_ManualStopAfter(2),
        min_speech_ms=90,
        max_silence_ms=60,
    )

    assert audio_data == b"".join([SPEECH_FRAME] * 2)


def test_dictation_recording_uses_vad_backend_when_enabled(monkeypatch):
    dictation_module._shutdown_event.clear()
    stream = _FakeStream([SILENCE_FRAME])
    pyaudio_module = _FakePyAudioModule(stream)
    config = DictationConfig(vad=True, clipboard=False)
    backend = object()
    calls: dict[str, object] = {}

    monkeypatch.setattr("parakeet.dictation.build_vad_backend", lambda mode: backend)

    def _fake_record_until_vad_stop(stream_arg, **kwargs):
        calls["stream"] = stream_arg
        calls.update(kwargs)
        return b"vad-audio"

    monkeypatch.setattr("parakeet.dictation.record_until_vad_stop", _fake_record_until_vad_stop)

    audio_data = dictation_module.record_audio_interruptible(config, pyaudio_module)

    assert audio_data == b"vad-audio"
    assert pyaudio_module.open_kwargs is not None
    assert pyaudio_module.open_kwargs["frames_per_buffer"] == VAD_FRAME_SAMPLES
    assert calls["stream"] is stream
    assert calls["vad"] is backend
    assert calls["min_speech_ms"] == config.min_speech_ms
    assert calls["max_silence_ms"] == config.max_silence_ms
    assert stream.stop_called is True
    assert stream.close_called is True
    assert pyaudio_module.terminated is True
