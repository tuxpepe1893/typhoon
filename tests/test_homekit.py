"""HAP service tests with a fake AC; never advertise, pair, or send AC packets."""

from contextlib import contextmanager
import asyncio
import copy
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pyhap.accessory_driver import AccessoryDriver
from pyhap.characteristic import CharacteristicError
from pyhap.loader import Loader
from pyhap.encoder import AccessoryEncoder
from pyhap.state import State

from typhoon import homekit
from typhoon.config import DeviceIdentity
from typhoon.errors import TyphoonError


STATE = {"power": True, "temperature": 24.5, "ambientTemperature": 26.7,
         "mode": "cool", "fan": "medium", "display": True, "swing": False}


@pytest.fixture
def identity():
    return DeviceIdentity("192.0.2.40", "aa:bb:cc:dd:ee:ff")


@pytest.fixture
def rig(identity):
    calls = []
    lock_held = False
    live = {"state": copy.deepcopy(STATE), "fail": False, "mismatch": False, "verified": True, "now": 1.0}

    @contextmanager
    def lock(timeout):
        nonlocal lock_held
        assert timeout == 3 and not lock_held
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    class FakeController:
        def __init__(self, selected, timeout):
            assert lock_held and selected == identity and timeout == 3

        def status(self):
            assert lock_held
            calls.append(("read", None))
            if live["fail"]:
                raise RuntimeError("secret vendor session detail")
            return {"verified": live["verified"], "state": copy.deepcopy(live["state"])}

        def set(self, changes):
            assert lock_held
            calls.append(("write", dict(changes)))
            if live["fail"]:
                raise RuntimeError("secret vendor session detail")
            if not live["mismatch"]:
                live["state"].update(changes)
            return {"verified": live["verified"], "state": copy.deepcopy(live["state"])}

    backend = homekit.ACBackend(identity, factory=FakeController, lock=lock, clock=lambda: live["now"])
    backend.refresh()
    driver = SimpleNamespace(loader=Loader(), loop=SimpleNamespace(call_soon_threadsafe=Mock()), publish=Mock(), _notify=Mock())
    bridge = homekit.ACBridge(driver, backend)
    driver.accessory = bridge
    return SimpleNamespace(backend=backend, driver=driver, bridge=bridge, calls=calls, live=live,
                           thermostat=bridge.accessories[2], display=bridge.accessories[3])


def test_standard_services_have_real_sensor_and_half_degree_limits(rig):
    thermostat = rig.thermostat.thermostat
    assert rig.thermostat.available
    assert thermostat.get_characteristic("CurrentTemperature").get_value() == 26.7
    assert thermostat.get_characteristic("TargetTemperature").get_value() == 24.5
    assert thermostat.get_characteristic("TargetHeatingCoolingState").get_value() == 2
    assert thermostat.get_characteristic("TargetTemperature").properties["minStep"] == 0.5
    assert rig.display.switch.get_characteristic("On").get_value() is True
    assert rig.calls == [("read", None)]


@pytest.mark.parametrize("mode,power,target,current", [
    ("cool", True, 2, 2), ("heat", True, 1, 1), ("auto", True, 3, 0), ("dry", False, 0, 0),
])
def test_mode_mapping(mode, power, target, current):
    result = homekit.thermostat_values(dict(STATE, mode=mode, power=power))
    assert result["TargetHeatingCoolingState"] == target
    assert result["CurrentHeatingCoolingState"] == current


@pytest.mark.parametrize("override", [{"mode": "dry"}, {"mode": "fan"}, {"ambientTemperature": None}])
def test_unrepresentable_thermostat_is_unavailable_but_display_still_works(rig, override):
    rig.live["state"].update(override)
    rig.backend.refresh()
    assert not rig.thermostat.available
    assert rig.display.available
    with pytest.raises(CharacteristicError):
        rig.thermostat.thermostat.get_characteristic("CurrentTemperature").get_value()
    assert rig.display.read() is True


