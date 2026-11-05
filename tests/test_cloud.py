"""Synthetic vendor-shaped fixtures; these tests do not contact an actual AC."""

import base64
import copy
import hashlib
import json
import ssl
import urllib.error
import urllib.parse

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from typhoon import cloud


@pytest.fixture
def session():
    return {"version": 1, "region": "usa", "userid": "test-user", "loginsession": "secret-session"}


@pytest.fixture
def device():
    return {
        "id": "test-device", "endpointId": "test-device", "name": "Living room",
        "productId": "test-product", "mac": "aabbccddeeff", "region": "usa",
        "familyId": "test-family", "transport": "cloud", "devicetypeFlag": 0,
        "devSession": "secret-device-session",
        "cookie": base64.b64encode(json.dumps({"terminalid": "test-terminal", "aeskey": "secret-aes"}).encode()).decode(),
    }


def online(state="on"):
    return {"event": {"payload": {"status": 0, "data": [{"did": "test-device", "state": state}]}}}


def sdk(values=None, *, event_name="Response"):
    values = values or {"pwr": 1, "temp": 245, "envtemp": 267, "ac_mode": 0,
                        "ac_mark": 2, "scrdisp": 1, "ac_vdir": 0}
    return {"event": {"header": {"name": event_name}, "payload": {"status": 0, "data": json.dumps({
        "params": list(values), "vals": [[{"val": value, "idx": 1}] for value in values.values()],
    })}}}


def fake_transport(monkeypatch, *responses):
    pending = iter(responses)
    calls = []

    def post(session, path, body=None, extra_headers=None):
        calls.append((path, copy.deepcopy(body), copy.deepcopy(extra_headers)))
        reply = next(pending)
        if isinstance(reply, Exception):
            raise reply
        return copy.deepcopy(reply)

    monkeypatch.setattr(cloud, "_post", post)
    return calls


@pytest.mark.parametrize("region", ["usa", "eu"])
def test_login_encrypts_vendor_payload_and_returns_only_session(monkeypatch, region):
    monkeypatch.setattr(cloud.time, "time", lambda: 1720000000.25)
    calls = fake_transport(monkeypatch, {"status": 0, "userid": "test-user", "loginsession": "secret-session"})
    actual = cloud.login(" person@example.test ", "secret-password", region)
    assert actual == {"version": 1, "region": region, "userid": "test-user", "loginsession": "secret-session"}
    path, ciphertext, headers = calls[0]
    assert path == "/account/login"
    assert headers["timestamp"] == "1720000000.25"
    key = hashlib.md5(b"1720000000.25kdixkdqp54545^#*").digest()
    decryptor = Cipher(algorithms.AES(key), modes.CBC(bytes.fromhex("eaaaaa3abb5862a21918b5771d1615aa"))).decryptor()
    plaintext = (decryptor.update(ciphertext) + decryptor.finalize()).rstrip(b"\0")
    assert json.loads(plaintext) == {
        "email": "person@example.test", "password": hashlib.sha1(b"secret-password4969fj#k23#").hexdigest(),
        "companyid": "48eb1b36cf0202ab2ef07b880ecda60d", "lid": "3c015b249dd66ef0f11f9bef59ecd737",
    }
    assert headers["token"] == hashlib.md5(plaintext + b"xgx3d*fe3478$ukx").hexdigest()
    assert b"secret-password" not in ciphertext


def test_login_rejection_does_not_echo_response_or_credentials(monkeypatch):
    fake_transport(monkeypatch, {"status": -1001, "message": "secret-password secret-session person@example.test"})
    with pytest.raises(cloud.CloudAuthenticationError) as error:
        cloud.login("person@example.test", "secret-password")
    assert "secret" not in str(error.value)
    assert "example.test" not in str(error.value)
    assert error.value.details["verified"] is False


