#!/usr/bin/env python3
"""Install/manage the current user's Typhoon login service."""
import argparse
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import tempfile

LABEL = "com.tuxpepe1893.typhoon.desktop"
APP = next((path for path in [Path.home() / "Applications/Typhoon.app",
                             Path("/Applications/Typhoon.app")] if path.is_dir()),
           Path.home() / "Applications/Typhoon.app")
EXECUTABLE = APP / "Contents/MacOS/Typhoon"
AGENT = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
TARGET = f"gui/{os.getuid()}/{LABEL}"


def launchctl(*args, check=True):
    return subprocess.run(["/bin/launchctl", *args], capture_output=True,
                          text=True, check=check, timeout=15)


def loaded():
    return launchctl("print", TARGET, check=False).returncode == 0


def enable():
    with (APP / "Contents/Info.plist").open("rb") as stream:
        if plistlib.load(stream).get("CFBundleIdentifier") != "com.tuxpepe1893.typhoon":
            raise RuntimeError("Refusing to manage an unrelated application")
    if not os.access(EXECUTABLE, os.X_OK):
        raise RuntimeError("Install Typhoon.app before enabling its login service")
    log_dir = Path.home() / "Library/Logs/Typhoon"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / "desktop.log"
    fd = os.open(log, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)
    os.close(fd)
    job = {
        "Label": LABEL,
        "ProgramArguments": [str(EXECUTABLE), "--background"],
        "RunAtLoad": True,
        # Recover from crashes/signals; an intentional Quit remains respected.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "LimitLoadToSessionType": "Aqua",
        "ProcessType": "Interactive",
        "WorkingDirectory": str(Path.home()),
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    if loaded():
        launchctl("bootout", TARGET)
    # Stop only this user's exact installed executable before launchd takes
    # ownership, avoiding two GUI instances when enabling an already-open app.
    processes = subprocess.check_output(
        ["/bin/ps", "-U", str(os.getuid()), "-o", "pid=,comm="], text=True)
    for line in processes.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1] == str(EXECUTABLE):
            try:
                os.kill(int(parts[0]), signal.SIGTERM)
            except ProcessLookupError:
                pass
    AGENT.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".typhoon-desktop-", dir=AGENT.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(job, stream)
        os.replace(temporary, AGENT)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    launchctl("enable", TARGET)
    launchctl("bootstrap", f"gui/{os.getuid()}", str(AGENT))
    print("Typhoon starts hidden at login and restarts after a crash.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["enable", "disable", "status"])
    action = parser.parse_args().action
    if action == "enable":
        enable()
    elif action == "disable":
        if loaded():
            launchctl("bootout", TARGET)
        AGENT.unlink(missing_ok=True)
        print("Typhoon desktop automatic startup disabled. Apple Home is unchanged.")
    else:
        print(f"Start at login: {AGENT.is_file()}; registered with launchd: {loaded()}")
        result = launchctl("print", TARGET, check=False)
        for line in result.stdout.splitlines():
            if line.strip().startswith(("state =", "pid =", "runs =", "last exit code =")):
                print(line.strip())


if __name__ == "__main__":
    main()