def test_hap_returns_communication_failure_after_real_read_fails(rig):
    rig.live["fail"] = True
    with pytest.raises(TyphoonError) as error:
        rig.backend.refresh()
    assert "secret" not in str(error.value)
    assert not rig.backend.available
    char = rig.display.switch.get_characteristic("On")
    iid = rig.display.iid_manager.get_iid(char)
    result = AccessoryDriver.get_characteristics(rig.driver, [f"3.{iid}"])
    assert result == {"characteristics": [{"aid": 3, "iid": iid, "status": -70402}]}


def test_old_poll_result_becomes_unavailable(rig):
    rig.live["now"] += homekit.MAX_STATE_AGE + 1
    assert not rig.thermostat.available and not rig.display.available


def test_service_callback_combines_mode_and_temperature_in_one_verified_write(rig):
    characteristics = []
    for name, value in (("TargetHeatingCoolingState", 1), ("TargetTemperature", 23.5)):
        char = rig.thermostat.thermostat.get_characteristic(name)
        characteristics.append({"aid": 2, "iid": rig.thermostat.iid_manager.get_iid(char), "value": value})
    result = AccessoryDriver.set_characteristics(rig.driver, {"characteristics": characteristics}, ("192.0.2.90", 12345))
    assert result is None  # HAP success status after the service's verified write.
    assert rig.calls[-1] == ("write", {"power": True, "mode": "heat", "temperature": 23.5})
    assert rig.thermostat.read("TargetHeatingCoolingState") == 1
    assert rig.thermostat.read("TargetTemperature") == 23.5


def test_home_off_preserves_existing_mode_and_temperature(rig):
    rig.thermostat.set_values({"TargetHeatingCoolingState": 0})
    assert rig.calls[-1] == ("write", {"power": False})
    assert rig.backend.state["mode"] == "cool"
    assert rig.backend.state["temperature"] == 24.5


@pytest.mark.parametrize("kind", ["fail", "mismatch", "unverified"])
def test_failed_home_write_is_not_an_optimistic_success(rig, kind):
    if kind == "unverified":
        rig.live["verified"] = False
    else:
        rig.live[kind] = True
    char = rig.display.switch.get_characteristic("On")
    assert char.value is True
    with pytest.raises(CharacteristicError):
        char.client_update_value(False)
    assert char.value is True  # Restored last actual readback, now explicitly unavailable.
    assert not rig.display.available
    assert rig.calls[-1] == ("write", {"display": False})


def test_display_changes_use_corrected_public_visibility(rig):
    rig.display.switch.get_characteristic("On").client_update_value(False)
    assert rig.calls[-1] == ("write", {"display": False})
    assert rig.display.read() is False


@pytest.mark.parametrize("values", [{"TargetTemperature": 33}, {"TargetTemperature": 22.1},
                                   {"TargetHeatingCoolingState": 4}, {"TargetHeatingCoolingState": True}])
def test_invalid_hap_changes_do_not_touch_ac(rig, values):
    with pytest.raises(CharacteristicError):
        rig.thermostat.set_values(values)
    assert rig.calls == [("read", None)]


def test_pairing_settings_are_private_and_pin_identity(tmp_path, identity, monkeypatch):
    monkeypatch.setattr(homekit, "state_directory", lambda: tmp_path)
    first = homekit._pin_settings(tmp_path, identity, "192.0.2.5", 51826)
    assert (tmp_path / "bridge.json").stat().st_mode & 0o777 == 0o600
    assert len(first["pincode"]) == 10
    renamed = DeviceIdentity("192.0.2.41", identity.mac, name="Updated room")
    second = homekit._pin_settings(tmp_path, renamed, "192.0.2.5", 51826)
    assert second["pincode"] == first["pincode"]
    other = DeviceIdentity("192.0.2.42", "11:22:33:44:55:66")
    with pytest.raises(TyphoonError, match="another air conditioner"):
        homekit._pin_settings(tmp_path, other, "192.0.2.5", 51826)
    (tmp_path / "pairing.json").write_text(json.dumps({"private_key": "secret-key", "paired_clients": {"private-client": "private-value"}}))
    result = homekit.status()
    assert result["paired"] is True and result["running"] is False
    serialized = json.dumps(result)
    assert all(value not in serialized for value in ("private_key", "secret-key", "private-client", "private-value", first["pincode"]))


