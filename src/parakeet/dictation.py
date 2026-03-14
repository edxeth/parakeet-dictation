#!/usr/bin/env python3
"""Packaged dictation flow for the Parakeet CLI."""

from __future__ import annotations

import argparse
import atexit
import importlib
import io
import logging
import os
import select
import signal
import sys
import tempfile
import threading
import time
import warnings
import wave
from contextlib import nullcontext
from typing import Any, Callable, cast

from parakeet.audio import VAD_FRAME_SAMPLES, build_vad_backend, list_input_devices, record_until_vad_stop
from parakeet.config import resolve_config
from parakeet.errors import (
    AUDIO_BACKEND_UNREACHABLE,
    MODEL_IMPORT_FAILED,
    MODEL_TRANSCRIBE_FAILED,
    AudioError,
    ExitCode,
    ModelError,
)
from parakeet.output import emit_transcription_result
from parakeet.types import DictationConfig, TranscriptionEngine, TranscriptionResult


_shutdown_event = threading.Event()
_bridge_stop_event = threading.Event()
_old_stdout = None
_stderr_fd = None
_devnull_fd = None


def _cleanup_handler() -> None:
    _shutdown_event.set()


atexit.register(_cleanup_handler)


def _signal_handler(signum, frame) -> None:  # pragma: no cover - signal plumbing
    if hasattr(signal, "SIGUSR1") and signum == signal.SIGUSR1:
        _bridge_stop_event.set()
        return
    _shutdown_event.set()


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)
if hasattr(signal, "SIGUSR1"):
    signal.signal(signal.SIGUSR1, _signal_handler)


def _no_kbi_traceback(exc_type, exc, tb) -> None:
    if exc_type is KeyboardInterrupt:
        return
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _no_kbi_traceback


HELP_DESC = "Parakeet TDT 0.6B v3 dictation with GPU/CPU support, clean prompts, and debug diagnostics."
HELP_EPILOG = """Examples:
  parakeet dictation
  parakeet dictation --debug
  parakeet dictation --cpu
  parakeet dictation --list-devices
  parakeet dictation --input-device 2
"""


