"""LaunchAgent/CLI integration using temporary files and mocked launchctl only."""

import json
import os
import plistlib
from types import SimpleNamespace

import pytest

from typhoon import cli, homekit, homekit_service as service
from typhoon.config import DeviceIdentity, save_json
from typhoon.errors import TyphoonError


@pytest.fixture
def environment(tmp_path, monkeypatch):
    folder = tmp_path / "support/homekit"
    folder.mkdir(parents=True)
    plist = tmp_path / "LaunchAgents" / f"{service.LABEL}.plist"
    backend = tmp_path / "Applications/Typhoon.app/Contents/Resources/typhoon-backend"
    backend.parent.mkdir(parents=True)
    backend.write_text("synthetic executable placeholder")
    backend.chmod(0o755)
    identity = DeviceIdentity("192.0.2.50", "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(homekit, "state_directory", lambda: folder)
    monkeypatch.setattr(service, "launch_agent_path", lambda: plist)
    monkeypatch.setattr(service, "load_config", lambda: identity)
    monkeypatch.setattr(service.sys, "frozen", True, raising=False)
    monkeypatch.setattr(service.sys, "executable", str(backend))
    monkeypatch.setattr(service.sys, "platform", "darwin")
    commands = []
    runtime = {"loaded": False, "bootstrap_error": False}

    def run(command, **kwargs):
        assert command[0] == "/bin/launchctl"
        assert kwargs["capture_output"] and kwargs["timeout"] == 10
        commands.append(command)
        action = command[1]
        if action == "print":
            return SimpleNamespace(returncode=0 if runtime["loaded"] else 113)
        if action == "bootstrap":
            if runtime["bootstrap_error"]:
                return SimpleNamespace(returncode=5, stderr="secret unsafe diagnostic")
            runtime["loaded"] = True
        if action == "bootout":
            runtime["loaded"] = False
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(service.subprocess, "run", run)
    return SimpleNamespace(folder=folder, plist=plist, backend=backend, identity=identity, commands=commands, runtime=runtime)


def test_enable_installs_exact_user_job_and_discards_pairing_stdout(environment):
    e = environment
    result = service.enable()
    with e.plist.open("rb") as stream:
        job = plistlib.load(stream)
    assert job["Label"] == "com.tuxpepe1893.typhoon.homekit"
    assert job["ProgramArguments"] == [str(e.backend), "--json", "homekit", "start"]
    assert job["StandardOutPath"] == "/dev/null"
    assert job["StandardErrorPath"] == str(e.folder / "bridge-error.log")
    assert job["KeepAlive"] is True and job["RunAtLoad"] is True and job["ThrottleInterval"] == 10
    assert e.plist.stat().st_mode & 0o777 == 0o600
    assert (e.folder / "bridge-error.log").stat().st_mode & 0o777 == 0o600
    assert result["enabled"] is True and result["serviceLoaded"] is True
    assert result["running"] is False and result["available"] is False
    assert "pincode" not in result and "setupURI" not in result
    assert ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(e.plist)] in e.commands
    assert all(command[2] in (f"gui/{os.getuid()}", f"gui/{os.getuid()}/{service.LABEL}") for command in e.commands)


def test_enable_is_idempotent_and_does_not_restart_healthy_loaded_job(environment):
    service.enable()
    environment.commands.clear()
    service.enable()
    assert not any(command[1] in ("bootout", "bootstrap", "kickstart") for command in environment.commands)


def test_disable_stops_only_this_job_and_keeps_pairing(environment):
    e = environment
    service.enable()
    original = b'{"private_key":"synthetic-private-key","paired_clients":{}}'
    (e.folder / "pairing.json").write_bytes(original)
    e.commands.clear()
    result = service.disable()
    assert ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{service.LABEL}"] in e.commands
    assert not e.plist.exists()
    assert (e.folder / "pairing.json").read_bytes() == original
    assert result["enabled"] is False and result["serviceLoaded"] is False


def test_pairing_secret_is_only_in_explicit_pairing_response(environment):
    e = environment
    settings = homekit._pin_settings(e.folder, e.identity, "192.0.2.10", 51826)
    settings["setupURI"] = "X-HM://012345678ABCD"
    save_json(settings, e.folder / "bridge.json")
    with homekit._bridge_lock(e.folder):
        public = service.status()
        assert "pincode" not in public and "setupURI" not in public
        assert settings["pincode"] not in json.dumps(public)
        private = service.pairing()
        assert private["pincode"] == settings["pincode"]
        assert private["setupURI"] == "X-HM://012345678ABCD"
    with pytest.raises(TyphoonError, match="Start the Home bridge"):
        service.pairing()