def test_discover_merges_owned_shared_and_excludes_cached_state(monkeypatch, session, device):
    owned = dict(device, friendlyName="Living room", params={"pwr": 1}, state="on", email="private@example.test")
    shared = dict(device, endpointId="second-device", friendlyName="Bedroom")
    fake_transport(monkeypatch,
        {"status": 0, "data": {"familyList": [{"familyid": "test-family"}]}},
        {"status": 0, "data": {"endpoints": [owned]}},
        {"status": 0, "data": {"shareFromOther": [{"devinfo": owned}, {"devinfo": shared}]}},
    )
    found = cloud.discover(session)
    assert [d["id"] for d in found] == ["test-device", "second-device"]
    assert all("params" not in d and "state" not in d and "email" not in d for d in found)
    assert found[0]["devSession"] == device["devSession"]
    assert cloud.public_device(found[0]) == {
        "id": "test-device", "name": "Living room", "mac": "aabbccddeeff",
        "host": "Tornado cloud (USA)", "transport": "cloud",
    }
    public = json.dumps([cloud.public_device(d) for d in found])
    assert not any(secret in public for secret in ("secret-session", "secret-device-session", device["cookie"]))


def test_public_device_uses_endpoint_id_when_mac_missing(device):
    del device["mac"]
    assert cloud.public_device(device)["mac"] == "test-device"


def test_get_status_requires_online_and_direct_read(monkeypatch, session, device):
    device["params"] = {"pwr": 0, "temp": 180}  # Inventory cache is deliberately contradictory.
    calls = fake_transport(monkeypatch, online(), sdk())
    result = cloud.get_status(session, device)
    assert result["state"] == {
        "power": True, "temperature": 24.5, "ambientTemperature": 26.7,
        "mode": "cool", "fan": "medium", "display": True, "swing": False,
    }
    assert result["verified"] is True
    assert result["verification"] == "cloud-readback"
    assert calls[0][0] == "/device/control/v2/querystate"
    path, body, _ = calls[1]
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["license"] == [cloud.LICENSE]
    assert body["directive"]["payload"]["act"] == "get"
    pairing = json.loads(base64.b64decode(body["directive"]["endpoint"]["devicePairedInfo"]["cookie"]))
    assert pairing["device"]["id"] == "test-terminal"
    assert pairing["device"]["aeskey"] == "secret-aes"


@pytest.mark.parametrize("state", ["off", "offline", 0, "0"])
def test_offline_never_attempts_settings_read(monkeypatch, session, device, state):
    calls = fake_transport(monkeypatch, online(state))
    with pytest.raises(cloud.CloudOfflineError) as error:
        cloud.get_status(session, device)
    assert len(calls) == 1
    assert error.value.details["verified"] is False


@pytest.mark.parametrize("name", [None, "ReportState", "ErrorResponse"])
def test_cached_or_push_response_cannot_satisfy_direct_read(monkeypatch, session, device, name):
    fake_transport(monkeypatch, online(), sdk(event_name=name))
    with pytest.raises(cloud.CloudError):
        cloud.get_status(session, device)


def test_wrong_device_online_response_rejected(monkeypatch, session, device):
    response = online()
    response["event"]["payload"]["data"][0]["did"] = "different-device"
    calls = fake_transport(monkeypatch, response)
    with pytest.raises(cloud.CloudError):
        cloud.get_status(session, device)
    assert len(calls) == 1


@pytest.mark.parametrize("mode,name", [(0, "cool"), (1, "heat"), (2, "dry"), (3, "fan"), (4, "auto")])
def test_cloud_mode_numbers_follow_vendor_not_local_protocol(mode, name):
    assert cloud._normalize({"pwr": 0, "temp": 230, "ac_mode": mode, "ac_mark": 0})["mode"] == name


def test_older_sensor_query_and_optional_settings(monkeypatch, session, device):
    calls = fake_transport(monkeypatch, online(), sdk({"pwr": 0, "temp": 230, "ac_mode": 4, "ac_mark": 5}), sdk({"envtemp": 260}))
    state = cloud.get_state(session, device)
    assert state["ambientTemperature"] == 26
    assert state["display"] is None and state["swing"] is None and state["fan"] == "silent"
    assert calls[-1][1]["directive"]["payload"]["params"] == ["mode"]
    assert calls[-1][1]["directive"]["payload"]["vals"] == [[{"val": 0, "idx": 1}]]


