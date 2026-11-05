"""User-scoped launchd management for the installed Typhoon Home bridge.

Only ``pairing()`` returns the local setup secret. Status, enable, and disable
never include it. No function runs automatically at import time.
"""

import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
import time

from . import homekit
from .config import load_config
from .errors import TyphoonError

LABEL = "com.tuxpepe1893.typhoon.homekit"


def launch_agent_path():
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def _service_target():
    return f"gui/{os.getuid()}/{LABEL}"


def _launchctl(*arguments, check=True):
    if sys.platform != "darwin":
        raise TyphoonError("The automatic Home bridge service requires macOS.")
    try:
        result = subprocess.run(["/bin/launchctl", *arguments], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise TyphoonError("macOS did not respond while managing the Typhoon Home bridge.") from None
    if check and result.returncode:
        raise TyphoonError("macOS could not update the Typhoon Home bridge service. Check Login Items in System Settings, then try again.")
    return result.returncode


def _loaded():
    return _launchctl("print", _service_target(), check=False) == 0


def _installed_backend():
    if getattr(sys, "frozen", False):
        candidates = [Path(sys.executable)]
    else:
        candidates = [Path.home() / "Applications/Typhoon.app/Contents/Resources/typhoon-backend",
                      Path("/Applications/Typhoon.app/Contents/Resources/typhoon-backend")]
    for candidate in candidates:
        # Use the installed app, never a source checkout, transient PyInstaller
        # directory, interpreter, shell wrapper, or arbitrary executable.
        if (candidate.name == "typhoon-backend" and candidate.parent.name == "Resources"
                and candidate.parent.parent.name == "Contents"
                and candidate.parent.parent.parent.name == "Typhoon.app"
                and candidate.is_file() and os.access(candidate, os.X_OK)):
            return candidate.resolve()
    raise TyphoonError("Install Typhoon.app in Applications before enabling its automatic Home bridge.")


def _write_plist(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".typhoon-homekit-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(data, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def status():
    result = homekit.status()
    result.update(enabled=launch_agent_path().is_file(), serviceLoaded=_loaded())
    return result


def enable():
    """Install and bootstrap only this user's Typhoon job; retain pairing keys."""
    identity = load_config()
    if identity is None:
        raise TyphoonError("Connect to the local air conditioner in Typhoon before enabling Apple Home.")
    folder = homekit.state_directory()
    previous = homekit._read_settings(folder)
    if previous:
        pinned = previous.get("device", {})
        if (pinned.get("mac"), pinned.get("devtype")) != (identity.mac, identity.devtype):
            raise TyphoonError("The saved Home bridge belongs to another air conditioner. Restore its original Typhoon selection.")
    executable = _installed_backend()
    path = launch_agent_path()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    error_log = folder / "bridge-error.log"
    fd = os.open(error_log, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)
    os.close(fd)
    arguments = [str(executable), "--json", "homekit", "start"]
    if previous and previous.get("port", homekit.DEFAULT_PORT) != homekit.DEFAULT_PORT:
        port = previous["port"]
        if type(port) is not int or not 1024 <= port <= 65535:
            raise TyphoonError("The saved Home bridge port is invalid.")
        arguments += ["--port", str(port)]
    job = {"Label": LABEL, "ProgramArguments": arguments,
           "WorkingDirectory": str(folder), "RunAtLoad": True, "KeepAlive": True,
           "ThrottleInterval": 10, "ProcessType": "Background",
           "LimitLoadToSessionType": "Aqua", "LowPriorityIO": True,
           "StandardOutPath": "/dev/null", "StandardErrorPath": str(error_log),
           "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"}}
    old_job = None
    try:
        with path.open("rb") as stream:
            old_job = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException):
        pass
    loaded = _loaded()
    if loaded and old_job != job:
        _launchctl("bootout", _service_target())
        loaded = False
    _write_plist(path, job)
    _launchctl("enable", _service_target())
    if not loaded:
        # bootout may return before launchd finishes removing the old job.
        # Its immediate replacement then fails with EIO (5); retry briefly.
        for attempt in range(5):
            result = _launchctl("bootstrap", f"gui/{os.getuid()}", str(path), check=False)
            if result == 0:
                break
            if result != 5 or attempt == 4:
                raise TyphoonError("macOS could not start the Typhoon Home bridge service. Check Login Items in System Settings, then try again.")
            time.sleep(0.25 * (attempt + 1))
    return status()


def disable():
    """Stop/unregister our own login job; leave identity and pairing files alone."""
    if _loaded():
        _launchctl("bootout", _service_target())
    try:
        launch_agent_path().unlink(missing_ok=True)
    except OSError:
        raise TyphoonError("The Home bridge stopped, but its login item could not be removed.") from None
    return status()


def pairing():
    """Explicitly reveal the local Home pairing secret to this Mac's user."""
    public = status()
    if not public["running"]:
        raise TyphoonError("Start the Home bridge and wait for it to connect before showing its pairing code.")
    settings = homekit._read_settings(homekit.state_directory()) or {}
    pincode, uri = settings.get("pincode"), settings.get("setupURI")
    if not isinstance(pincode, str) or not re.fullmatch(r"\d{3}-\d{2}-\d{3}", pincode):
        raise TyphoonError("The Home bridge has not created a valid pairing code yet.")
    if not isinstance(uri, str) or not re.fullmatch(r"X-HM://[0-9A-Z]{13}", uri):
        raise TyphoonError("The Home bridge is still starting. Refresh and try showing its pairing code again.")
    return {**public, "pincode": pincode, "setupURI": uri}
