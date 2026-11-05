"""Read-preserve-write-read control over the Broadlink AUX protocol."""

import math
import time

import broadlink

from .config import DeviceIdentity
from .errors import TyphoonError
from .protocol import TornadoHVAC

MODES = {"auto": TornadoHVAC.Mode.AUTO, "cool": TornadoHVAC.Mode.COOL,
         "dry": TornadoHVAC.Mode.DRY, "heat": TornadoHVAC.Mode.HEAT,
         "fan": TornadoHVAC.Mode.FAN}
FANS = {"auto": broadlink.hvac.Speed.AUTO, "low": broadlink.hvac.Speed.LOW,
        "medium": broadlink.hvac.Speed.MID, "high": broadlink.hvac.Speed.HIGH}
STATE_FIELDS = ("power", "target_temp", "mode", "speed", "preset", "swing_h", "swing_v",
                "sleep", "ifeel", "display", "health", "clean", "mildew")


def make_device(identity: DeviceIdentity, timeout=5):
    return TornadoHVAC((identity.host, identity.port), bytes.fromhex(identity.mac.replace(":", "")),
                       identity.devtype, timeout=timeout, name=identity.name)


def validate_changes(changes: dict) -> dict:
    allowed = {"power", "temperature", "mode", "fan", "display", "swing"}
    if not changes or set(changes) - allowed:
        raise TyphoonError("Specify at least one supported change: power, temperature, mode, fan, display, or swing.")
    for key, value in changes.items():
        if key in {"power", "display", "swing"} and not isinstance(value, bool):
            raise TyphoonError(f"{key} must be on or off.")
        if key == "temperature":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 16 <= value <= 32 or value * 2 != round(value * 2):
                raise TyphoonError("Temperature must be 16–32 °C in 0.5 °C steps.")
        if key == "mode" and value not in MODES:
            raise TyphoonError("Mode must be auto, cool, dry, heat, or fan.")
        if key == "fan" and value not in FANS:
            raise TyphoonError("Fan must be auto, low, medium, or high.")
    return changes


def merge_state(current: dict, changes: dict) -> dict:
    validate_changes(changes)
    missing = set(STATE_FIELDS) - current.keys()
    if missing:
        raise TyphoonError("The device returned an incomplete state; refusing to overwrite unknown settings.")
    result = {key: current[key] for key in STATE_FIELDS}
    for key, value in changes.items():
        if key == "temperature":
            result["target_temp"] = value
        elif key == "mode":
            result["mode"] = MODES[value]
        elif key == "fan":
            result["speed"] = FANS[value]
        elif key == "swing":
            result["swing_v"] = broadlink.hvac.SwVert.ON if value else broadlink.hvac.SwVert.OFF
        else:
            result[key] = value
    # AUX reports preset and requested fan independently (turbo can retain a
    # low requested fan). Preserve both; verify the actual accepted state later.
    return result


def public_state(raw: dict, ambient=None) -> dict:
    return {
        "power": bool(raw["power"]), "temperature": raw["target_temp"],
        "ambientTemperature": ambient,
        "mode": next(name for name, value in MODES.items() if value == raw["mode"]),
        "fan": next(name for name, value in FANS.items() if value == raw["speed"]),
        "display": bool(raw["display"]), "swing": raw["swing_v"] == broadlink.hvac.SwVert.ON,
    }


def read_ambient(device):
    try:
        value = device.get_ac_info().get("ambient_temp")
        return float(value) if value is not None and math.isfinite(value) else None
    except Exception:
        # Optional sensor support must not hide a successfully read main state.
        return None


class Controller:
    def __init__(self, identity: DeviceIdentity, timeout=5, factory=make_device):
        self.identity = identity
        self.device = factory(identity, timeout)

    def connect(self):
        # Discovery's lock bit can be stale or firmware-specific. Only the
        # current authentication response determines whether access works.
        try:
            authenticated = self.device.auth()
        except (broadlink.exceptions.AuthenticationError, broadlink.exceptions.AuthorizationError) as exc:
            raise TyphoonError("The Wi-Fi module refused local authentication. Check that Tornado WiFi 3 can currently control it. Its firmware or account binding may restrict local access.",
                               reportedLocked=self.identity.locked, detail=str(exc)) from exc
        if authenticated is False:
            raise TyphoonError("The Wi-Fi module did not accept local authentication.",
                               reportedLocked=self.identity.locked)

    def status(self):
        self.connect()
        raw = self.device.get_state()
        return {"device": self.identity.to_dict(), "state": public_state(raw, read_ambient(self.device)), "verified": True}

    def set(self, changes: dict, readback_attempts=3, readback_delay=0.25):
        validate_changes(changes)
        self.connect()
        before = self.device.get_state()
        target = merge_state(before, changes)
        # The command response is intentionally not accepted as verification.
        try:
            self.device.set_state(**target)
        except Exception as exc:
            raise TyphoonError("The device did not acknowledge the command. It may have received the change; refresh its status before trying again.",
                               device=self.identity.to_dict(), verified=False, detail=str(exc)) from exc
        observed = None
        mismatches = {}
        for attempt in range(readback_attempts):
            if attempt:
                time.sleep(readback_delay)
            try:
                observed = public_state(self.device.get_state())
            except Exception as exc:
                raise TyphoonError("The command was sent, but its verification read failed. Refresh the device status before trying again.",
                                   device=self.identity.to_dict(), state=observed, verified=False, detail=str(exc)) from exc
            mismatches = {key: {"requested": value, "actual": observed[key]}
                          for key, value in changes.items() if observed[key] != value}
            if not mismatches:
                observed["ambientTemperature"] = read_ambient(self.device)
                return {"device": self.identity.to_dict(), "state": observed, "verified": True}
        raise TyphoonError("The command was sent, but a fresh device read did not confirm every requested setting.",
                           device=self.identity.to_dict(), state=observed, verified=False, mismatches=mismatches)
