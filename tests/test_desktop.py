from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from parakeet.cli import main
import parakeet.desktop as desktop


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakePopen:
    def __init__(self, command, **kwargs):
        self.command = command
        self.cwd = kwargs.get("cwd")
        self.env = kwargs.get("env")
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_timeouts: list[float | None] = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        self.returncode = 0

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9

    def communicate(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.returncode is None:
            self.returncode = 0
        return "", ""


class _PopenFactory:
    def __init__(self):
        self.calls: list[_FakePopen] = []

    def __call__(self, command, **kwargs):
        process = _FakePopen(command, **kwargs)
        self.calls.append(process)
        return process


def test_gui_subcommand_dispatches_to_desktop_launcher(monkeypatch):
    calls: list[SimpleNamespace] = []

    def _fake_run_gui(namespace):
        calls.append(namespace)
        return 0

    monkeypatch.setattr("parakeet.desktop.run_gui_command", _fake_run_gui)

    assert main(["gui"]) == 0
    assert len(calls) == 1
    assert calls[0].command == "gui"
    assert calls[0].bridge is False
    assert calls[0].host == "127.0.0.1"
    assert calls[0].port == 8765


def test_gui_stage_subcommand_dispatches_to_desktop_stager(monkeypatch):
    calls: list[SimpleNamespace] = []

    def _fake_stage(namespace):
        calls.append(namespace)
        return 0

    monkeypatch.setattr("parakeet.desktop.run_gui_stage_command", _fake_stage)

    assert main(["gui-stage", "--json"]) == 0
    assert len(calls) == 1
    assert calls[0].command == "gui-stage"
    assert calls[0].json_output is True


def test_gui_package_subcommand_dispatches_to_desktop_packager(monkeypatch):
    calls: list[SimpleNamespace] = []

    def _fake_package(namespace):
        calls.append(namespace)
        return 0

    monkeypatch.setattr("parakeet.desktop.run_gui_package_command", _fake_package)

    assert main(["gui-package", "--json"]) == 0
    assert len(calls) == 1
    assert calls[0].command == "gui-package"
    assert calls[0].json_output is True


def test_gui_package_smoke_subcommand_dispatches_to_desktop_smoke_runner(monkeypatch):
    calls: list[SimpleNamespace] = []

    def _fake_smoke(namespace):
        calls.append(namespace)
        return 0

    monkeypatch.setattr("parakeet.desktop.run_gui_package_smoke_command", _fake_smoke)

    assert main(["gui-package-smoke", "--json", "--timeout-seconds", "12", "--auto-exit-ms", "900"]) == 0
    assert len(calls) == 1
    assert calls[0].command == "gui-package-smoke"
    assert calls[0].json_output is True
    assert calls[0].timeout_seconds == 12
    assert calls[0].auto_exit_ms == 900


def test_gui_package_automation_subcommand_dispatches_to_desktop_automation_runner(monkeypatch):
    calls: list[SimpleNamespace] = []

    def _fake_automation(namespace):
        calls.append(namespace)
        return 0

    monkeypatch.setattr("parakeet.desktop.run_gui_package_automation_command", _fake_automation)

    assert main(["gui-package-automation", "--json", "--timeout-seconds", "20", "--automation-port", "49001"]) == 0
    assert len(calls) == 1
    assert calls[0].command == "gui-package-automation"
    assert calls[0].json_output is True
    assert calls[0].timeout_seconds == 20
    assert calls[0].automation_port == 49001


def test_gui_bridge_flag_delegates_to_full_command(monkeypatch):
    monkeypatch.setattr("parakeet.desktop.run_full_command", lambda namespace: 7)

    namespace = SimpleNamespace(host="127.0.0.1", port=8765, bridge=True)

    assert desktop.run_gui_command(namespace) == 7


def test_full_command_starts_bridge_then_gui_and_cleans_up(monkeypatch):
    popen_factory = _PopenFactory()
    monkeypatch.setattr(desktop, "bridge_healthy", lambda host, port: False)
    monkeypatch.setattr(desktop, "wait_for_bridge", lambda host, port: True)
    monkeypatch.setattr(desktop, "_run_gui_process", lambda host, port: 0)
    monkeypatch.setattr(desktop, "repo_root", lambda: Path("/tmp/parakeet"))
    monkeypatch.setattr(desktop.subprocess, "Popen", popen_factory)

    namespace = SimpleNamespace(
        host="127.0.0.1",
        port=8765,
        cpu=True,
        input_device="Mic 1",
        vad=True,
        max_silence_ms=1600,
        min_speech_ms=400,
        vad_mode=3,
        clipboard=False,
        debug=True,
        log_file="bridge.log",
    )

    assert desktop.run_full_command(namespace) == 0
    assert len(popen_factory.calls) == 1
    process = popen_factory.calls[0]
    assert process.cwd == Path("/tmp/parakeet")
    assert process.command[:4] == [desktop.sys.executable, "-m", "parakeet.cli", "bridge"]
    assert "--cpu" in process.command
    assert "--vad" in process.command
    assert "--no-clipboard" in process.command
    assert "--debug" in process.command
    assert process.terminate_calls == 1
    assert process.kill_calls == 0


def test_full_command_reuses_existing_bridge(monkeypatch, capsys):
    monkeypatch.setattr(desktop, "bridge_healthy", lambda host, port: True)
    monkeypatch.setattr(desktop, "_run_gui_process", lambda host, port: 0)
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not spawn bridge")))

    namespace = SimpleNamespace(host="127.0.0.1", port=8765)

    assert desktop.run_full_command(namespace) == 0
    captured = capsys.readouterr()
    assert "Reusing existing Parakeet bridge" in captured.out


def test_stage_windows_desktop_app_copies_desktop_folder(monkeypatch, tmp_path: Path):
    repo_root = tmp_path / "repo"
    app_dir = repo_root / "desktop" / "electrobun"
    (app_dir / "src").mkdir(parents=True)
    (app_dir / "package.json").write_text('{"name":"parakeet-electrobun"}')
    (app_dir / "src" / "index.ts").write_text("console.log('hello');")
    (app_dir / "node_modules").mkdir()
    (app_dir / "node_modules" / "ignored.txt").write_text("ignore me")
    (app_dir / ".tmp-check").mkdir()
    (app_dir / ".tmp-check" / "ignored.txt").write_text("ignore me")

    stage_root = tmp_path / "mnt" / "c" / "Users" / "dev" / "AppData" / "Local" / "ParakeetDictation" / "staging"
    monkeypatch.setattr(desktop, "windows_stage_root", lambda: stage_root)
    monkeypatch.setattr(desktop, "windows_path_from_wsl", lambda path: f"C:\\stage\\{path.name}")

    payload = desktop.stage_windows_desktop_app(app_dir)
    staged_app_dir = Path(payload["desktop_app_dir"])

    assert staged_app_dir == next(stage_root.glob("*/desktop/electrobun"))
    assert (staged_app_dir / "package.json").exists()
    assert (staged_app_dir / "src" / "index.ts").read_text() == "console.log('hello');"
    assert not (staged_app_dir / "node_modules").exists()
    assert not (staged_app_dir / ".tmp-check").exists()
    assert payload["windows_desktop_app_dir"].startswith("C:\\stage\\")


def test_run_gui_package_command_stages_build_and_reports_artifacts(monkeypatch, tmp_path: Path):
    app_dir = tmp_path / "desktop" / "electrobun"
    app_dir.mkdir(parents=True)
    build_dir = app_dir / "build" / "stable-win-x64"
    artifacts_dir = app_dir / "artifacts"

    monkeypatch.setattr(desktop, "ensure_windows_bun_available", lambda: "C:\\Users\\dev\\scoop\\shims\\bun.exe")
    monkeypatch.setattr(
        desktop,
        "stage_windows_desktop_app",
        lambda: {
            "desktop_app_dir": str(app_dir),
            "windows_desktop_app_dir": "C:\\stage\\electrobun",
        },
    )
    monkeypatch.setattr(desktop, "windows_path_from_wsl", lambda path: f"C:\\stage\\{path.name}")

    commands: list[tuple[str, list[str]]] = []

    def _fake_run_windows_in_dir(windows_dir: str, command_list: list[str]) -> None:
        commands.append((windows_dir, command_list))
        build_dir.mkdir(parents=True)
        (build_dir / "parakeet-desktop" / "bin").mkdir(parents=True)
        artifacts_dir.mkdir(parents=True)
        (build_dir / "parakeet-desktop" / "bin" / "launcher").write_text("launcher")
        (build_dir / "parakeet-desktop-Setup.exe").write_text("setup")
        (build_dir / "parakeet-desktop-Setup.tar.zst").write_text("archive")
        (build_dir / "parakeet-desktop-Setup.metadata.json").write_text('{"identifier":"parakeet.desktop.local","channel":"stable","name":"parakeet-desktop"}')
        (artifacts_dir / "stable-win-x64-parakeet-desktop-Setup.zip").write_text("zip")

    monkeypatch.setattr(desktop, "run_windows_in_dir", _fake_run_windows_in_dir)

    namespace = SimpleNamespace(json_output=False)
    assert desktop.run_gui_package_command(namespace) == 0
    assert commands == [(
        "C:\\stage\\electrobun",
        ["bun install", "bunx electrobun build --env=stable"],
    )]


def test_run_gui_package_smoke_command_launches_packaged_app_and_validates_diagnostics(monkeypatch, tmp_path: Path):
    stage_root = tmp_path / "stage-root"
    stage_root.mkdir(parents=True)
    installed_root = tmp_path / "installed" / "stable"
    launcher_path = installed_root / "app" / "bin" / "launcher.exe"
    launcher_path.parent.mkdir(parents=True)
    launcher_path.write_text("launcher")
    diagnostics_path = installed_root / "logs" / "startup-diagnostics.json"
    diagnostics_path.parent.mkdir(parents=True)
    log_path = installed_root / "logs" / "startup.log"

    monkeypatch.setattr(
        desktop,
        "build_windows_package_payload",
        lambda: {
            "stage_root": str(stage_root),
            "installer_path": str(stage_root / "parakeet-desktop-Setup.exe"),
            "app_identifier": "parakeet.desktop.local",
            "app_channel": "stable",
        },
    )
    monkeypatch.setattr(
        desktop,
        "installed_windows_app_paths",
        lambda identifier, channel: {
            "app_root": installed_root,
            "app_dir": installed_root / "app",
            "launcher_path": launcher_path,
            "logs_dir": installed_root / "logs",
            "diagnostics_path": diagnostics_path,
            "log_path": log_path,
        },
    )
    monkeypatch.setattr(desktop, "windows_path_from_wsl", lambda path: f"C:\\stage\\{path.name}")
    monkeypatch.setattr(desktop, "prepare_installed_windows_app_for_reinstall", lambda installed_paths: None)

    installer_calls: list[tuple[Path, float]] = []

    def _fake_run_wsl_windows_executable_capture(executable_path: Path, *, timeout_seconds: float, env=None, cwd=None):
        installer_calls.append((executable_path, timeout_seconds))
        return _FakeCompletedProcess(returncode=0, stdout="installer ok", stderr="")

    monkeypatch.setattr(desktop, "run_wsl_windows_executable_capture", _fake_run_wsl_windows_executable_capture)

    popen_factory = _PopenFactory()

    def _fake_launch_wsl_windows_executable(executable_path: Path, *, env=None, cwd=None):
        diagnostics_path.write_text(
            json.dumps(
                {
                    "bunReady": True,
                    "rendererReady": True,
                    "rendererRpcReady": True,
                    "shutdownReason": "e2e-auto-exit",
                }
            ),
            encoding="utf-8",
        )
        log_path.write_text("renderer ready", encoding="utf-8")
        process = popen_factory([str(executable_path)], env=env, cwd=cwd)
        process.returncode = 0
        return process

    monkeypatch.setattr(desktop, "launch_wsl_windows_executable", _fake_launch_wsl_windows_executable)
    monkeypatch.setattr(desktop, "request_installed_gui_shutdown", lambda installed_paths: None)

    namespace = SimpleNamespace(json_output=False, timeout_seconds=12.0, auto_exit_ms=900)
    assert desktop.run_gui_package_smoke_command(namespace) == 0
    assert installer_calls == [(
        stage_root / "parakeet-desktop-Setup.exe",
        12.0,
    )]
    assert len(popen_factory.calls) == 1
    assert popen_factory.calls[0].command == [str(launcher_path)]
    assert popen_factory.calls[0].env is not None
    assert popen_factory.calls[0].env["PARAKEET_GUI_E2E"] == "1"
    assert popen_factory.calls[0].env["PARAKEET_GUI_AUTO_EXIT_MS"] == "900"
    assert (stage_root / "smoke" / "installer.stdout.log").read_text(encoding="utf-8") == "installer ok"
    assert (stage_root / "smoke" / "startup-diagnostics.json").exists()


def test_run_gui_package_automation_command_launches_packaged_app_and_uses_localhost_hooks(monkeypatch, tmp_path: Path):
    stage_root = tmp_path / "stage-root"
    stage_root.mkdir(parents=True)
    installed_root = tmp_path / "installed" / "stable"
    launcher_path = installed_root / "app" / "bin" / "launcher.exe"
    launcher_path.parent.mkdir(parents=True)
    launcher_path.write_text("launcher")
    diagnostics_path = installed_root / "logs" / "startup-diagnostics.json"
    diagnostics_path.parent.mkdir(parents=True)
    log_path = installed_root / "logs" / "startup.log"

    monkeypatch.setattr(
        desktop,
        "build_windows_package_payload",
        lambda: {
            "stage_root": str(stage_root),
            "installer_path": str(stage_root / "parakeet-desktop-Setup.exe"),
            "app_identifier": "parakeet.desktop.local",
            "app_channel": "stable",
        },
    )
    monkeypatch.setattr(
        desktop,
        "installed_windows_app_paths",
        lambda identifier, channel: {
            "app_root": installed_root,
            "app_dir": installed_root / "app",
            "launcher_path": launcher_path,
            "logs_dir": installed_root / "logs",
            "diagnostics_path": diagnostics_path,
            "log_path": log_path,
        },
    )
    monkeypatch.setattr(desktop, "windows_path_from_wsl", lambda path: f"C:\\stage\\{path.name}")
    monkeypatch.setattr(desktop, "prepare_installed_windows_app_for_reinstall", lambda installed_paths: None)

    installer_calls: list[tuple[Path, float]] = []

    def _fake_run_wsl_windows_executable_capture(executable_path: Path, *, timeout_seconds: float, env=None, cwd=None):
        installer_calls.append((executable_path, timeout_seconds))
        return _FakeCompletedProcess(returncode=0, stdout="installer ok", stderr="")

    monkeypatch.setattr(desktop, "run_wsl_windows_executable_capture", _fake_run_wsl_windows_executable_capture)

    popen_factory = _PopenFactory()

    def _fake_launch_wsl_windows_executable(executable_path: Path, *, env=None, cwd=None):
        diagnostics_path.write_text(
            json.dumps(
                {
                    "bunReady": True,
                    "rendererReady": True,
                    "rendererRpcReady": True,
                    "automationReady": True,
                    "shutdownReason": "automation:quit",
                }
            ),
            encoding="utf-8",
        )
        log_path.write_text("automation ready", encoding="utf-8")
        process = popen_factory([str(executable_path)], env=env, cwd=cwd)
        process.returncode = 0
        return process

    monkeypatch.setattr(desktop, "launch_wsl_windows_executable", _fake_launch_wsl_windows_executable)
    monkeypatch.setattr(desktop, "reserve_localhost_port", lambda: 49011)

    state_requests: list[str] = []
    action_requests: list[str] = []

    def _fake_wait_for_gui_e2e_state(port: int, predicate, *, timeout_seconds: float, poll_interval: float = 0.25):
        state = {
            "diagnostics": {"automationReady": True},
            "tray": {"created": True, "actions": ["open", "toggle", "quit"]},
            "hotkey": {"accelerator": "CommandOrControl+Alt+R", "registered": True},
            "renderer": {"ready": True, "rpcReady": True, "userAgent": "test-agent"},
            "bridge": {"connected": False},
        }
        state_requests.append(f"state:{port}:{timeout_seconds}")
        assert predicate(state) is True
        return state

    def _fake_invoke_gui_e2e_action(port: int, action: str, *, timeout_seconds: float = desktop.DEFAULT_GUI_E2E_ACTION_TIMEOUT_SECONDS):
        action_requests.append(f"{port}:{action}:{timeout_seconds}")
        return {"diagnostics": {"lastAutomationAction": action}}

    monkeypatch.setattr(desktop, "wait_for_gui_e2e_state", _fake_wait_for_gui_e2e_state)
    monkeypatch.setattr(desktop, "invoke_gui_e2e_action", _fake_invoke_gui_e2e_action)

    namespace = SimpleNamespace(json_output=False, timeout_seconds=12.0, automation_port=0)
    assert desktop.run_gui_package_automation_command(namespace) == 0
    assert installer_calls == [(
        stage_root / "parakeet-desktop-Setup.exe",
        12.0,
    )]
    assert len(popen_factory.calls) == 1
    assert popen_factory.calls[0].command == [str(launcher_path)]
    assert popen_factory.calls[0].env is not None
    assert popen_factory.calls[0].env["PARAKEET_GUI_E2E"] == "1"
    assert popen_factory.calls[0].env["PARAKEET_GUI_E2E_PORT"] == "49011"
    assert state_requests == ["state:49011:12.0"]
    assert action_requests == [
        f"49011:show-window:{desktop.DEFAULT_GUI_E2E_ACTION_TIMEOUT_SECONDS}",
        f"49011:tray/open:{desktop.DEFAULT_GUI_E2E_ACTION_TIMEOUT_SECONDS}",
        f"49011:quit:{desktop.DEFAULT_GUI_E2E_ACTION_TIMEOUT_SECONDS}",
    ]
    assert (stage_root / "smoke" / "startup-diagnostics.json").exists()
