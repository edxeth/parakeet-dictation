from __future__ import annotations

import importlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


whisper_module = importlib.import_module("local_ai_dictation.whisper")
WhisperEngine = whisper_module.WhisperEngine


class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def transcribe(self, audio_path: str, **kwargs):
        self.calls.append({"audio_path": audio_path, **kwargs})
        return [_FakeSegment(" hello")], object()



def test_whisper_engine_enables_vad_filter_for_transcription():
    model = _FakeModel()
    engine = WhisperEngine(model, device="cpu", compute_type="int8", model_id="fake")

    result = engine.transcribe(["/tmp/sample.wav"])

    assert len(result) == 1
    assert result[0].text == "hello"
    assert model.calls == [
        {
            "audio_path": "/tmp/sample.wav",
            "language": "en",
            "condition_on_previous_text": False,
            "vad_filter": True,
            "beam_size": 5,
        }
    ]