def add_cli_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        default=None,
        help="Verbose diagnostics: device, timings, GPU memory (logs to file unless --log-file=-)",
    )
    parser.add_argument("--cpu", action="store_true", default=None, help="Force CPU inference")
    parser.add_argument(
        "--vad",
        action="store_true",
        default=None,
        help="Enable VAD-driven auto-stop when supported by the runtime",
    )
    parser.add_argument(
        "--max-silence-ms",
        type=int,
        default=None,
        help="Silence duration required before VAD auto-stop becomes eligible",
    )
    parser.add_argument(
        "--min-speech-ms",
        type=int,
        default=None,
        help="Minimum cumulative voiced duration before VAD stop can trigger",
    )
    parser.add_argument(
        "--vad-mode",
        type=int,
        choices=[0, 1, 2, 3],
        default=None,
        help="WebRTC-VAD aggressiveness",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default=None,
        help="Transcript output format",
    )
    parser.add_argument(
        "--output-file",
        default=None,
        help="Optional transcript output file path",
    )
    parser.add_argument(
        "--clipboard",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable clipboard copy",
    )
    parser.add_argument(
        "--log-file",
        default="transcriber.debug.log",
        help="Debug log file (use '-' for stderr)",
    )
    parser.add_argument(
        "--list-devices", action="store_true", help="List audio input devices and exit"
    )
    parser.add_argument(
        "--input-device",
        type=str,
        default=None,
        help="PyAudio input device index or exact device name",
    )
    parser.add_argument(
        "--bridge-mode",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parakeet dictation",
        description=HELP_DESC,
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    return add_cli_arguments(parser)


def configure_logging(config: DictationConfig, *, status_stream=None) -> None:
    if config.debug:
        if config.log_file != "-":
            try:
                if os.path.exists(config.log_file):
                    os.remove(config.log_file)
            except Exception:
                pass
            logging.basicConfig(
                filename=config.log_file,
                filemode="w",
                level=logging.DEBUG,
                force=True,
            )
            print(f"Debug logs -> {config.log_file}", file=status_stream or sys.stdout)
        else:
            logging.basicConfig(level=logging.DEBUG, force=True)
    else:
        logging.basicConfig(level=logging.WARNING, force=True)


def redirect_library_loggers_to_root_file() -> None:
    names = [
        "",
        "nemo_logger",
        "urllib3",
        "datasets",
        "matplotlib",
        "graphviz",
        "huggingface_hub",
        "transformers",
    ]
    for name in names:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger.propagate = True
        logger.setLevel(logging.DEBUG)


def _silence_start() -> None:
    global _old_stdout, _stderr_fd, _devnull_fd
    _old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    _stderr_fd = os.dup(2)
    _devnull_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(_devnull_fd, 2)


def _silence_stop() -> None:
    global _old_stdout, _stderr_fd, _devnull_fd
    if _old_stdout is not None:
        sys.stdout = _old_stdout
        _old_stdout = None
    if _stderr_fd is not None:
        os.dup2(_stderr_fd, 2)
        os.close(_stderr_fd)
        _stderr_fd = None
    if _devnull_fd is not None:
        os.close(_devnull_fd)
        _devnull_fd = None


class SilentSTDERR:
    def __enter__(self):
        self.old_stderr = os.dup(2)
        self.devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(self.devnull, 2)
        return self

    def __exit__(self, *args):
        os.dup2(self.old_stderr, 2)
        os.close(self.old_stderr)
        os.close(self.devnull)


class _silence_context:
    def __enter__(self):
        _silence_start()
        return self

    def __exit__(self, *exc):
        _silence_stop()
        return False


def _status_stream(config: DictationConfig):
    return sys.stderr if config.format == "json" else sys.stdout


def spinner_animation(stop_event: threading.Event, prefix: str, stream=None) -> None:
    stream = stream or sys.stdout
    chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    idx = 0
    try:
        while not stop_event.is_set() and not _shutdown_event.is_set():
            stream.write(f"\r{prefix} {chars[idx % len(chars)]}")
            stream.flush()
            idx += 1
            time.sleep(0.1)
    finally:
        stream.write("\033[2K\r")
        stream.flush()


def wait_for_enter_interruptible(show_prompt: bool = False, *, stream=None) -> bool:
    prompt_stream = stream or sys.stdout
    if show_prompt:
        prompt_stream.write("Press ENTER to start recording (Ctrl+C to exit)...\n")
        prompt_stream.flush()
    while not _shutdown_event.is_set():
        readable, _, _ = select.select([sys.stdin], [], [], 0.1)
        if readable:
            sys.stdin.readline()
            return True
    return False


def _manual_stop_requested(timeout: float = 0.0) -> bool:
    readable, _, _ = select.select([sys.stdin], [], [], timeout)
    if readable:
        sys.stdin.readline()
        return True
    return False


def save_audio(audio_data: bytes, filename: str, sample_rate: int = 16000) -> None:
    with wave.open(filename, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_data)


def _load_runtime_dependencies(debug: bool):
    if not debug:
        os.environ.setdefault("NEMO_LOG_LEVEL", "ERROR")
        _silence_start()
    try:
        nemo_asr = importlib.import_module("nemo.collections.asr")
        pyaudio = importlib.import_module("pyaudio")
        pyperclip = importlib.import_module("pyperclip")
        torch = importlib.import_module("torch")
    finally:
        if not debug:
            _silence_stop()

    warnings.filterwarnings("ignore")
    return nemo_asr, pyaudio, pyperclip, torch


def list_devices(pyaudio_module: Any) -> int:
    try:
        devices = list_input_devices(pyaudio_module)
        print("Input devices:")
        for device in devices:
            default_marker = " default" if device.is_default_candidate else ""
            print(
                f"- id={device.id} name='{device.name}' rate={device.default_sample_rate}Hz host_api={device.host_api}{default_marker}"
            )
        return int(ExitCode.OK)
    except Exception as exc:
        error = AudioError(AUDIO_BACKEND_UNREACHABLE, str(exc))
        print(f"Error listing devices: {error}")
        return int(ExitCode.ERROR)


def record_audio_interruptible(
    config: DictationConfig,
    pyaudio_module: Any,
    sample_rate: int = 16000,
    *,
    stop_requested: Callable[[], bool] | None = None,
    status_stream=None,
) -> bytes | None:
    status_stream = status_stream or _status_stream(config)
    frames_per_buffer = VAD_FRAME_SAMPLES if config.vad else 1024
    ctx = SilentSTDERR() if not config.debug else nullcontext()
    with ctx:
        pa = pyaudio_module.PyAudio()
        try:
            stream = pa.open(
                format=pyaudio_module.paInt16,
                channels=1,
                rate=sample_rate,
                input=True,
                input_device_index=config.input_device if isinstance(config.input_device, int) else None,
                frames_per_buffer=frames_per_buffer,
            )
        except Exception as exc:
            error = AudioError(AUDIO_BACKEND_UNREACHABLE, str(exc))
            print(f"❌ Audio error: {error}", file=status_stream)
            try:
                pa.terminate()
            except Exception:
                pass
            return None

    audio_data: bytes | None = None
    print("🎤 Recording...", file=status_stream, flush=True)

    if stop_requested is None:
        stop_requested = lambda: _shutdown_event.is_set() or _manual_stop_requested()

    ctx = SilentSTDERR() if not config.debug else nullcontext()
    with ctx:
        try:
            if config.vad:
                try:
                    vad_backend = build_vad_backend(config.vad_mode)
                except Exception as exc:
                    error = AudioError(AUDIO_BACKEND_UNREACHABLE, f"WebRTC VAD unavailable: {exc}")
                    print(f"❌ Audio error: {error}", file=status_stream)
                    return None

                audio_data = record_until_vad_stop(
                    stream,
                    vad=vad_backend,
                    stop_requested=stop_requested,
                    min_speech_ms=config.min_speech_ms,
                    max_silence_ms=config.max_silence_ms,
                    sample_rate=sample_rate,
                    frame_samples=VAD_FRAME_SAMPLES,
                )
            else:
                frames: list[bytes] = []
                while not _shutdown_event.is_set():
                    if stop_requested():
                        break
                    try:
                        data = stream.read(1024, exception_on_overflow=False)
                        frames.append(data)
                    except Exception:
                        continue
                audio_data = b"".join(frames)
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    return audio_data or None


def _load_model(
    config: DictationConfig,
    nemo_asr: Any,
    torch_module: Any,
    *,
    status_stream=None,
) -> tuple[TranscriptionEngine, bool, float, float]:
    stop_spinner = threading.Event()
    spinner_thread = threading.Thread(
        target=spinner_animation,
        args=(stop_spinner, "⏳ Loading model"),
        kwargs={"stream": status_stream or _status_stream(config)},
        daemon=True,
    )
    spinner_thread.start()

    load_ctx = nullcontext() if config.debug else _silence_context()
    start = time.perf_counter()
    try:
        with load_ctx:
            model = nemo_asr.models.ASRModel.from_pretrained("nvidia/parakeet-tdt-0.6b-v3")
    except Exception as exc:
        raise ModelError(MODEL_IMPORT_FAILED, str(exc)) from exc
    finally:
        end = time.perf_counter()
        stop_spinner.set()
        spinner_thread.join()

    use_cuda = torch_module.cuda.is_available() and not config.cpu
    device = "cuda" if use_cuda else "cpu"
    model.to(device)
    model.eval()
    return model, use_cuda, start, end


def _transcribe_once(
    config: DictationConfig,
    model: TranscriptionEngine,
    audio_data: bytes,
    sample_rate: int,
    *,
    status_stream=None,
) -> tuple[TranscriptionResult, str, float, float]:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        save_audio(audio_data, tmp.name, sample_rate=sample_rate)
        temp_path = tmp.name

    stop_spinner = threading.Event()
    spinner_thread = threading.Thread(
        target=spinner_animation,
        args=(stop_spinner, "🤖 Generating..."),
        kwargs={"stream": status_stream or _status_stream(config)},
        daemon=True,
    )
    spinner_thread.start()

    infer_ctx = nullcontext() if config.debug else _silence_context()
    start = time.perf_counter()
    try:
        with infer_ctx:
            result = model.transcribe([temp_path], verbose=False)
        if isinstance(result, list) and result:
            first = result[0]
            transcript_text = getattr(first, "text", first if isinstance(first, str) else str(first))
        else:
            transcript_text = str(result)
        transcription = TranscriptionResult(text=transcript_text)
        return transcription, temp_path, start, time.perf_counter()
    except Exception as exc:
        raise ModelError(MODEL_TRANSCRIBE_FAILED, str(exc)) from exc
    finally:
        stop_spinner.set()
        spinner_thread.join()


def _run_bridge_controlled_dictation(
    config: DictationConfig,
    *,
    pyaudio_module: Any,
    pyperclip_module: Any,
    nemo_asr: Any,
    torch_module: Any,
) -> int:
    status_stream = _status_stream(config)
    _bridge_stop_event.clear()

    try:
        model, _use_cuda, _load_start, _load_end = _load_model(config, nemo_asr, torch_module)
    except ModelError as exc:
        print(f"❌ Error: {exc}", file=status_stream)
        return int(ExitCode.ERROR)

    if _shutdown_event.is_set():
        return int(ExitCode.OK)

    sample_rate = 16000
    audio_data = record_audio_interruptible(
        config,
        pyaudio_module,
        sample_rate=sample_rate,
        stop_requested=lambda: _shutdown_event.is_set() or _bridge_stop_event.is_set(),
    )

    if _shutdown_event.is_set():
        return int(ExitCode.OK)

    if not audio_data:
        emit_transcription_result(
            TranscriptionResult(text=""),
            config,
            pyperclip_module=pyperclip_module,
            status_stream=status_stream,
        )
        return int(ExitCode.OK)

    try:
        transcription, temp_path, _infer_start, _infer_end = _transcribe_once(
            config, model, audio_data, sample_rate
        )
    except ModelError as exc:
        print(f"❌ Error: {exc}", file=status_stream)
        return int(ExitCode.ERROR)

    emit_transcription_result(
        transcription,
        config,
        pyperclip_module=pyperclip_module,
        status_stream=status_stream,
    )

    try:
        os.unlink(temp_path)
    except Exception:
        pass

    return int(ExitCode.OK)


def run_dictation(args: argparse.Namespace) -> int:
    config = resolve_config(args, env=os.environ)
    status_stream = _status_stream(config)
    configure_logging(config, status_stream=status_stream)
    print("Starting...", file=status_stream, flush=True)

    nemo_asr, pyaudio_module, pyperclip_module, torch_module = _load_runtime_dependencies(
        config.debug
    )

    if config.list_devices:
        return list_devices(pyaudio_module)

    if config.debug and config.log_file != "-":
        redirect_library_loggers_to_root_file()

    if bool(getattr(args, "bridge_mode", False)):
        return _run_bridge_controlled_dictation(
            config,
            pyaudio_module=pyaudio_module,
            pyperclip_module=pyperclip_module,
            nemo_asr=nemo_asr,
            torch_module=torch_module,
        )

    try:
        model, use_cuda, load_start, load_end = _load_model(config, nemo_asr, torch_module)
    except ModelError as exc:
        print(f"❌ Error: {exc}", file=status_stream)
        return int(ExitCode.ERROR)

    if _shutdown_event.is_set():
        return int(ExitCode.OK)

    print("🚀 PARAKEET TDT 0.6B V3", file=status_stream)
    if torch_module.cuda.is_available():
        print(f"✅ GPU: {torch_module.cuda.get_device_name(0)}", file=status_stream)
    print("=" * 60, file=status_stream)
    print("📝 Press ENTER to start → Speak → Press ENTER to stop", file=status_stream)
    print("   Ctrl+C to exit", file=status_stream)
    print("=" * 60 + "\n", file=status_stream)

    if config.debug:
        model_parameters = cast(Any, model).parameters()
        print(f"Model device: {next(model_parameters).device}", file=status_stream)
        if use_cuda:
            capability = torch_module.cuda.get_device_capability()
            print(f"CUDA capability: {capability[0]}.{capability[1]}", file=status_stream)
            print(
                f"GPU alloc (MiB) after load: {torch_module.cuda.memory_allocated() / 1024**2:.2f}",
                file=status_stream,
            )
        print(f"Model load time: {load_end - load_start:.3f}s", file=status_stream)

    next_wait_shows_prompt = False

    try:
        while not _shutdown_event.is_set():
            if not wait_for_enter_interruptible(
                show_prompt=next_wait_shows_prompt,
                stream=status_stream,
            ):
                break

            sample_rate = 16000
            record_start = time.perf_counter()
            audio_data = record_audio_interruptible(config, pyaudio_module, sample_rate=sample_rate)
            record_end = time.perf_counter()

            if not audio_data or _shutdown_event.is_set():
                break

            try:
                transcription, temp_path, infer_start, infer_end = _transcribe_once(
                    config, model, audio_data, sample_rate
                )
            except ModelError as exc:
                print(f"❌ Error: {exc}", file=status_stream)
                next_wait_shows_prompt = True
                continue

            if _shutdown_event.is_set():
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
                break

            emit_transcription_result(
                transcription,
                config,
                pyperclip_module=pyperclip_module,
                status_stream=status_stream,
            )

            if config.debug:
                seconds = len(audio_data) / (2 * sample_rate)
                print(
                    f"Audio length: {seconds:.2f}s | Record: {record_end - record_start:.3f}s | Infer: {infer_end - infer_start:.3f}s",
                    file=status_stream,
                )
                if use_cuda:
                    print(
                        f"GPU alloc (MiB) after infer: {torch_module.cuda.memory_allocated() / 1024**2:.2f}",
                        file=status_stream,
                    )

            try:
                os.unlink(temp_path)
            except Exception:
                pass

            next_wait_shows_prompt = True

        return int(ExitCode.OK)
    except KeyboardInterrupt:
        return int(ExitCode.OK)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    namespace = parser.parse_args(sys.argv[1:] if argv is None else argv)
    return run_dictation(namespace)


if __name__ == "__main__":
    raise SystemExit(main())
