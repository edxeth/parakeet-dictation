#!/usr/bin/env python3
"""Parakeet TDT 0.6B v3 - GPU/CPU Transcription with refined prompts and clean Ctrl+C"""

import sys
import os
import io
import argparse
import logging
import time
import warnings
import threading
import wave
import tempfile
import select
from contextlib import nullcontext
import signal
import atexit

# ============ Global shutdown ============
_shutdown_event = threading.Event()


def _cleanup_handler():
    _shutdown_event.set()


atexit.register(_cleanup_handler)


def _signal_handler(signum, frame):
    _shutdown_event.set()


# Install signal handlers early (main thread)
signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# Suppress KeyboardInterrupt traceback
def _no_kbi_traceback(exc_type, exc, tb):
    if exc_type is KeyboardInterrupt:
        return
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _no_kbi_traceback

# ============ CLI ============
HELP_DESC = "Parakeet TDT 0.6B v3 dictation with GPU/CPU support, clean prompts, and debug diagnostics."
HELP_EPILOG = """Examples:
  python transcriber.py
  python transcriber.py --debug
  python transcriber.py --cpu
  python transcriber.py --list-devices
  python transcriber.py --input-device 2
"""

parser = argparse.ArgumentParser(
    description=HELP_DESC,
    epilog=HELP_EPILOG,
    formatter_class=argparse.RawTextHelpFormatter,
)
parser.add_argument(
    "-d",
    "--debug",
    action="store_true",
    help="Verbose diagnostics: device, timings, GPU memory (logs to file unless --log-file=-)",
)
parser.add_argument("--cpu", action="store_true", help="Force CPU inference")
parser.add_argument(
    "--no-clipboard", action="store_true", help="Do not copy transcript to clipboard"
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
    "--input-device", type=int, default=None, help="PyAudio input device index"
)
args = parser.parse_args()


# ============ Logging (truncate on --debug) ============
def configure_logging(debug: bool, log_file: str):
    if debug:
        if log_file != "-":
            try:
                if os.path.exists(log_file):
                    os.remove(log_file)
            except Exception:
                pass
            logging.basicConfig(
                filename=log_file, filemode="w", level=logging.DEBUG, force=True
            )
            print(f"Debug logs -> {log_file}")
        else:
            logging.basicConfig(level=logging.DEBUG, force=True)
    else:
        logging.basicConfig(level=logging.WARNING, force=True)


configure_logging(args.debug, args.log_file)
print("Starting...", flush=True)


def redirect_library_loggers_to_root_file():
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
    for n in names:
        lg = logging.getLogger(n)
        for h in list(lg.handlers):
            lg.removeHandler(h)
        lg.propagate = True
        if args.debug:
            lg.setLevel(logging.DEBUG)


# ============ Fast path: list devices ============
if args.list_devices:
    try:
        import pyaudio

        pa = pyaudio.PyAudio()
        count = pa.get_device_count()
        print("Input devices:")
        for i in range(count):
            info = pa.get_device_info_by_index(i)
            if int(info.get("maxInputChannels", 0)) > 0:
                name = info.get("name", "unknown")
                rate = int(info.get("defaultSampleRate", 0))
                print(f"- id={i} name='{name}' rate={rate}Hz")
        pa.terminate()
    except Exception as e:
        print(f"Error listing devices: {e}")
    sys.exit(0)

# ============ Silencing helpers (normal mode) ============
old_stdout = None
stderr_fd = None
devnull_fd = None


def _silence_start():
    global old_stdout, stderr_fd, devnull_fd
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    stderr_fd = os.dup(2)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull_fd, 2)


def _silence_stop():
    global old_stdout, stderr_fd, devnull_fd
    if old_stdout is not None:
        sys.stdout = old_stdout
        old_stdout = None
    if stderr_fd is not None:
        os.dup2(stderr_fd, 2)
        os.close(stderr_fd)
        stderr_fd = None
    if devnull_fd is not None:
        os.close(devnull_fd)
        devnull_fd = None


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


class SilentSTDOUT:
    def __enter__(self):
        self.old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        return self

    def __exit__(self, *args):
        sys.stdout = self.old_stdout


if not args.debug:
    os.environ.setdefault("NEMO_LOG_LEVEL", "ERROR")
    _silence_start()

# ============ Heavy imports ============
import nemo.collections.asr as nemo_asr
import pyaudio
import pyperclip
import torch

if not args.debug:
    _silence_stop()

warnings.filterwarnings("ignore")


# ============ Spinner ============
def spinner_animation(stop_event, prefix, stream=None):
    stream = stream or sys.__stdout__
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


# ============ Input & audio ============
def wait_for_enter_interruptible(show_prompt=False):
    """Wait for Enter (nonblocking), return False if shutdown was requested."""
    if show_prompt:
        # Single-line prompt (no duplication at startup)
        sys.__stdout__.write("Press ENTER to start recording (Ctrl+C to exit)...\n")
        sys.__stdout__.flush()
    while not _shutdown_event.is_set():
        r, _, _ = select.select([sys.stdin], [], [], 0.1)
        if r:
            sys.stdin.readline()  # consume
            return True
    return False