def test_status_distinguishes_running_lock_without_network(tmp_path, identity, monkeypatch):
    monkeypatch.setattr(homekit, "state_directory", lambda: tmp_path)
    assert homekit.status()["configured"] is False
    homekit._pin_settings(tmp_path, identity, "192.0.2.5", 51826)
    with homekit._bridge_lock(tmp_path):
        assert homekit.status()["running"] is True
    assert homekit.status()["running"] is False


def test_hap_pairing_persistence_writes_owner_only_file(tmp_path):
    # Exercise HAP-python's real file encoder without constructing a server.
    driver = SimpleNamespace(persist_file=str(tmp_path / "pairing.json"), encoder=AccessoryEncoder(),
                             state=State(address="127.0.0.1", mac="aa:bb:cc:dd:ee:ff", pincode=b"031-45-154", port=51826))
    AccessoryDriver.persist(driver)
    assert (tmp_path / "pairing.json").stat().st_mode & 0o777 == 0o600
    assert "private_key" in json.loads((tmp_path / "pairing.json").read_text())


@pytest.mark.parametrize("port", [0, 80, 1023, 65536, True])
def test_invalid_port_fails_before_any_device_or_driver_creation(port, monkeypatch):
    read = Mock(side_effect=AssertionError("must not read config"))
    monkeypatch.setattr(homekit, "load_config", read)
    with pytest.raises(TyphoonError, match="port"):
        homekit.start(port=port)
    read.assert_not_called()


def lifecycle_driver():
    """No sockets or actual HAP startup: exercise only event-loop ownership."""
    return SimpleNamespace(loop=asyncio.new_event_loop(), stop_event=threading.Event(),
                           aio_stop_event=None, advertiser=None, mdns_service_info=None,
                           http_server=SimpleNamespace(server=None), accessory=None,
                           executor=Mock())


def test_bridge_runner_supports_python_without_child_watchers(monkeypatch):
    monkeypatch.delattr(asyncio, "SafeChildWatcher", raising=False)
    monkeypatch.delattr(asyncio, "set_child_watcher", raising=False)
    driver = lifecycle_driver()

    async def start():
        driver.aio_stop_event = asyncio.Event()
        driver.loop.call_later(0.001, driver.loop.stop)

    driver.async_start = start
    homekit._run_driver(driver)
    assert driver.stop_event.is_set() and driver.aio_stop_event.is_set()
    assert driver.loop.is_closed()
    driver.executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)


def test_bridge_startup_failure_is_reported_and_closes_partial_resources():
    driver = lifecycle_driver()
    driver.http_server = SimpleNamespace(server=SimpleNamespace(wait_closed=AsyncMock()), async_stop=Mock())
    driver.advertiser = SimpleNamespace(async_unregister_service=AsyncMock(side_effect=RuntimeError("not registered")),
                                        async_close=AsyncMock())
    driver.mdns_service_info = object()
    driver.async_start = AsyncMock(side_effect=OSError("private low-level details"))
    with pytest.raises(TyphoonError, match="could not start") as failure:
        homekit._run_driver(driver)
    assert "private low-level details" not in str(failure.value)
    driver.advertiser.async_close.assert_awaited_once()
    driver.http_server.async_stop.assert_called_once()
    driver.http_server.server.wait_closed.assert_awaited_once()
    assert driver.loop.is_closed()
    driver.executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)


def test_bridge_runner_does_not_repeat_completed_signal_shutdown():
    driver = lifecycle_driver()
    driver.advertiser = SimpleNamespace(async_close=AsyncMock())

    async def start():
        def finish_signal_shutdown():
            driver.stop_event.set()
            driver.loop.stop()
        driver.loop.call_later(0.001, finish_signal_shutdown)

    driver.async_start = start
    homekit._run_driver(driver)
    driver.advertiser.async_close.assert_not_awaited()
    assert driver.loop.is_closed()
