"""JSON command-line boundary used by the native macOS application."""

import argparse
from contextlib import contextmanager
import getpass
import json
import math
import platform
import sys

from broadlink import exceptions as broadlink_errors

from . import __version__
from .config import (DeviceIdentity, SUPPORTED_TYPE, clear_cloud_selection, config_path,
                     device_lock, load_cloud_selection, load_config, sanitize_cloud_device,
                     save_cloud_selection, save_config)
from .controller import Controller, FANS, MODES, validate_changes
from .discovery import DISCOVERY_PORTS, discover, local_interfaces
from .errors import TyphoonError


class Parser(argparse.ArgumentParser):
    def error(self, message):
        if "--password" in message:
            raise TyphoonError("Passwords cannot be passed as command arguments. Use cloud-login --stdin or the interactive password prompt.")
        raise TyphoonError(message)


def parse_devtype(value):
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("device type must be an integer or hex value") from exc


def parser():
    root = Parser(description="Control Tornado/AUX air conditioners locally or through Tornado cloud. All command results are JSON.")
    root.add_argument("--json", action="store_true", help="emit JSON (also the default)")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True, parser_class=Parser)
    for name in ("discover", "configure", "status", "set", "config", "doctor",
                 "cloud-login", "cloud-devices", "cloud-select", "cloud-logout", "homekit"):
        command = commands.add_parser(name)
        command.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
        if name == "homekit":
            command.add_argument("action", choices=["status", "start", "enable", "disable", "pairing"])
            command.add_argument("--address", help="This Mac's local IPv4 address (foreground start only)")
            command.add_argument("--port", type=int, default=51826, help="Home bridge port (foreground start only)")
        if name in {"discover", "status", "set"}:
            command.add_argument("--timeout", type=float, default=5, help="local network request timeout in seconds (default 5)")
        if name in {"discover", "configure", "status", "set"}:
            command.add_argument("--host", required=name == "configure")
        if name in {"configure", "status", "set"}:
            command.add_argument("--mac", required=name == "configure")
            command.add_argument("--devtype", type=parse_devtype)
            command.add_argument("--port", type=int)
        if name == "configure":
            command.add_argument("--name", default="Tornado air conditioner")
        if name == "cloud-login":
            command.add_argument("--stdin", action="store_true", help="read login JSON privately from stdin")
            command.add_argument("--email", help="Tornado WiFi 3 account email; password is prompted privately")
            command.add_argument("--region", choices=["usa", "eu"], default="usa")
        if name == "cloud-select":
            command.add_argument("--id", required=True, help="public ID returned by cloud-devices")
        if name == "set":
            command.add_argument("--expected-device", help="reject changes if the selected identity differs: local:MAC or cloud:ID")
            command.add_argument("--power", choices=["on", "off"])
            command.add_argument("--temperature", type=float)
            command.add_argument("--mode", choices=list(MODES))
            command.add_argument("--fan", choices=list(FANS))
            command.add_argument("--display", choices=["on", "off"])
            command.add_argument("--swing", choices=["on", "off"])
    return root


def cloud_services():
    # Keep local control available independently of account state. These
    # explicit imports are also visible to the PyInstaller dependency scanner.
    from . import cloud, keychain
    return cloud, keychain


@contextmanager
def cloud_error_boundary():
    try:
        yield
    except TyphoonError:
        # CloudError and Keychain errors provide deliberate, redacted messages.
        raise
    except Exception as exc:
        # Unexpected provider/HTTP exceptions can contain request credentials.
        raise TyphoonError("The cloud connection failed unexpectedly. Try again, or sign in again from the Mac app.") from exc


def active_device():
    selected = load_cloud_selection()
    if selected:
        return selected
    local = load_config()
    return local.to_dict() if local else None


def load_cloud_account(keychain):
    account = keychain.load_secret("cloud")
    if not isinstance(account, dict) or not isinstance(account.get("session"), dict) or not isinstance(account.get("devices"), list):
        raise TyphoonError("No cloud account is signed in. Run cloud-login or sign in from the Mac app.")
    return account


def public_cloud_devices(cloud, devices):
    return [sanitize_cloud_device(cloud.public_device(device)) for device in devices]


