"""Personal, local HomeKit bridge backed by Typhoon's verified AC controller.

Uses HAP-python's accessory protocol, not Apple's controller-side HomeKit SDK.
Pairing keys stay in Application Support; they must never be copied into Git.
Run with ``typhoon homekit start`` to advertise on the LAN.
"""

import asyncio
from contextlib import contextmanager, suppress
import fcntl
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import secrets
import signal
import socket
import threading
import time

from pyhap.accessory import Accessory, Bridge
from pyhap.accessory_driver import AccessoryDriver
from pyhap.characteristic import CharacteristicError
from pyhap.const import CATEGORY_SWITCH, CATEGORY_THERMOSTAT

from .config import DeviceIdentity, config_path, device_lock, load_config, save_json
from .controller import Controller
from .errors import TyphoonError

LOGGER = logging.getLogger(__name__)
DEFAULT_PORT = 51826
POLL_INTERVAL = 10
MAX_STATE_AGE = 25
TARGET_MODES = {"heat": 1, "cool": 2, "auto": 3}


def state_directory():
    return config_path().parent / "homekit"


def _read_settings(folder):
    try:
        result = json.loads((folder / "bridge.json").read_text())
        if not isinstance(result, dict):
            raise ValueError
        return result
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise TyphoonError("The Home bridge settings are unreadable. Restore its local backup before starting.") from None


@contextmanager
def _bridge_lock(folder):
    fd = os.open(folder / ".bridge.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TyphoonError("The Typhoon Home bridge is already running.") from None
        yield
    finally:
        os.close(fd)


def status():
    """Read local bridge status without exposing keys, setup code, or touching AC."""
    folder = state_directory()
    settings = _read_settings(folder)
    if settings is None:
        return {"configured": False, "running": False, "paired": False,
                "available": False, "lastSeen": None,
                "port": DEFAULT_PORT, "stateDirectory": str(folder)}
    running = False
    if (folder / ".bridge.lock").exists():
        try:
            with _bridge_lock(folder):
                pass
        except TyphoonError:
            running = True
    paired = False
    try:
        paired = bool(json.loads((folder / "pairing.json").read_text()).get("paired_clients"))
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError):
        raise TyphoonError("The Home bridge pairing file is unreadable. Restore its local backup.") from None
    identity = DeviceIdentity(**settings["device"])
    last_seen = None
    online = False
    try:
        health = json.loads((folder / "health.json").read_text())
        value = health.get("lastSeen")
        if type(value) in (float, int) and math.isfinite(value):
            last_seen = value
        online = health.get("online") is True and last_seen is not None and 0 <= time.time() - last_seen <= MAX_STATE_AGE
    except (OSError, ValueError, AttributeError):
        pass
    return {"configured": True, "running": running, "paired": paired,
            "available": running and online, "lastSeen": last_seen,
            "port": settings["port"], "address": settings.get("address"),
            "device": identity.to_dict(), "stateDirectory": str(folder)}


def _validated_state(result):
    if not isinstance(result, dict) or result.get("verified") is not True:
        raise TyphoonError("The air conditioner did not return verified settings.")
    state = result.get("state")
    if not isinstance(state, dict) or any(key not in state for key in ("power", "mode", "temperature", "display")):
        raise TyphoonError("The air conditioner returned incomplete settings.")
    if type(state["power"]) is not bool or type(state["display"]) is not bool:
        raise TyphoonError("The air conditioner returned unsupported switch values.")
    temperature = state["temperature"]
    if type(temperature) not in (float, int) or not math.isfinite(temperature) or not 16 <= temperature <= 32:
        raise TyphoonError("The air conditioner returned an unsupported target temperature.")
    if state["mode"] not in ("auto", "heat", "cool", "dry", "fan"):
        raise TyphoonError("The air conditioner returned an unsupported operating mode.")
    return dict(state)


def thermostat_values(state):
    """Home thermostat modes cannot express AUX's dry/fan-only operating modes."""
    ambient = state.get("ambientTemperature")
    if type(ambient) not in (float, int) or not math.isfinite(ambient) or not -40 <= ambient <= 100:
        raise CharacteristicError("The AC has not reported a room temperature.")
    if state["power"] and state["mode"] not in TARGET_MODES:
        raise CharacteristicError("Dry and fan-only modes are controlled in Typhoon.")
    target = TARGET_MODES[state["mode"]] if state["power"] else 0
    # The AUX state supplies operating mode, not an independent compressor sensor.
    # Auto has no observable current heat/cool direction, so its current state is
    # idle (0); the target still correctly reports Auto (3).
    current = {"heat": 1, "cool": 2}.get(state["mode"], 0) if state["power"] else 0
    return {"CurrentTemperature": ambient, "TargetTemperature": state["temperature"],
            "CurrentHeatingCoolingState": current, "TargetHeatingCoolingState": target,
            "TemperatureDisplayUnits": 0}


