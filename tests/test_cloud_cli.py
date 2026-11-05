from copy import deepcopy
from io import StringIO
import json
import stat
from types import SimpleNamespace

import pytest

from typhoon import cli, config
from typhoon.errors import TyphoonError

SECRET = "sensitive-test-password"
SESSION_TOKEN = "sensitive-test-session"
DEVICE_COOKIE = "sensitive-test-cookie"
PUBLIC = {"id": "ac-one", "name": "Bedroom", "mac": "aa:bb:cc:dd:ee:ff",
          "host": "Tornado cloud (USA)", "transport": "cloud"}
STATE = {"power": True, "temperature": 24.5, "ambientTemperature": None,
         "mode": "cool", "fan": "turbo", "display": None, "swing": None}


@pytest.fixture
def services(monkeypatch):
    store = {}
    calls = []
    raw_device = {**PUBLIC, "cookie": DEVICE_COOKIE, "devSession": "private-device-session"}

    def login(email, password, region):
        calls.append(("login", email, password, region))
        return {"userid": "user-one", "loginsession": SESSION_TOKEN, "region": region}

    def save(name, value):
        store[name] = deepcopy(value)

    def status(session, device):
        calls.append(("status",))
        return {"state": deepcopy(STATE), "verified": True, "verification": "cloud-readback",
                "cookie": DEVICE_COOKIE}  # Not forwarded by the CLI.

    def set_state(session, device, changes):
        calls.append(("set", changes))
        return {"state": {**STATE, **changes}, "verified": True, "verification": "cloud-readback"}

    cloud = SimpleNamespace(login=login, discover=lambda _: [raw_device],
                            public_device=lambda device: device, get_status=status, set_state=set_state)
    keychain = SimpleNamespace(save_secret=save, load_secret=lambda name: deepcopy(store.get(name)),
                               delete_secret=lambda name: store.pop(name, None))
    monkeypatch.setattr(cli, "cloud_services", lambda: (cloud, keychain))
    return SimpleNamespace(cloud=cloud, keychain=keychain, store=store, calls=calls, raw_device=raw_device)


def sign_in(monkeypatch, services):
    credentials = json.dumps({"email": "person@example.com", "password": SECRET, "region": "usa"})
    monkeypatch.setattr(cli.sys, "stdin", StringIO(credentials))
    return cli.main(["cloud-login", "--stdin"])


def test_stdin_login_keeps_secrets_out_of_output_and_files(services, monkeypatch, capsys):
    assert sign_in(monkeypatch, services) == 0
    output = capsys.readouterr().out
    assert json.loads(output) == {"ok": True, "devices": [PUBLIC], "signedIn": True}
    assert SECRET not in json.dumps(services.store)
    assert SESSION_TOKEN in json.dumps(services.store)
    assert DEVICE_COOKIE in json.dumps(services.store)
    assert not config.config_path().exists()
    assert not config.selection_path().exists()


def test_cloud_select_stores_only_public_metadata_and_preserves_local(services, monkeypatch, capsys):
    local = config.DeviceIdentity("192.0.2.1", "aa:bb:cc:dd:ee:ff")
    config.save_config(local)
    assert sign_in(monkeypatch, services) == 0
    capsys.readouterr()
    assert cli.main(["cloud-select", "--id", "ac-one"]) == 0
    assert json.loads(capsys.readouterr().out)["verified"] is False
    assert json.loads(config.selection_path().read_text()) == {"transport": "cloud", "device": PUBLIC}
    assert stat.S_IMODE(config.selection_path().stat().st_mode) == 0o600
    assert config.load_config() == local
    assert cli.main(["config"]) == 0
    assert json.loads(capsys.readouterr().out)["device"] == PUBLIC