def login_credentials(args):
    if args.stdin:
        if args.email:
            raise TyphoonError("Use either --stdin or --email for cloud login.")
        try:
            credentials = json.loads(sys.stdin.read(65537))
        except (ValueError, OSError) as exc:
            raise TyphoonError("Cloud login expects stdin JSON with email, password, and region.") from exc
        if not isinstance(credentials, dict):
            raise TyphoonError("Cloud login expects stdin JSON with email, password, and region.")
        email, password, region = (credentials.get("email"), credentials.get("password"), credentials.get("region", "usa"))
    else:
        if not args.email:
            raise TyphoonError("Use cloud-login --email EMAIL for a private password prompt, or --stdin from the Mac app.")
        if not sys.stdin.isatty():
            raise TyphoonError("Interactive login requires a terminal. Use --stdin for the Mac app's private login input.")
        email, region = args.email, args.region
        password = getpass.getpass("Tornado WiFi 3 password: ")
    if not isinstance(email, str) or not email.strip() or not isinstance(password, str) or not password:
        raise TyphoonError("Both account email and password are required.")
    if region not in {"usa", "eu"}:
        raise TyphoonError("Cloud region must be usa or eu.")
    return email.strip(), password, region


def dispatch_cloud_account(args):
    credentials = login_credentials(args) if args.command == "cloud-login" else None
    cloud, keychain = cloud_services()
    with device_lock():
        if args.command == "cloud-logout":
            keychain.delete_secret("cloud")
            clear_cloud_selection()
            return {"device": active_device(), "signedIn": False}
        if args.command == "cloud-login":
            email, password, region = credentials
            try:
                session = cloud.login(email, password, region)
            finally:
                # The password is only an in-memory request input. Neither
                # the Keychain object nor the command result contains it.
                password = None
                credentials = None
            devices = cloud.discover(session)
            public = public_cloud_devices(cloud, devices)
            account = {"session": session, "devices": devices}
            previous = load_cloud_selection()
            keychain.save_secret("cloud", account)
            if previous:
                matching = next((device for device in public if device["id"] == previous["id"]), None)
                if matching:
                    save_cloud_selection(matching)
                else:
                    clear_cloud_selection()
            return {"devices": public, "signedIn": True}
        account = load_cloud_account(keychain)
        if args.command == "cloud-devices":
            devices = cloud.discover(account["session"])
            public = public_cloud_devices(cloud, devices)
            keychain.save_secret("cloud", {"session": account["session"], "devices": devices})
            previous = load_cloud_selection()
            if previous:
                matching = next((device for device in public if device["id"] == previous["id"]), None)
                if matching:
                    save_cloud_selection(matching)
            return {"devices": public, "signedIn": True}
        if args.command == "cloud-select":
            public = public_cloud_devices(cloud, account["devices"])
            selected = next((device for device in public if device["id"] == args.id), None)
            if not selected:
                raise TyphoonError("That cloud device is not in the signed-in account. Run cloud-devices to refresh the list.")
            save_cloud_selection(selected)
            return {"device": selected, "verified": False}
        raise TyphoonError("Unsupported cloud account command.")


def cloud_control(args, selected, changes):
    cloud, keychain = cloud_services()
    account = load_cloud_account(keychain)
    device = next((device for device in account["devices"]
                   if sanitize_cloud_device(cloud.public_device(device))["id"] == selected["id"]), None)
    if not device:
        raise TyphoonError("The selected device is no longer in the saved cloud account. Run cloud-devices and select it again.")
    result = (cloud.get_status(account["session"], device) if args.command == "status"
              else cloud.set_state(account["session"], device, changes))
    # Never forward a session or raw device object through the JSON boundary.
    state = result.get("state")
    fields = ("power", "temperature", "ambientTemperature", "mode", "fan", "display", "swing")
    if not isinstance(state, dict) or any(field not in state for field in fields):
        raise TyphoonError("The cloud returned incomplete device state.")
    valid_number = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if (not isinstance(state["power"], bool) or not valid_number(state["temperature"])
            or state["mode"] not in MODES or state["fan"] not in {*FANS, "turbo", "silent"}
            or any(state[key] is not None and not isinstance(state[key], bool) for key in ("display", "swing"))
            or state["ambientTemperature"] is not None and not valid_number(state["ambientTemperature"])):
        raise TyphoonError("The cloud returned invalid device state.")
    verified = result.get("verified") is True and result.get("verification") == "cloud-readback"
    return {"device": sanitize_cloud_device(cloud.public_device(device)),
            "state": {field: state[field] for field in fields},
            "verified": verified, "verification": "cloud-readback" if verified else "unverified"}


def requested_changes(args):
    changes = {}
    for field in ("power", "temperature", "mode", "fan", "display", "swing"):
        value = getattr(args, field)
        if value is not None:
            changes[field] = value == "on" if field in {"power", "display", "swing"} else value
    return validate_changes(changes)