def thermostat_changes(values):
    changes = {}
    if "TargetHeatingCoolingState" in values:
        mode = values["TargetHeatingCoolingState"]
        if type(mode) is not int or mode not in (0, 1, 2, 3):
            raise CharacteristicError("Unsupported thermostat mode.")
        changes["power"] = mode != 0
        if mode:
            changes["mode"] = {1: "heat", 2: "cool", 3: "auto"}[mode]
    if "TargetTemperature" in values:
        value = values["TargetTemperature"]
        if type(value) not in (int, float) or not math.isfinite(value) or not 16 <= value <= 32 or value * 2 != round(value * 2):
            raise CharacteristicError("Temperature must be 16–32 °C in half-degree steps.")
        changes["temperature"] = value
    return changes


class ACBackend:
    """Serializes local reads/writes with the CLI, GUI, and other HAP requests."""

    def __init__(self, identity, *, factory=Controller, lock=device_lock, clock=time.monotonic):
        self.identity = identity
        self.factory = factory
        self.device_lock = lock
        self.clock = clock
        self.state = None
        self.updated_at = 0
        self.online = False
        self._lock = threading.RLock()
        self.listeners = []

    @property
    def available(self):
        return self.online and self.state is not None and self.clock() - self.updated_at <= MAX_STATE_AGE

    def _perform(self, changes=None):
        with self._lock:
            try:
                with self.device_lock(timeout=3):
                    controller = self.factory(self.identity, timeout=3)
                    result = controller.status() if changes is None else controller.set(changes)
                state = _validated_state(result)
                if changes is not None and any(state.get(key) != value for key, value in changes.items()):
                    raise TyphoonError("The Home command did not match the AC's fresh readback.")
            except Exception:
                self.online = False
                for callback in self.listeners:
                    callback()
                # Vendor messages and authentication details must not enter HAP logs.
                raise TyphoonError("The AC could not confirm its settings. Refresh it in Typhoon.") from None
            self.state, self.updated_at, self.online = state, self.clock(), True
            for callback in self.listeners:
                callback()
            return dict(state)

    def refresh(self):
        return self._perform()

    def write(self, changes):
        return self._perform(changes)


class ACBaseAccessory(Accessory):
    def __init__(self, driver, name, backend, aid):
        super().__init__(driver, name, aid=aid)
        self.backend = backend
        self.set_info_service(manufacturer="Typhoon", model="Tornado SMART-22X SQ bridge",
                              serial_number=f"{backend.identity.mac.replace(':', '')}-{aid}", firmware_revision="0.1.0")
        backend.listeners.append(self.changed)

    @property
    def available(self):
        return self.backend.available

    def changed(self):
        # Polling runs in a worker; HAP notifications belong on its event loop.
        self.driver.loop.call_soon_threadsafe(self.apply_state)

    def require_available(self):
        if not self.available:
            raise CharacteristicError("The air conditioner is not responding.")

    def write(self, changes):
        try:
            self.backend.write(changes)
            self.apply_state()
        except TyphoonError:
            # HAP-python tentatively changes writable values before callbacks.
            # Restore the last actual values, then return communication failure.
            self.apply_state()
            raise CharacteristicError("The air conditioner did not confirm this change.") from None


class ACThermostat(ACBaseAccessory):
    category = CATEGORY_THERMOSTAT

    def __init__(self, driver, backend):
        super().__init__(driver, backend.identity.name, backend, aid=2)
        self.thermostat = self.add_preload_service("Thermostat")
        self.thermostat.is_primary_service = True
        self.thermostat.configure_char("TargetTemperature", properties={"minValue": 16, "maxValue": 32, "minStep": 0.5})
        self.thermostat.configure_char("TemperatureDisplayUnits", valid_values={"Celsius": 0})
        for char in self.thermostat.characteristics:
            char.getter_callback = lambda name=char.display_name: self.read(name)
        self.thermostat.setter_callback = self.set_values
        self.apply_state()

    @property
    def available(self):
        if not self.backend.available:
            return False
        try:
            thermostat_values(self.backend.state)
            return True
        except CharacteristicError:
            return False

    def read(self, name):
        self.require_available()
        return thermostat_values(self.backend.state)[name]

    def apply_state(self):
        if self.backend.state is None:
            return
        try:
            values = thermostat_values(self.backend.state)
        except CharacteristicError:
            return
        for name, value in values.items():
            self.thermostat.get_characteristic(name).set_value(value)

    def set_values(self, values):
        changes = thermostat_changes(values)
        if changes:
            self.write(changes)


class ACDisplay(ACBaseAccessory):
    category = CATEGORY_SWITCH

    def __init__(self, driver, backend):
        super().__init__(driver, "AC display", backend, aid=3)
        self.switch = self.add_preload_service("Switch")
        self.switch.configure_char("On", getter_callback=self.read, setter_callback=self.set_value)
        self.apply_state()

    def read(self):
        self.require_available()
        return self.backend.state["display"]

    def apply_state(self):
        if self.backend.state is not None:
            self.switch.get_characteristic("On").set_value(self.backend.state["display"])

    def set_value(self, value):
        if type(value) is not bool:
            raise CharacteristicError("Display must be on or off.")
        self.write({"display": value})


