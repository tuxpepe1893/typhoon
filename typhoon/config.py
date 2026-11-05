"""Nonsecret, atomic configuration shared by the app and command line."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
import time

from .errors import TyphoonError

SUPPORTED_TYPE = 0x4E2A


def config_path() -> Path:
    return Path.home() / "Library/Application Support/Typhoon/device.json"


def selection_path() -> Path:
    return config_path().with_name("connection.json")


@dataclass(frozen=True)
class DeviceIdentity:
    host: str
    mac: str
    devtype: int = SUPPORTED_TYPE
    name: str = "Tornado air conditioner"
    locked: bool = False
    port: int = 80

    def __post_init__(self):
        try:
            host = str(ipaddress.IPv4Address(self.host))
        except (ValueError, TypeError) as exc:
            raise TyphoonError("Host must be the air conditioner's IPv4 address.") from exc
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", self.mac):
            raise TyphoonError("MAC must contain six hex pairs, for example aa:bb:cc:dd:ee:ff.")
        if self.devtype != SUPPORTED_TYPE:
            raise TyphoonError(f"Unsupported device type {self.devtype!r}; this controller supports Tornado/AUX 0x4e2a.")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise TyphoonError("Port must be an integer between 1 and 65535.")
        if not isinstance(self.name, str) or not self.name.strip() or any(ord(c) < 32 for c in self.name):
            raise TyphoonError("Device name must be nonempty text without control characters.")
        if not isinstance(self.locked, bool):
            raise TyphoonError("Device lock status must be true or false.")
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "mac", self.mac.lower().replace("-", ":"))
        object.__setattr__(self, "name", self.name.strip())

    def to_dict(self):
        return asdict(self)


def load_config(path: Path | None = None) -> DeviceIdentity | None:
    path = path or config_path()
    try:
        data = json.loads(path.read_text())
        return DeviceIdentity(**data)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        raise TyphoonError(f"Cannot read device configuration: {exc}. Run configure to replace it.") from exc


def save_config(device: DeviceIdentity, path: Path | None = None) -> None:
    save_json(device.to_dict(), path or config_path())


def save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".device-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sanitize_cloud_device(device: dict) -> dict:
    """Only this allowlist may enter public JSON or nonsecret configuration."""
    fields = ("id", "name", "mac", "host")
    if not isinstance(device, dict) or any(not isinstance(device.get(key), str) for key in fields):
        raise TyphoonError("The cloud returned incomplete public device information.")
    if not device["id"] or not device["name"] or device["host"] not in {"Tornado cloud (USA)", "Tornado cloud (EU)"}:
        raise TyphoonError("The cloud returned invalid public device information.")
    return {**{key: device[key] for key in fields}, "transport": "cloud"}


def load_cloud_selection() -> dict | None:
    try:
        value = json.loads(selection_path().read_text())
        if value.get("transport") != "cloud":
            raise ValueError("unsupported transport")
        return sanitize_cloud_device(value["device"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise TyphoonError("Cannot read the saved connection. Configure a local device or select a cloud device again.") from exc


def save_cloud_selection(device: dict) -> None:
    save_json({"transport": "cloud", "device": sanitize_cloud_device(device)}, selection_path())


def clear_cloud_selection() -> None:
    selection_path().unlink(missing_ok=True)


@contextmanager
def device_lock(timeout=10):
    """Prevent independent GUI/CLI processes from invalidating each other's auth."""
    folder = config_path().parent
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(folder / ".control.lock", os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TyphoonError("Another Typhoon command is communicating with the air conditioner. Try again shortly.")
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)
