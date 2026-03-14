"""Top-level CLI entry point for the packaged Parakeet app."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
from typing import Sequence

from parakeet.audio import list_input_devices
from parakeet.dictation import add_cli_arguments
from parakeet.doctor import collect_doctor_report, doctor_exit_code, render_doctor_text


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