def record_audio_interruptible(sample_rate=16000, device_index=None):
    """Capture audio until Enter or shutdown."""
    ctx = SilentSTDERR() if not args.debug else nullcontext()
    with ctx:
        p = pyaudio.PyAudio()
        try:
            stream = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=1024,
            )
        except Exception as e:
            print(f"❌ Audio error: {e}")
            try:
                p.terminate()
            except:
                pass
            return None

    frames = []
    # Live status: only "Recording..." during capture
    print("🎤 Recording...", flush=True)

    ctx = SilentSTDERR() if not args.debug else nullcontext()
    with ctx:
        while not _shutdown_event.is_set():
            # Stop on Enter
            r, _, _ = select.select([sys.stdin], [], [], 0.01)
            if r:
                sys.stdin.readline()
                break
            try:
                data = stream.read(1024, exception_on_overflow=False)
                frames.append(data)
            except:
                continue

        stream.stop_stream()
        stream.close()
        p.terminate()

    return b"".join(frames) if frames else None


def save_audio(audio_data, filename, sample_rate=16000):
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data)


class _silence_context:
    def __enter__(self):
        _silence_start()
        return self

    def __exit__(self, *exc):
        _silence_stop()
        return False


# ============ Main ============
def main():
    if args.debug and args.log_file != "-":
        redirect_library_loggers_to_root_file()

    # Spinner: Loading model
    stop_spinner = threading.Event()
    spinner_thread = threading.Thread(
        target=spinner_animation,
        args=(stop_spinner, "⏳ Loading model"),
        kwargs={"stream": sys.__stdout__},
        daemon=True,
    )
    spinner_thread.start()

    # Load model
    load_ctx = nullcontext() if args.debug else _silence_context()
    t_load0 = time.perf_counter()
    try:
        with load_ctx:
            model = nemo_asr.models.ASRModel.from_pretrained(
                "nvidia/parakeet-tdt-0.6b-v3"
            )
    except Exception as e:
        stop_spinner.set()
        spinner_thread.join()
        print(f"❌ Error: {e}")
        return 1
    finally:
        t_load1 = time.perf_counter()
        stop_spinner.set()
        spinner_thread.join()

    if _shutdown_event.is_set():
        return 0

    # Device
    use_cuda = torch.cuda.is_available() and not args.cpu
    device = "cuda" if use_cuda else "cpu"
    model.to(device)
    model.eval()

    # Header
    print("🚀 PARAKEET TDT 0.6B V3")
    if torch.cuda.is_available():
        print(f"✅ GPU: {torch.cuda.get_device_name(0)}")
    print("=" * 60)
    print("📝 Press ENTER to start → Speak → Press ENTER to stop")
    print("   Ctrl+C to exit")
    print("=" * 60 + "\n")

    if args.debug:
        print(f"Model device: {next(model.parameters()).device}")
        if use_cuda:
            cap = torch.cuda.get_device_capability()
            print(f"CUDA capability: {cap[0]}.{cap[1]}")
            print(
                f"GPU alloc (MiB) after load: {torch.cuda.memory_allocated() / 1024**2:.2f}"
            )
        print(f"Model load time: {t_load1 - t_load0:.3f}s")

    # First wait: no redundant prompt (header already explains)
    next_wait_shows_prompt = False

    try:
        while not _shutdown_event.is_set():
            if not wait_for_enter_interruptible(show_prompt=next_wait_shows_prompt):
                break

            sr = 16000
            t_rec0 = time.perf_counter()
            audio_data = record_audio_interruptible(
                sample_rate=sr, device_index=args.input_device
            )
            t_rec1 = time.perf_counter()

            if not audio_data or _shutdown_event.is_set():
                break

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                save_audio(audio_data, tmp.name, sample_rate=sr)
                temp_path = tmp.name

            # Spinner: Generating...
            gen_stop = threading.Event()
            gen_spinner = threading.Thread(
                target=spinner_animation,
                args=(gen_stop, "🤖 Generating..."),
                kwargs={"stream": sys.__stdout__},
                daemon=True,
            )
            gen_spinner.start()

            infer_ctx = nullcontext() if args.debug else _silence_context()
            t_inf0 = time.perf_counter()
            try:
                with infer_ctx:
                    result = model.transcribe([temp_path], verbose=False)
                if isinstance(result, list) and len(result) > 0:
                    first = result[0]
                    transcript = getattr(
                        first, "text", first if isinstance(first, str) else str(first)
                    )
                else:
                    transcript = str(result)
            except Exception as e:
                gen_stop.set()
                gen_spinner.join()
                print(f"❌ Error: {e}")
                try:
                    os.unlink(temp_path)
                except:
                    pass
                # After an error, prompt before next try
                next_wait_shows_prompt = True
                continue
            finally:
                t_inf1 = time.perf_counter()
                gen_stop.set()
                gen_spinner.join()

            if _shutdown_event.is_set():
                try:
                    os.unlink(temp_path)
                except:
                    pass
                break

            print(f"📝 {transcript}\n")

            if not args.no_clipboard:
                try:
                    pyperclip.copy(transcript)
                except Exception as e:
                    if args.debug:
                        print(f"Clipboard warning: {e}")

            if args.debug:
                secs = len(audio_data) / (2 * sr)
                print(
                    f"Audio length: {secs:.2f}s | Record: {t_rec1 - t_rec0:.3f}s | Infer: {t_inf1 - t_inf0:.3f}s"
                )
                if use_cuda:
                    print(
                        f"GPU alloc (MiB) after infer: {torch.cuda.memory_allocated() / 1024**2:.2f}"
                    )

            try:
                os.unlink(temp_path)
            except:
                pass

            # After any transcript, show the concise next-start prompt
            next_wait_shows_prompt = True

        return 0

    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    try:
        code = main()
        print("\n👋 Goodbye!", flush=True)
        sys.exit(code)
    except KeyboardInterrupt:
        print("\n👋 Goodbye!", flush=True)
        sys.exit(130)