class ACBridge(Bridge):
    def __init__(self, driver, backend):
        super().__init__(driver, "Typhoon Home Bridge")
        self.backend = backend
        self.add_accessory(ACThermostat(driver, backend))
        self.add_accessory(ACDisplay(driver, backend))

    @Accessory.run_at_interval(POLL_INTERVAL)
    async def run(self):
        try:
            await asyncio.to_thread(self.backend.refresh)
        except TyphoonError:
            LOGGER.warning("Air conditioner unavailable; Home will report No Response.")


def _pin_settings(folder, identity, address, port):
    settings = _read_settings(folder)
    if settings:
        pinned = DeviceIdentity(**settings["device"])
        if (pinned.mac, pinned.devtype) != (identity.mac, identity.devtype):
            raise TyphoonError("This Home bridge is paired with another air conditioner. Restore its original Typhoon device selection.")
    else:
        # HAP forbids trivial/repeated setup codes.
        while True:
            digits = "".join(secrets.choice("0123456789") for _ in range(8))
            if len(set(digits)) > 1 and digits not in {"12345678", "87654321"}:
                break
        settings = {"version": 1, "pincode": f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"}
    settings.update(device=identity.to_dict(), address=address, port=port)
    save_json(settings, folder / "bridge.json")
    return settings


async def _close_driver_resources(driver):
    """Release even partially initialized HAP resources after a startup failure."""
    driver.stop_event.set()
    if driver.aio_stop_event is not None:
        driver.aio_stop_event.set()
    if driver.advertiser is not None:
        if driver.mdns_service_info is not None:
            with suppress(Exception):
                await driver.advertiser.async_unregister_service(driver.mdns_service_info)
        with suppress(Exception):
            await driver.advertiser.async_close()
    server = driver.http_server.server
    if server is not None:
        with suppress(Exception):
            driver.http_server.async_stop()
        with suppress(Exception):
            await server.wait_closed()
    if driver.accessory is not None:
        with suppress(Exception):
            await driver.async_add_job(driver.accessory.stop)


def _run_driver(driver):
    """Run HAP without its removed Python 3.14 child-watcher compatibility code.

    Await startup explicitly so bind/mDNS failures reach the invoking command,
    instead of leaving an idle process that appears to be a running bridge.
    """
    loop = driver.loop
    try:
        loop.run_until_complete(driver.async_start())
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        raise TyphoonError("The Home bridge could not start or run. Check this Mac's Wi-Fi address and whether its Home bridge port is already in use.") from None
    finally:
        # SIGTERM uses HAP's normal async_stop. Other exits, including partially
        # completed startup, need guarded cleanup rather than async_stop's
        # assumptions that every component was successfully initialized.
        if not driver.stop_event.is_set():
            loop.run_until_complete(_close_driver_resources(driver))
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        if driver.executor is not None:
            driver.executor.shutdown(wait=True, cancel_futures=True)
        loop.close()


def start(*, address=None, port=DEFAULT_PORT):
    """Blocking foreground bridge. It performs real AC reads only when invoked."""
    # Connection events help distinguish LAN reachability from AC failures.
    # Never enable HAP debug logging: it can include pairing and payload data.
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("pyhap.hap_protocol").setLevel(logging.INFO)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise TyphoonError("Choose a Home bridge port between 1024 and 65535.")
    identity = load_config()
    if identity is None:
        raise TyphoonError("First select and connect to the local AC in Typhoon.")
    if address is None:
        # Route selection only; connect on an unbound UDP socket sends no packet.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((identity.host, identity.port))
            address = probe.getsockname()[0]
    try:
        parsed = ipaddress.IPv4Address(address)
        if parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast:
            raise ValueError
    except ValueError:
        raise TyphoonError("Use this Mac's local Wi-Fi IPv4 address for the Home bridge.") from None
    folder = state_directory()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    with _bridge_lock(folder):
        settings = _pin_settings(folder, identity, str(parsed), port)
        for filename in ("bridge.json", "pairing.json"):
            if (folder / filename).exists():
                os.chmod(folder / filename, 0o600)
        backend = ACBackend(identity)
        last_seen = None

        def record_health():
            nonlocal last_seen
            if backend.available:
                last_seen = time.time()
            save_json({"online": backend.available, "lastSeen": last_seen}, folder / "health.json")

        backend.listeners.append(record_health)
        backend.refresh()  # Refuse to advertise placeholder temperatures or switches.
        thermostat_values(backend.state)
        old_umask = os.umask(0o077)
        try:
            driver = AccessoryDriver(address=str(parsed), port=port, persist_file=str(folder / "pairing.json"),
                                     pincode=settings["pincode"].encode("ascii"))
            driver.add_accessory(accessory=ACBridge(driver, backend))
            settings["setupURI"] = driver.accessory.xhm_uri()
            save_json(settings, folder / "bridge.json")
            signal.signal(signal.SIGTERM, driver.signal_handler)
            # HAP-python prints the pairing code/QR locally. Never persist stdout
            # to this repository or include it in public status/diagnostic output.
            _run_driver(driver)
        finally:
            os.umask(old_umask)
