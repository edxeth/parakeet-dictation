"""Top-level CLI entry point for the packaged Parakeet app."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
from typing import Sequence

from parakeet.audio import list_input_devices
from parakeet.benchmark import run_benchmark_command
from parakeet.bridge import run_bridge_server
from parakeet.dictation import add_cli_arguments
from parakeet.doctor import collect_doctor_report, doctor_exit_code, render_doctor_text


def add_bridge_cli_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind the bridge to.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="TCP port to bind the bridge to.",
    )
    parser.add_argument(
        "--cpu",
        action="store_true",
        help="Force CPU inference for bridge-controlled dictation sessions.",
    )
    parser.add_argument(
        "--input-device",
        type=str,
        default=None,
        help="PyAudio input device index or exact device name.",
    )
    parser.add_argument(
        "--vad",
        action="store_true",
        help="Enable VAD-driven auto-stop for bridge-controlled sessions.",
    )
    parser.add_argument(
        "--max-silence-ms",
        type=int,
        default=1200,
        help="Silence duration required before VAD auto-stop becomes eligible.",
    )
    parser.add_argument(
        "--min-speech-ms",
        type=int,
        default=300,
        help="Minimum cumulative voiced duration before VAD stop can trigger.",
    )
    parser.add_argument(
        "--vad-mode",
        type=int,
        choices=[0, 1, 2, 3],
        default=2,
        help="WebRTC-VAD aggressiveness for bridge-controlled sessions.",
    )
    parser.add_argument(
        "--clipboard",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable or disable clipboard copy for bridge-controlled dictation sessions.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging for the bridge-controlled dictation subprocess.",
    )
    parser.add_argument(
        "--log-file",
        default="transcriber.debug.log",
        help="Debug log file used by the dictation subprocess.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parakeet",
        description="Packaged Parakeet dictation CLI.",
    )
    subparsers = parser.add_subparsers(dest="command")

    dictation_parser = subparsers.add_parser(
        "dictation",
        help="Run one interactive dictation session.",
        description="Run one interactive dictation session.",
    )
    add_cli_arguments(dictation_parser)
    dictation_parser.set_defaults(handler=_run_dictation_namespace)

    devices_parser = subparsers.add_parser(
        "devices",
        help="List available input devices.",
        description="List available input devices.",
    )
    devices_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON.",
    )
    devices_parser.set_defaults(handler=_run_devices_namespace)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Diagnose environment readiness for dictation.",
        description="Diagnose environment readiness for dictation.",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON.",
    )
    doctor_parser.add_argument(
        "--check-model-cache",
        action="store_true",
        help="Check local Parakeet cache/import readiness without loading or downloading the model.",
    )
    doctor_parser.set_defaults(handler=_run_doctor_namespace)

    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="Benchmark prerecorded WAV fixtures deterministically.",
        description="Benchmark prerecorded WAV fixtures deterministically.",
    )
    benchmark_parser.add_argument(
        "--fixture",
        required=True,
        help="Local WAV fixture path.",
    )
    benchmark_parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="Number of warm transcription runs to execute.",
    )
    benchmark_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON.",
    )
    benchmark_parser.add_argument(
        "--cpu",
        action="store_true",
        help="Force CPU inference.",
    )
    benchmark_parser.add_argument(
        "--check-expected",
        action="store_true",
        help="Require and compare the expected transcript sidecar.",
    )
    benchmark_parser.set_defaults(handler=_run_benchmark_namespace)

    bridge_parser = subparsers.add_parser(
        "bridge",
        help="Run an opt-in localhost control bridge for a desktop app.",
        description="Run an opt-in localhost control bridge for a desktop app.",
    )
    add_bridge_cli_arguments(bridge_parser)
    bridge_parser.set_defaults(handler=_run_bridge_namespace)

    bridge_toggle_parser = subparsers.add_parser(
        "bridge-toggle",
        help="Toggle the current bridge recording session over localhost.",
        description="Toggle the current bridge recording session over localhost.",
    )
    bridge_toggle_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bridge host the toggle command should connect to.",
    )
    bridge_toggle_parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Bridge port the toggle command should connect to.",
    )
    bridge_toggle_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON.",
    )
    bridge_toggle_parser.set_defaults(handler=_run_bridge_toggle_namespace)

    gui_parser = subparsers.add_parser(
        "gui",
        help="Run the Electrobun desktop app on the current platform.",
        description="Run the Electrobun desktop app on the current platform.",
    )
    gui_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bridge host the desktop app should connect to.",
    )
    gui_parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Bridge port the desktop app should connect to.",
    )
    gui_parser.add_argument(
        "--hotkey",
        default=None,
        help="Optional global hotkey override for the desktop app.",
    )
    gui_parser.add_argument(
        "--bridge-command",
        default=None,
        help="Optional command string shown in the UI for starting the bridge.",
    )
    gui_parser.set_defaults(handler=_run_gui_namespace)

    gui_stage_parser = subparsers.add_parser(
        "gui-stage",
        help="Stage the Electrobun desktop app to a drive-backed Windows path.",
        description="Stage the Electrobun desktop app to a drive-backed Windows path.",
    )
    gui_stage_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON.",
    )
    gui_stage_parser.set_defaults(handler=_run_gui_stage_namespace)

    gui_package_parser = subparsers.add_parser(
        "gui-package",
        help="Build the staged Electrobun desktop app as a Windows package.",
        description="Build the staged Electrobun desktop app as a Windows package.",
    )
    gui_package_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON.",
    )
    gui_package_parser.set_defaults(handler=_run_gui_package_namespace)

    gui_package_smoke_parser = subparsers.add_parser(
        "gui-package-smoke",
        help="Package the Windows app, launch it unattended, and capture startup diagnostics.",
        description="Package the Windows app, launch it unattended, and capture startup diagnostics.",
    )
    gui_package_smoke_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON.",
    )
    gui_package_smoke_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="Maximum seconds to wait for the packaged Windows launcher to exit.",
    )
    gui_package_smoke_parser.add_argument(
        "--auto-exit-ms",
        type=int,
        default=1500,
        help="Milliseconds to wait after renderer readiness before the packaged app exits in E2E mode.",
    )
    gui_package_smoke_parser.set_defaults(handler=_run_gui_package_smoke_namespace)

    gui_package_automation_parser = subparsers.add_parser(
        "gui-package-automation",
        help="Package the Windows app and verify the localhost E2E automation surface.",
        description="Package the Windows app and verify the localhost E2E automation surface.",
    )
    gui_package_automation_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON.",
    )
    gui_package_automation_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="Maximum seconds to wait for the packaged Windows launcher to exit.",
    )
    gui_package_automation_parser.add_argument(
        "--automation-port",
        type=int,
        default=0,
        help="Optional localhost port for the packaged GUI automation server.",
    )
    gui_package_automation_parser.set_defaults(handler=_run_gui_package_automation_namespace)

    gui_package_bridge_recovery_parser = subparsers.add_parser(
        "gui-package-bridge-recovery",
        help="Package the Windows app and verify offline-to-online bridge recovery.",
        description="Package the Windows app and verify offline-to-online bridge recovery.",
    )
    gui_package_bridge_recovery_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON.",
    )
    gui_package_bridge_recovery_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="Maximum seconds to wait for the packaged Windows verification flow.",
    )
    gui_package_bridge_recovery_parser.add_argument(
        "--automation-port",
        type=int,
        default=0,
        help="Optional localhost port for the packaged GUI automation server.",
    )
    gui_package_bridge_recovery_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface the deterministic WSL bridge should bind to during verification.",
    )
    gui_package_bridge_recovery_parser.add_argument(
        "--bridge-port",
        type=int,
        default=0,
        help="Optional localhost port for the deterministic WSL bridge during verification.",
    )
    gui_package_bridge_recovery_parser.set_defaults(handler=_run_gui_package_bridge_recovery_namespace)

    gui_package_main_window_parser = subparsers.add_parser(
        "gui-package-main-window",
        help="Package the Windows app and verify main-window start/stop against the deterministic WSL bridge.",
        description="Package the Windows app and verify main-window start/stop against the deterministic WSL bridge.",
    )
    gui_package_main_window_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON.",
    )
    gui_package_main_window_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="Maximum seconds to wait for the packaged Windows verification flow.",
    )
    gui_package_main_window_parser.add_argument(
        "--automation-port",
        type=int,
        default=0,
        help="Optional localhost port for the packaged GUI automation server.",
    )
    gui_package_main_window_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface the deterministic WSL bridge should bind to during verification.",
    )
    gui_package_main_window_parser.add_argument(
        "--bridge-port",
        type=int,
        default=0,
        help="Optional localhost port for the deterministic WSL bridge during verification.",
    )
    gui_package_main_window_parser.set_defaults(handler=_run_gui_package_main_window_namespace)

    gui_package_tray_parser = subparsers.add_parser(
        "gui-package-tray",
        help="Package the Windows app and verify tray open/toggle/quit against the deterministic WSL bridge.",
        description="Package the Windows app and verify tray open/toggle/quit against the deterministic WSL bridge.",
    )
    gui_package_tray_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON.",
    )
    gui_package_tray_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="Maximum seconds to wait for the packaged Windows verification flow.",
    )
    gui_package_tray_parser.add_argument(
        "--automation-port",
        type=int,
        default=0,
        help="Optional localhost port for the packaged GUI automation server.",
    )
    gui_package_tray_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface the deterministic WSL bridge should bind to during verification.",
    )
    gui_package_tray_parser.add_argument(
        "--bridge-port",
        type=int,
        default=0,
        help="Optional localhost port for the deterministic WSL bridge during verification.",
    )
    gui_package_tray_parser.set_defaults(handler=_run_gui_package_tray_namespace)

    gui_package_hotkey_parser = subparsers.add_parser(
        "gui-package-hotkey",
        help="Package the Windows app and verify hotkey registration plus end-to-end toggling against the deterministic WSL bridge.",
        description="Package the Windows app and verify hotkey registration plus end-to-end toggling against the deterministic WSL bridge.",
    )
    gui_package_hotkey_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON.",
    )
    gui_package_hotkey_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="Maximum seconds to wait for the packaged Windows verification flow.",
    )
    gui_package_hotkey_parser.add_argument(
        "--automation-port",
        type=int,
        default=0,
        help="Optional localhost port for the packaged GUI automation server.",
    )
    gui_package_hotkey_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface the deterministic WSL bridge should bind to during verification.",
    )
    gui_package_hotkey_parser.add_argument(
        "--bridge-port",
        type=int,
        default=0,
        help="Optional localhost port for the deterministic WSL bridge during verification.",
    )
    gui_package_hotkey_parser.set_defaults(handler=_run_gui_package_hotkey_namespace)

    gui_package_verify_parser = subparsers.add_parser(
        "gui-package-verify",
        help="Run the unattended packaged Windows verification suite from WSL.",
        description="Run the unattended packaged Windows verification suite from WSL.",
    )
    gui_package_verify_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON.",
    )
    gui_package_verify_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=240.0,
        help="Maximum seconds to wait for each packaged Windows verification step.",
    )
    gui_package_verify_parser.set_defaults(handler=_run_gui_package_verify_namespace)

    return parser


def _run_dictation_namespace(namespace: argparse.Namespace) -> int:
    from parakeet.dictation import run_dictation

    return run_dictation(namespace)


def _run_devices_namespace(namespace: argparse.Namespace) -> int:
    devices = list_input_devices()
    payload = {
        "schema_version": 1,
        "devices": [asdict(device) for device in devices],
    }

    if namespace.json_output:
        print(json.dumps(payload))
        return 0

    print("Input devices:")
    for device in devices:
        default_marker = " default" if device.is_default_candidate else ""
        print(
            f"- id={device.id} name='{device.name}' rate={device.default_sample_rate}Hz host_api={device.host_api}{default_marker}"
        )
    return 0


def _run_doctor_namespace(namespace: argparse.Namespace) -> int:
    report = collect_doctor_report(check_model_cache=bool(namespace.check_model_cache))

    if namespace.json_output:
        print(json.dumps(asdict(report)))
    else:
        print(render_doctor_text(report))

    return doctor_exit_code(report)


def _run_benchmark_namespace(namespace: argparse.Namespace) -> int:
    return run_benchmark_command(
        namespace.fixture,
        runs=namespace.runs,
        cpu=bool(namespace.cpu),
        json_output=bool(namespace.json_output),
        check_expected=bool(namespace.check_expected),
    )


def _run_bridge_namespace(namespace: argparse.Namespace) -> int:
    return run_bridge_server(namespace)


def _run_gui_namespace(namespace: argparse.Namespace) -> int:
    from parakeet.desktop import run_gui_command

    return run_gui_command(namespace)


def _run_bridge_toggle_namespace(namespace: argparse.Namespace) -> int:
    from parakeet.desktop import run_bridge_toggle_command

    return run_bridge_toggle_command(namespace)


def _run_gui_stage_namespace(namespace: argparse.Namespace) -> int:
    from parakeet.desktop import run_gui_stage_command

    return run_gui_stage_command(namespace)


def _run_gui_package_namespace(namespace: argparse.Namespace) -> int:
    from parakeet.desktop import run_gui_package_command

    return run_gui_package_command(namespace)


def _run_gui_package_smoke_namespace(namespace: argparse.Namespace) -> int:
    from parakeet.desktop import run_gui_package_smoke_command

    return run_gui_package_smoke_command(namespace)


def _run_gui_package_automation_namespace(namespace: argparse.Namespace) -> int:
    from parakeet.desktop import run_gui_package_automation_command

    return run_gui_package_automation_command(namespace)


def _run_gui_package_bridge_recovery_namespace(namespace: argparse.Namespace) -> int:
    from parakeet.desktop import run_gui_package_bridge_recovery_command

    return run_gui_package_bridge_recovery_command(namespace)


def _run_gui_package_main_window_namespace(namespace: argparse.Namespace) -> int:
    from parakeet.desktop import run_gui_package_main_window_command

    return run_gui_package_main_window_command(namespace)


def _run_gui_package_tray_namespace(namespace: argparse.Namespace) -> int:
    from parakeet.desktop import run_gui_package_tray_command

    return run_gui_package_tray_command(namespace)


def _run_gui_package_hotkey_namespace(namespace: argparse.Namespace) -> int:
    from parakeet.desktop import run_gui_package_hotkey_command

    return run_gui_package_hotkey_command(namespace)


def _run_gui_package_verify_namespace(namespace: argparse.Namespace) -> int:
    from parakeet.desktop import run_gui_package_verify_command

    return run_gui_package_verify_command(namespace)


def run_dictation_argv(argv: Sequence[str] | None = None) -> int:
    from parakeet.dictation import build_parser, run_dictation

    parser = build_parser()
    actual_argv = sys.argv[1:] if argv is None else list(argv)
    namespace = parser.parse_args(actual_argv)
    return run_dictation(namespace)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        parser = build_parser()
        parser.print_help()
        return 0

    if argv[0].startswith("-") and argv[0] not in {"-h", "--help"}:
        return run_dictation_argv(argv)

    parser = build_parser()
    namespace = parser.parse_args(argv)
    handler = getattr(namespace, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(namespace)


if __name__ == "__main__":
    raise SystemExit(main())
