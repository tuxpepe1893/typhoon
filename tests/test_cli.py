import json
import stat

import pytest

from typhoon import cli
from typhoon.config import DeviceIdentity, load_config, save_config


def test_config_roundtrip_is_private_and_atomic(tmp_path):
    path = tmp_path / "settings/device.json"
    first = DeviceIdentity("192.168.1.77", "AA:BB:CC:DD:EE:FF")
    save_config(first, path)
    assert load_config(path) == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("arguments", [["nonsense"], ["set", "--power", "maybe"], ["configure"], ["status", "--timeout", "nan"]])
def test_argument_errors_remain_json(arguments, capsys):
    assert cli.main(arguments) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert data["error"]


def test_missing_config_does_not_invent_a_device(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: None)
    assert cli.main(["--json", "config"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "device": None}
    assert cli.main(["status", "--json"]) == 1
    assert "No air conditioner configured" in json.loads(capsys.readouterr().out)["error"]


def test_configure_saves_but_does_not_claim_connection(monkeypatch, capsys):
    saved = []
    monkeypatch.setattr(cli, "save_config", saved.append)
    assert cli.main(["configure", "--host", "192.168.1.77", "--mac", "aa:bb:cc:dd:ee:ff", "--devtype", "0x4e2a"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verified"] is False
    assert len(saved) == 1


def test_empty_discovery_is_not_fabricated_successful_connection(monkeypatch, capsys):
    monkeypatch.setattr(cli, "discover", lambda **_: [])
    assert cli.main(["discover", "--timeout", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["devices"] == []
    assert "No compatible" in result["hint"]


def test_expected_local_identity_mismatch_never_constructs_controller(monkeypatch, capsys):
    save_config(DeviceIdentity("192.0.2.1", "aa:bb:cc:dd:ee:ff"))
    calls = []
    monkeypatch.setattr(cli, "Controller", lambda *_: calls.append("controller"))
    assert cli.main(["set", "--power", "off", "--expected-device", "local:11:22:33:44:55:66"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["code"] == "device_changed"
    assert result["verified"] is False
    assert calls == []


def test_expected_local_identity_match_controls_device(monkeypatch, capsys):
    from types import SimpleNamespace

    save_config(DeviceIdentity("192.0.2.1", "aa:bb:cc:dd:ee:ff"))
    calls = []
    controller = SimpleNamespace(connect=lambda: None, set=lambda changes: calls.append(changes) or {"verified": True})
    monkeypatch.setattr(cli, "Controller", lambda *_: controller)
    assert cli.main(["set", "--power", "off", "--expected-device", "local:aa:bb:cc:dd:ee:ff"]) == 0
    assert json.loads(capsys.readouterr().out)["verified"] is True
    assert calls == [{"power": False}]


class FakeController:
    reachable = {"192.0.2.9"}

    def __init__(self, identity, timeout):
        self.identity = identity

    def connect(self):
        if self.identity.host not in self.reachable:
            raise TimeoutError("timed out")

    def status(self):
        return {"device": self.identity.to_dict(), "verified": True}


def test_saved_device_that_moved_is_found_by_mac_and_config_updated(monkeypatch, capsys):
    save_config(DeviceIdentity("192.0.2.1", "aa:bb:cc:dd:ee:ff", name="Bedroom"))
    monkeypatch.setattr(cli, "Controller", FakeController)
    monkeypatch.setattr(cli, "discover", lambda **_: [DeviceIdentity("192.0.2.9", "aa:bb:cc:dd:ee:ff", name="raw")])
    assert cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["device"]["host"] == "192.0.2.9"
    saved = load_config()
    assert (saved.host, saved.name) == ("192.0.2.9", "Bedroom")


@pytest.mark.parametrize("arguments", [["status"], ["status", "--host", "192.0.2.1", "--mac", "aa:bb:cc:dd:ee:ff"]])
def test_timeout_without_a_moved_match_still_reports_no_response(monkeypatch, capsys, arguments):
    save_config(DeviceIdentity("192.0.2.1", "aa:bb:cc:dd:ee:ff"))
    monkeypatch.setattr(cli, "Controller", FakeController)
    other = DeviceIdentity("192.0.2.9", "11:22:33:44:55:66")
    monkeypatch.setattr(cli, "discover", lambda **_: [other] if arguments == ["status"] else pytest.fail("explicit host must not rediscover"))
    assert cli.main(arguments) == 1
    assert "did not respond" in json.loads(capsys.readouterr().out)["error"]
    assert load_config().host == "192.0.2.1"