def check_expected_device(args, fingerprint):
    expected = getattr(args, "expected_device", None)
    if expected is not None and expected != fingerprint:
        raise TyphoonError("The selected air conditioner changed. Refresh its status before sending another command.",
                           code="device_changed", verified=False)


def resolve_device(args):
    # Supplying a host and MAC is sufficient even if an old config is invalid.
    saved = None if args.host and args.mac else load_config()
    if not saved and not (args.host and args.mac):
        raise TyphoonError("No air conditioner configured. Run discover, then configure --host IP --mac MAC, or supply both --host and --mac.")
    values = saved.to_dict() if saved else {}
    for field in ("host", "mac", "devtype", "port"):
        value = getattr(args, field)
        if value is not None:
            values[field] = value
    return DeviceIdentity(**values)


def dispatch(args):
    if hasattr(args, "timeout") and not 0 < args.timeout <= 60:
        raise TyphoonError("Timeout must be greater than zero and at most 60 seconds.")
    if args.command == "homekit":
        # Explicit imports let PyInstaller collect the complete HAP bridge.
        from . import homekit, homekit_service
        if args.action == "start":
            homekit.start(address=args.address, port=args.port)
            return {"homekit": homekit_service.status()}
        action = {"status": homekit_service.status, "enable": homekit_service.enable,
                  "disable": homekit_service.disable, "pairing": homekit_service.pairing}[args.action]
        return {"homekit": action()}
    if args.command == "config":
        return {"device": active_device()}
    if args.command.startswith("cloud-"):
        with cloud_error_boundary():
            return dispatch_cloud_account(args)
    if args.command == "configure":
        device = DeviceIdentity(host=args.host, mac=args.mac, devtype=args.devtype if args.devtype is not None else SUPPORTED_TYPE,
                                name=args.name, port=args.port if args.port is not None else 80)
        with device_lock():
            save_config(device)
            clear_cloud_selection()
        return {"device": device.to_dict(), "verified": False}
    if args.command == "discover":
        devices = discover(timeout=args.timeout, host=args.host)
        result = {"devices": [device.to_dict() for device in devices]}
        if not devices:
            result["hint"] = "No compatible air conditioner replied. Connect this Mac to the same LAN as the air conditioner, allow Local Network access in macOS, and check that the router does not isolate Wi-Fi clients. If you know its IP, try discover --host IP."
        return result
    if args.command == "doctor":
        device = active_device()
        return {"version": __version__, "platform": platform.platform(),
                "configPath": str(config_path()), "device": device,
                "interfaces": local_interfaces(), "discoveryPorts": list(DISCOVERY_PORTS),
                "hints": ["The Mac and air conditioner need to share the same local network; 5 GHz on the Mac is fine when bridged to 2.4 GHz.",
                          "Allow Typhoon Local Network access in macOS System Settings > Privacy & Security.",
                          "Check that the router does not isolate Wi-Fi clients. Some Wi-Fi module firmware or account bindings may restrict local access.",
                          "No Wi-Fi password or Tornado account password is needed for local control."]}
    changes = requested_changes(args) if args.command == "set" else None
    with device_lock():
        explicit_local = any(getattr(args, field) is not None for field in ("host", "mac", "devtype", "port"))
        selected = None if explicit_local else load_cloud_selection()
        if selected:
            check_expected_device(args, "cloud:" + selected["id"])
            with cloud_error_boundary():
                return cloud_control(args, selected, changes)
        device = resolve_device(args)
        check_expected_device(args, "local:" + device.mac)
        controller = Controller(device, args.timeout)
        if args.command == "status":
            return controller.status()
        return controller.set(changes)


def main(argv=None):
    try:
        result = {"ok": True, **dispatch(parser().parse_args(argv))}
        exit_code = 0
    except TyphoonError as exc:
        result = {"ok": False, "error": str(exc), **exc.details}
        exit_code = 1
    except (broadlink_errors.AuthenticationError, broadlink_errors.AuthorizationError) as exc:
        result = {"ok": False, "error": "The device refused local authentication. Check that Tornado WiFi 3 can currently control it; firmware or account binding may restrict local access.", "detail": str(exc)}
        exit_code = 1
    except (broadlink_errors.NetworkTimeoutError, TimeoutError) as exc:
        result = {"ok": False, "error": "The air conditioner did not respond. Check its power, IP address, the Mac's Local Network permission, and that both devices share the same LAN.", "detail": str(exc)}
        exit_code = 1
    except KeyboardInterrupt:
        result = {"ok": False, "error": "Cancelled."}
        exit_code = 130
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        exit_code = 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