def test_status_and_set_route_to_selected_cloud_with_private_output(services, monkeypatch, capsys):
    assert sign_in(monkeypatch, services) == 0
    assert cli.main(["cloud-select", "--id", "ac-one"]) == 0
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"] == STATE
    assert status["verification"] == "cloud-readback"
    assert status["verified"] is True
    assert DEVICE_COOKIE not in json.dumps(status)
    assert cli.main(["set", "--display", "off"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"]["display"] is False
    assert ("set", {"display": False}) in services.calls


def test_explicit_local_arguments_override_cloud_without_switching_selection(services, monkeypatch, capsys):
    assert sign_in(monkeypatch, services) == 0
    assert cli.main(["cloud-select", "--id", "ac-one"]) == 0
    capsys.readouterr()
    local = SimpleNamespace(status=lambda: {"state": {"power": False}, "verified": True})
    monkeypatch.setattr(cli, "Controller", lambda *_: local)
    assert cli.main(["status", "--host", "192.0.2.1", "--mac", "aa:bb:cc:dd:ee:ff"]) == 0
    assert json.loads(capsys.readouterr().out)["state"]["power"] is False
    assert config.load_cloud_selection() == PUBLIC
    assert ("status",) not in services.calls


def test_cloud_logout_deletes_account_and_returns_to_local(services, monkeypatch, capsys):
    local = config.DeviceIdentity("192.0.2.1", "aa:bb:cc:dd:ee:ff")
    config.save_config(local)
    assert sign_in(monkeypatch, services) == 0
    assert cli.main(["cloud-select", "--id", "ac-one"]) == 0
    capsys.readouterr()
    assert cli.main(["cloud-logout"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["signedIn"] is False
    assert result["device"] == local.to_dict()
    assert not services.store
    assert not config.selection_path().exists()


def test_failed_refresh_preserves_account_and_selection(services, monkeypatch, capsys):
    assert sign_in(monkeypatch, services) == 0
    assert cli.main(["cloud-select", "--id", "ac-one"]) == 0
    capsys.readouterr()
    previous = deepcopy(services.store)

    def fail(_):
        raise TyphoonError("The cloud could not be reached.")

    services.cloud.discover = fail
    assert cli.main(["cloud-devices"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert services.store == previous
    assert config.load_cloud_selection() == PUBLIC


def test_unexpected_provider_error_does_not_print_secrets(services, monkeypatch, capsys):
    def fail(*_):
        raise RuntimeError(f"Request failed: password={SECRET}, token={SESSION_TOKEN}")

    services.cloud.login = fail
    assert sign_in(monkeypatch, services) == 1
    output = capsys.readouterr().out
    assert SECRET not in output and SESSION_TOKEN not in output
    assert json.loads(output)["ok"] is False


def test_configure_switches_back_to_local_without_logging_out(services, monkeypatch, capsys):
    assert sign_in(monkeypatch, services) == 0
    assert cli.main(["cloud-select", "--id", "ac-one"]) == 0
    capsys.readouterr()
    assert cli.main(["configure", "--host", "192.0.2.1", "--mac", "aa:bb:cc:dd:ee:ff"]) == 0
    assert json.loads(capsys.readouterr().out)["device"]["host"] == "192.0.2.1"
    assert config.load_cloud_selection() is None
    assert services.store.get("cloud")


def test_plain_password_argument_error_does_not_echo_password(capsys):
    assert cli.main(["cloud-login", "--password", SECRET]) == 1
    output = capsys.readouterr().out
    assert SECRET not in output
    assert "Passwords cannot" in json.loads(output)["error"]


def test_expected_cloud_identity_mismatch_never_calls_control(services, monkeypatch, capsys):
    assert sign_in(monkeypatch, services) == 0
    assert cli.main(["cloud-select", "--id", "ac-one"]) == 0
    capsys.readouterr()
    services.calls.clear()
    assert cli.main(["set", "--power", "off", "--expected-device", "cloud:another-ac"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["code"] == "device_changed"
    assert services.calls == []


def test_expected_cloud_identity_match_controls_device(services, monkeypatch, capsys):
    assert sign_in(monkeypatch, services) == 0
    assert cli.main(["cloud-select", "--id", "ac-one"]) == 0
    capsys.readouterr()
    assert cli.main(["set", "--power", "off", "--expected-device", "cloud:ac-one"]) == 0
    assert json.loads(capsys.readouterr().out)["verified"] is True
    assert ("set", {"power": False}) in services.calls