def test_set_sends_only_requested_changes_then_independent_read(monkeypatch, session, device):
    changed = {"pwr": 0, "temp": 240, "envtemp": 267, "ac_mode": 1, "ac_mark": 2, "scrdisp": 1, "ac_vdir": 0}
    calls = fake_transport(monkeypatch, online(), sdk(), sdk({"pwr": 0, "temp": 240, "ac_mode": 1}), online(), sdk(changed))
    result = cloud.set_state(session, device, {"power": False, "temperature": 24, "mode": "heat"})
    assert result["before"]["power"] is True
    assert result["state"]["power"] is False
    assert result["verified"] is True and result["readbackMatched"] is True
    writes = [body["directive"]["payload"] for _, body, _ in calls if body["directive"]["payload"].get("act") == "set"]
    assert writes == [{"act": "set", "params": ["pwr", "temp", "ac_mode"],
                       "vals": [[{"val": 0, "idx": 1}], [{"val": 240, "idx": 1}], [{"val": 1, "idx": 1}]], "did": "test-device"}]
    assert len(calls) == 5  # Online+get, set, online+get; never just set echo.


def test_successful_set_echo_is_not_verified_if_fresh_read_disagrees(monkeypatch, session, device):
    calls = fake_transport(monkeypatch, online(), sdk(), sdk({"pwr": 0}), online(), sdk(), online(), sdk(), online(), sdk())
    with pytest.raises(cloud.CloudError) as error:
        cloud.set_state(session, device, {"power": False}, readback_delay=0)
    assert error.value.details["verified"] is False
    assert error.value.details["readbackMatched"] is False
    assert error.value.details["mismatches"] == {"power": {"requested": False, "actual": True}}
    assert sum(body["directive"]["payload"].get("act") == "set" for _, body, _ in calls) == 1


def test_write_timeout_is_never_retried(monkeypatch, session, device):
    calls = fake_transport(monkeypatch, online(), sdk(), cloud.CloudError("connection lost", code="cloud_connection"))
    with pytest.raises(cloud.CloudError, match="may have reached") as error:
        cloud.set_state(session, device, {"power": False})
    assert error.value.details["verified"] is False
    assert len(calls) == 3


@pytest.mark.parametrize("changes", [{}, {"password": "no"}, {"power": 1}, {"temperature": 33}, {"temperature": float("nan")},
                                     {"temperature": 22.25}, {"mode": []}, {"fan": "turbo"}, {"display": "yes"}])
def test_invalid_control_never_reaches_network(monkeypatch, session, device, changes):
    calls = fake_transport(monkeypatch)
    with pytest.raises(cloud.CloudError):
        cloud.set_state(session, device, changes)
    assert calls == []


def test_http_transport_uses_selected_https_server_and_verified_tls(monkeypatch, session):
    seen = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, limit):
            assert limit == cloud.MAX_RESPONSE_BYTES + 1
            return b'{"status":0}'

    class Opener:
        def open(self, request, timeout):
            seen["request"] = request
            assert timeout == 15
            return Reply()

    def build(*handlers):
        assert isinstance(handlers[0], cloud._NoRedirect)
        context = handlers[1]._context
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        return Opener()

    monkeypatch.setattr(cloud.urllib.request, "build_opener", build)
    session["region"] = "eu"
    assert cloud._post(session, "/test", {"key": "value"}) == {"status": 0}
    assert seen["request"].full_url == "https://app-service-deu-f0e9ebbb.smarthomecs.de/test"
    assert seen["request"].get_header("Loginsession") == "secret-session"
    assert cloud._NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.test") is None


def test_network_error_does_not_reveal_private_reason(monkeypatch, session):
    class Opener:
        def open(self, *args, **kwargs):
            raise urllib.error.URLError("secret-session password private@example.test")

    monkeypatch.setattr(cloud.urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(cloud.CloudError) as error:
        cloud._post(session, "/test")
    assert "secret" not in str(error.value)
    assert "example.test" not in str(error.value)
    assert error.value.details["verified"] is False