def test_running_is_not_claimed_available_without_recent_verified_read(environment):
    e = environment
    homekit._pin_settings(e.folder, e.identity, "192.0.2.10", 51826)
    with homekit._bridge_lock(e.folder):
        assert service.status()["running"] is True
        assert service.status()["available"] is False
        save_json({"online": True, "lastSeen": homekit.time.time()}, e.folder / "health.json")
        assert service.status()["available"] is True
        save_json({"online": False, "lastSeen": homekit.time.time()}, e.folder / "health.json")
        assert service.status()["available"] is False
        save_json({"online": True, "lastSeen": homekit.time.time() - 60}, e.folder / "health.json")
        assert service.status()["available"] is False


def test_enable_refuses_silently_switching_paired_ac(environment, monkeypatch):
    e = environment
    homekit._pin_settings(e.folder, e.identity, "192.0.2.10", 51826)
    monkeypatch.setattr(service, "load_config", lambda: DeviceIdentity("192.0.2.51", "11:22:33:44:55:66"))
    with pytest.raises(TyphoonError, match="another air conditioner"):
        service.enable()
    assert not e.plist.exists() and e.commands == []


def test_bootstrap_failure_does_not_echo_untrusted_diagnostic(environment):
    environment.runtime["bootstrap_error"] = True
    with pytest.raises(TyphoonError) as error:
        service.enable()
    assert "secret" not in str(error.value)
    assert not environment.runtime["loaded"]


def test_enable_recovers_from_launchd_teardown_race(environment, monkeypatch):
    e = environment
    pairing = b'{"private_key":"synthetic-pairing-state"}'
    (e.folder / "pairing.json").write_bytes(pairing)
    original = service._launchctl
    attempts = []
    sleeps = []

    def transient_failure(*arguments, **kwargs):
        if arguments[0] == "bootstrap":
            attempts.append(arguments)
            if len(attempts) == 1:
                return 5
        return original(*arguments, **kwargs)

    monkeypatch.setattr(service, "_launchctl", transient_failure)
    monkeypatch.setattr(service.time, "sleep", sleeps.append)
    result = service.enable()
    assert result["serviceLoaded"] is True
    assert len(attempts) == 2 and sleeps == [0.25]
    assert (e.folder / "pairing.json").read_bytes() == pairing


def test_enable_does_not_retry_permission_failure(environment, monkeypatch):
    original = service._launchctl
    sleeps = []

    def denied(*arguments, **kwargs):
        return 1 if arguments[0] == "bootstrap" else original(*arguments, **kwargs)

    monkeypatch.setattr(service, "_launchctl", denied)
    monkeypatch.setattr(service.time, "sleep", sleeps.append)
    with pytest.raises(TyphoonError, match="could not start"):
        service.enable()
    assert sleeps == []


def test_installed_backend_required_before_service_mutation(environment, monkeypatch):
    monkeypatch.setattr(service.sys, "executable", "/tmp/random-python")
    with pytest.raises(TyphoonError, match="Install Typhoon"):
        service.enable()
    assert environment.commands == [] and not environment.plist.exists()


@pytest.mark.parametrize("action", ["status", "enable", "disable", "pairing"])
def test_cli_homekit_json_contract(action, monkeypatch, capsys):
    response = {"configured": True, "enabled": True, "serviceLoaded": True,
                "running": True, "available": True, "paired": False, "port": 51826}
    if action == "pairing":
        response.update(pincode="031-45-154", setupURI="X-HM://012345678ABCD")
    monkeypatch.setattr(service, action, lambda: response)
    assert cli.main(["--json", "homekit", action]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "homekit": response}


def test_cli_foreground_start_passes_requested_interface_and_port(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(homekit, "start", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(service, "status", lambda: {"running": False, "available": False})
    assert cli.main(["--json", "homekit", "start", "--address", "192.0.2.10", "--port", "51827"]) == 0
    assert calls == [{"address": "192.0.2.10", "port": 51827}]
    assert json.loads(capsys.readouterr().out)["ok"] is True
