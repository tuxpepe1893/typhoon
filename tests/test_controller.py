from copy import deepcopy

import broadlink
import pytest

from typhoon.config import DeviceIdentity
from typhoon.controller import Controller, TornadoHVAC, make_device, merge_state
from typhoon.errors import TyphoonError


@pytest.fixture
def original():
    return {"power": True, "target_temp": 24.5, "mode": broadlink.hvac.Mode.COOL,
            "speed": broadlink.hvac.Speed.MID, "preset": broadlink.hvac.Preset.NORMAL,
            "swing_h": broadlink.hvac.SwHoriz.ON, "swing_v": broadlink.hvac.SwVert.POS3,
            "sleep": True, "ifeel": True, "display": True, "health": True,
            "clean": False, "mildew": True}


class FakeDevice:
    def __init__(self, original):
        self.original = deepcopy(original)
        self.state = deepcopy(original)
        self.sent = []
        self.reads = 0
        self.apply_commands = True
        self.ambient_error = False
        self.authenticated = False

    def auth(self):
        self.authenticated = True

    def get_state(self):
        assert self.authenticated
        self.reads += 1
        return deepcopy(self.state)

    def set_state(self, **state):
        self.sent.append(deepcopy(state))
        if self.apply_commands:
            self.state = deepcopy(state)
        # Always return the desired state, including when the device ignored it.
        return state

    def get_ac_info(self):
        if self.ambient_error:
            raise TimeoutError("optional sensor unavailable")
        return {"ambient_temp": 27.1}


def controller_for(fake):
    return Controller(DeviceIdentity("192.168.1.77", "aa:bb:cc:dd:ee:ff"), factory=lambda *_: fake)


def test_display_change_preserves_every_untouched_field_and_original(original):
    snapshot = deepcopy(original)
    fake = FakeDevice(original)
    result = controller_for(fake).set({"display": False}, readback_delay=0)
    expected = {**original, "display": False}
    assert fake.sent == [expected]
    assert original == snapshot
    assert fake.reads == 2  # Initial read and distinct verification read.
    assert result["verified"] is True
    assert result["state"]["display"] is False
    assert result["state"]["ambientTemperature"] == 27.1


def test_unconfirmed_write_is_not_reported_as_success(original):
    fake = FakeDevice(original)
    fake.apply_commands = False
    with pytest.raises(TyphoonError) as error:
        controller_for(fake).set({"temperature": 22}, readback_delay=0)
    assert error.value.details["verified"] is False
    assert error.value.details["mismatches"]["temperature"] == {"requested": 22, "actual": 24.5}
    assert fake.reads == 4
    assert len(fake.sent) == 1  # Only reads are retried.


@pytest.mark.parametrize("temperature", [15, 33, 22.1, float("nan"), float("inf"), True, "22"])
def test_invalid_temperatures_never_authenticate_or_write(original, temperature):
    fake = FakeDevice(original)
    with pytest.raises(TyphoonError):
        controller_for(fake).set({"temperature": temperature})
    assert fake.authenticated is False
    assert fake.sent == []


def test_missing_preserved_state_rejects_command(original):
    original.pop("health")
    fake = FakeDevice(original)
    with pytest.raises(TyphoonError, match="incomplete"):
        controller_for(fake).set({"power": False})
    assert fake.sent == []


def test_ambient_failure_does_not_hide_success(original):
    fake = FakeDevice(original)
    fake.ambient_error = True
    result = controller_for(fake).status()
    assert result["verified"] is True
    assert result["state"]["ambientTemperature"] is None


def test_lost_readback_is_explicitly_unverified(original):
    fake = FakeDevice(original)
    original_read = fake.get_state

    def read():
        if fake.sent:
            raise TimeoutError("lost reply")
        return original_read()

    fake.get_state = read
    with pytest.raises(TyphoonError, match="verification read failed") as error:
        controller_for(fake).set({"display": False})
    assert error.value.details["verified"] is False
    assert len(fake.sent) == 1


def test_lost_command_ack_does_not_claim_no_change(original):
    fake = FakeDevice(original)

    def send(**state):
        fake.sent.append(state)
        raise TimeoutError("lost acknowledgement")

    fake.set_state = send
    with pytest.raises(TyphoonError, match="may have received") as error:
        controller_for(fake).set({"display": False})
    assert error.value.details["verified"] is False
    assert len(fake.sent) == 1


def test_swing_changes_vertical_only(original):
    new = merge_state(original, {"swing": False})
    assert new["swing_v"] == broadlink.hvac.SwVert.OFF
    assert new["swing_h"] == original["swing_h"]
    assert {key: value for key, value in new.items() if key != "swing_v"} == {key: value for key, value in original.items() if key != "swing_v"}


def test_turbo_does_not_silently_override_requested_fan(original):
    original.update(preset=broadlink.hvac.Preset.TURBO, speed=broadlink.hvac.Speed.HIGH)
    fake = FakeDevice(original)
    result = controller_for(fake).set({"fan": "low"}, readback_delay=0)
    assert result["state"]["fan"] == "low"
    assert fake.sent[0]["speed"] == broadlink.hvac.Speed.LOW
    assert fake.sent[0]["preset"] == broadlink.hvac.Preset.TURBO


def test_stale_locked_identity_still_attempts_real_authentication(original):
    fake = FakeDevice(original)
    controller = Controller(DeviceIdentity("192.168.1.77", "aa:bb:cc:dd:ee:ff", locked=True), factory=lambda *_: fake)
    result = controller.status()
    assert fake.authenticated
    assert result["verified"] is True


@pytest.mark.parametrize("half,preset,expected", [(False, 0, 24), (True, 0, 24.5), (False, 2, 24), (True, 2, 24.5)])
def test_half_degree_is_not_confused_with_mute(half, preset, expected):
    # Fixed AUX state fixture, independent of the implementation's encoder:
    # byte 0 = 24 C/vertical off; byte 2 = fractional bit; byte 4 = preset.
    packet = bytearray.fromhex("87e00d40002000002000100000")
    packet[2] |= 0x80 if half else 0
    packet[4] = preset << 6
    device = make_device(DeviceIdentity("192.168.1.77", "aa:bb:cc:dd:ee:ff"))
    assert device._parse_state(packet)["target_temp"] == expected
