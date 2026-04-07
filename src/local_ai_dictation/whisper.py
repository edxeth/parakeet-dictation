"""Whisper backend helpers for local dictation."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from local_ai_dictation.errors import MODEL_IMPORT_FAILED, MODEL_TRANSCRIBE_FAILED, ModelError
from local_ai_dictation.types import DictationConfig, TranscriptionResult


WHISPER_MODEL_ID = "deepdml/faster-distil-whisper-large-v3.5"


class _DummyParameter:
    def __init__(self, device: str) -> None:
        self.device = device


class _TranscriptItem:
    def __init__(self, text: str) -> None:
        self.text = text


class WhisperEngine:
    def __init__(self, model: Any, *, device: str, compute_type: str, model_id: str) -> None:
        self._model = model
        self._parakeet_device = device
        self._parakeet_compute_type = compute_type
        self._parakeet_model_id = model_id

    def to(self, device: str) -> Any:
        self._parakeet_device = device
        return self

    def eval(self) -> Any:
        return self

    def parameters(self):
        yield _DummyParameter(self._parakeet_device)

    def transcribe(self, audio: Sequence[str], *, verbose: bool = False) -> list[Any]:
        if not audio:
            return [_TranscriptItem("")]
        try:
            segments, _info = self._model.transcribe(
                str(audio[0]),
                language="en",
                condition_on_previous_text=False,
                vad_filter=True,
                beam_size=5,
            )
            text = "".join(segment.text for segment in segments).strip()
        except Exception as exc:
            raise ModelError(MODEL_TRANSCRIBE_FAILED, str(exc)) from exc
        return [_TranscriptItem(text)]


def _load_runtime_dependencies() -> tuple[Any, Any]:
    try:
        faster_whisper = importlib.import_module("faster_whisper")
        torch_module = importlib.import_module("torch")
    except Exception as exc:
        raise ModelError(MODEL_IMPORT_FAILED, str(exc)) from exc
    return faster_whisper, torch_module


def _compute_type(config: DictationConfig, torch_module: Any) -> tuple[str, str]:
    use_cuda = bool(getattr(torch_module.cuda, "is_available", lambda: False)()) and not config.cpu
    if use_cuda:
        return "cuda", "float16"
    return "cpu", "int8"


def load_engine(config: DictationConfig) -> WhisperEngine:
    faster_whisper, torch_module = _load_runtime_dependencies()
    device, compute_type = _compute_type(config, torch_module)

    try:
        model = faster_whisper.WhisperModel(WHISPER_MODEL_ID, device=device, compute_type=compute_type)
    except Exception as exc:
        raise ModelError(MODEL_IMPORT_FAILED, str(exc)) from exc

    return WhisperEngine(model, device=device, compute_type=compute_type, model_id=WHISPER_MODEL_ID)


def warmup(engine: WhisperEngine) -> None:
    engine.eval()


def transcribe_wav(engine: WhisperEngine, path: str | Path) -> TranscriptionResult:
    result = engine.transcribe([str(path)], verbose=False)
    first = result[0] if result else _TranscriptItem("")
    transcript_text = getattr(first, "text", first if isinstance(first, str) else str(first))
    return TranscriptionResult(
        text=transcript_text,
        device=getattr(engine, "_parakeet_device", None),
        metadata={
            "backend": "whisper",
            "compute_type": getattr(engine, "_parakeet_compute_type", None),
            "model_id": getattr(engine, "_parakeet_model_id", WHISPER_MODEL_ID),
        },
    )


def check_model_cache(_env: Mapping[str, str] | None = None) -> dict[str, Any]:
    try:
        _load_runtime_dependencies()
        import_ready = True
        import_error = None
    except ModelError as exc:
        import_ready = False
        import_error = str(exc)

    detail = "Whisper runtime imports look ready" if import_ready else "Whisper runtime imports failed"
    return {
        "checked": True,
        "cache_present": None,
        "cache_path": None,
        "model_id": WHISPER_MODEL_ID,
        "import_ready": import_ready,
        "import_error": import_error,
        "detail": detail,
    }
