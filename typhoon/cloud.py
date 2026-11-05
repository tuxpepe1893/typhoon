"""Optional Tornado WIFI 3 cloud transport.

Protocol adapted from romfreiman/tornado-aircon-custom-component (MIT).
See docs/third-party-cloud.md. This module never persists or logs credentials.
Session dictionaries AND device cookies are secrets: callers must use Keychain.

``verified`` means an online query and a separate SDK control read succeeded.
It is cloud-reported state, not independent physical confirmation. Inventory
parameters and write acknowledgements are never used as verification.
"""

import base64
import hashlib
import json
import math
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .errors import TyphoonError


SERVERS = {
    "usa": "https://app-service-usa-fd7cc04c.smarthomecs.com",
    "eu": "https://app-service-deu-f0e9ebbb.smarthomecs.de",
}
LICENSE_ID = "3c015b249dd66ef0f11f9bef59ecd737"
COMPANY_ID = "48eb1b36cf0202ab2ef07b880ecda60d"
LICENSE = (
    "PAFbJJ3WbvDxH5vvWezXN5BujETtH/iuTtIIW5CE/SeHN7oNKqnEajgljTcL0fBQQWM0XAAAAAAnBh"
    "JyhMi7zIQMsUcwR/PEwGA3uB5HLOnr+xRrci+FwHMkUtK7v4yo0ZHa+jPvb6djelPP893k7SagmffZ"
    "mOkLSOsbNs8CAqsu8HuIDs2mDQAAAAA="
)
AES_IV = bytes([234, 170, 170, 58, 187, 88, 98, 162, 25, 24, 181, 119, 29, 22, 21, 170])
# These published protocol constants are not user credentials.
PASSWORD_SALT = "4969fj#k23#"
BODY_SALT = "xgx3d*fe3478$ukx"
TIMESTAMP_SALT = "kdixkdqp54545^#*"
TIMEOUT = 15
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MODES = {"cool": 0, "heat": 1, "dry": 2, "fan": 3, "auto": 4}
FANS = {"auto": 0, "low": 1, "medium": 2, "high": 3, "turbo": 4, "silent": 5}
PARAMETERS = ["pwr", "temp", "envtemp", "ac_mode", "ac_mark", "scrdisp", "ac_vdir"]
PARAMETER_MAP = {
    "power": "pwr", "temperature": "temp", "mode": "ac_mode", "fan": "ac_mark",
    "display": "scrdisp", "swing": "ac_vdir",
}


class CloudError(TyphoonError):
    """A safe, user-facing cloud failure."""


class CloudAuthenticationError(CloudError):
    """Sign-in failed or a session must be renewed."""


class CloudOfflineError(CloudError):
    """The vendor reports the AC disconnected."""


def _json(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _region(session):
    region = session.get("region") if isinstance(session, dict) else None
    if region not in SERVERS:
        raise CloudError("Choose the USA or EU Tornado cloud region. Israeli accounts normally use USA.")
    return region


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Session headers must never follow a redirect to another server.
        return None


def _post(session, path, body=None, extra_headers=None):
    region = _region(session)
    if path != "/account/login" and not all(
        isinstance(session.get(key), str) and session[key] for key in ("userid", "loginsession")
    ):
        raise CloudAuthenticationError("Sign in to Tornado cloud again.", code="cloud_auth", verified=False)
    if not path.startswith("/") or path.startswith("//"):
        raise CloudError("Invalid cloud API endpoint.")
    headers = {
        "Content-Type": "application/x-java-serialized-object",
        "licenseId": LICENSE_ID, "lid": LICENSE_ID, "language": "en",
        "appVersion": "2.2.10.456537160",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; SM-G991B Build/SP1A.210812.016)",
        "system": "android", "appPlatform": "android",
        "loginsession": session.get("loginsession", ""), "userid": session.get("userid", ""),
        **(extra_headers or {}),
    }
    data = body if isinstance(body, bytes) else _json(body) if body is not None else b""
    request = urllib.request.Request(SERVERS[region] + path, data=data, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise CloudAuthenticationError("Tornado cloud rejected the session. Sign in again and check the region.",
                                           code="cloud_auth", verified=False) from None
        raise CloudError(f"Tornado cloud returned HTTP {exc.code}. Try again later.",
                         code="cloud_http", verified=False) from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise CloudError("Could not reach Tornado cloud securely. Check this Mac's internet connection and try again.",
                         code="cloud_connection", verified=False) from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CloudError("Tornado cloud returned an unexpectedly large response.", verified=False)
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise CloudError("Tornado cloud returned an unreadable response. Try again later.", verified=False) from None
    if not isinstance(result, dict):
        raise CloudError("Tornado cloud returned an unsupported response.", verified=False)
    return result


def _check_status(payload, *, login_request=False):
    status = payload.get("status")
    if status is None or status == 0:
        return
    code = status if type(status) is int else None
    if login_request or code == -30129:
        raise CloudAuthenticationError(
            "Tornado sign-in failed or expired. Check the email, password, and region; Israeli accounts normally use USA.",
            code="cloud_auth", vendorCode=code, verified=False,
        )
    if code == -49002:
        raise CloudError("Tornado cloud could not reach the device. Check it in Tornado WIFI 3, then sign in again to refresh its pairing.",
                         code="cloud_unreachable", vendorCode=code, verified=False)
    raise CloudError("Tornado cloud rejected the request. Refresh the device list or sign in again.",
                     code="cloud_api", vendorCode=code, verified=False)


def _event_payload(result):
    _check_status(result)
    event = result.get("event")
    if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
        raise CloudError("Tornado cloud did not return a device response.", verified=False)
    payload = event["payload"]
    _check_status(payload)
    header = event.get("header", {})
    if not isinstance(header, dict) or header.get("name") not in (None, "Response", "ReportState"):
        raise CloudError("Tornado cloud did not confirm the device request. Check the unit in Tornado WIFI 3.", verified=False)
    return payload


def login(email, password, region="usa"):
    """Return a secret session, never the supplied email/password."""
    _region({"region": region})
    if not isinstance(email, str) or not email.strip() or not isinstance(password, str) or not password:
        raise CloudAuthenticationError("Enter the email and password used in Tornado WIFI 3.", code="cloud_auth")
    timestamp = str(time.time())
    payload = _json({
        "email": email.strip(),
        "password": hashlib.sha1((password + PASSWORD_SALT).encode()).hexdigest(),
        "companyid": COMPANY_ID, "lid": LICENSE_ID,
    })
    token = hashlib.md5(payload + BODY_SALT.encode()).hexdigest()
    key = hashlib.md5((timestamp + TIMESTAMP_SALT).encode()).digest()
    # Vendor protocol zero padding adds a complete block to aligned input.
    padded = payload + bytes(16 - len(payload) % 16)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(AES_IV)).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    result = _post({"region": region}, "/account/login", encrypted, {"timestamp": timestamp, "token": token})
    _check_status(result, login_request=True)
    if result.get("status") != 0 or not all(isinstance(result.get(k), str) and result[k] for k in ("userid", "loginsession")):
        raise CloudAuthenticationError("Tornado cloud did not issue a valid login session.", code="cloud_auth", verified=False)
    return {"version": 1, "region": region, "userid": result["userid"], "loginsession": result["loginsession"]}


def public_device(device):
    """The ONLY device projection safe for public CLI output or config files."""
    return {
        "id": device["endpointId"], "name": device.get("name") or device.get("friendlyName") or "Tornado air conditioner",
        "mac": device.get("mac") or device["endpointId"],
        "host": f"Tornado cloud ({_region(device).upper()})", "transport": "cloud",
    }


def discover(session):
    """List owned and shared devices. Returned cookies/devSession are secrets."""
    region = _region(session)
    result = _post(session, "/appsync/group/member/getfamilylist")
    _check_status(result)
    families = result.get("data", {}).get("familyList") if isinstance(result.get("data"), dict) else None
    if not isinstance(families, list):
        raise CloudError("Tornado cloud did not return a family list. Check the account and region.", verified=False)
    devices = {}
    for family in families:
        if not isinstance(family, dict) or not isinstance(family.get("familyid"), str):
            raise CloudError("Tornado cloud returned an incomplete family record.", verified=False)
        family_id = family["familyid"]
        for shared in (False, True):
            path = "/appsync/group/" + ("sharedev/querylist?querytype=shared" if shared else "dev/query?action=select")
            listing = _post(session, path, {"endpointId": ""} if shared else {"pids": []}, {"familyid": family_id})
            _check_status(listing)
            data = listing.get("data")
            if not isinstance(data, dict):
                raise CloudError("Tornado cloud returned an incomplete device inventory.", verified=False)
            rows = data.get("shareFromOther", []) if shared else data.get("endpoints", [])
            if not isinstance(rows, list):
                raise CloudError("Tornado cloud returned an unsupported device inventory.", verified=False)
            for row in rows:
                endpoint = row.get("devinfo") if shared and isinstance(row, dict) else row
                if not isinstance(endpoint, dict) or not isinstance(endpoint.get("endpointId"), str) or not endpoint["endpointId"]:
                    raise CloudError("Tornado cloud returned an incomplete device identity.", verified=False)
                did = endpoint["endpointId"]
                if did not in devices:
                    # Explicit allowlist drops cached params and unnecessary personal data.
                    devices[did] = {key: endpoint[key] for key in
                                    ("endpointId", "productId", "mac", "devSession", "cookie", "devicetypeFlag") if key in endpoint}
                    devices[did].update(id=did, name=endpoint.get("friendlyName") or "Tornado air conditioner",
                                        familyId=family_id, region=region, transport="cloud")
    return list(devices.values())


def _identity(session, device):
    if not isinstance(device, dict) or _region(device) != _region(session):
        raise CloudError("This device belongs to a different cloud region. Select it again after signing in.", verified=False)
    if not all(isinstance(device.get(k), str) and device[k] for k in ("endpointId", "devSession")):
        raise CloudError("The saved device identity is incomplete. Sign in again to refresh it.", verified=False)


def _header(namespace, name, prefix):
    return {"namespace": namespace, "name": name, "interfaceVersion": "2", "senderId": "sdk",
            "messageId": f"{prefix}-{int(time.time())}"}


def _ensure_online(session, device):
    _identity(session, device)
    header = _header("DNA.QueryState", "queryState", session.get("userid", ""))
    header["messageType"] = "controlgw.batch"
    header["timestamp"] = str(int(time.time()))
    payload = _event_payload(_post(session, "/device/control/v2/querystate", {"directive": {
        "header": header,
        "payload": {"studata": [{"did": device["endpointId"], "devSession": device["devSession"]}], "msgtype": "batch"},
    }}))
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise CloudError("Tornado cloud did not report whether this device is online.", verified=False)
    row = rows[0]
    if row.get("did", row.get("endpointId", device["endpointId"])) != device["endpointId"]:
        raise CloudError("Tornado cloud returned another device's connection state.", verified=False)
    if row.get("state") in (0, "0", "off", "offline"):
        raise CloudOfflineError("Tornado cloud reports the air conditioner offline. Reconnect it in Tornado WIFI 3 on the 2.4 GHz network.",
                                code="cloud_offline", verified=False)
    if row.get("state") not in (1, "1", "on", "online"):
        raise CloudError("Tornado cloud returned an unknown device connection state.", verified=False)


def _act(session, device, act, params, vals=None):
    _identity(session, device)
    try:
        cookie = json.loads(base64.b64decode(device["cookie"], validate=True))
        cookie = cookie.get("device", cookie)
        terminal_id, aes_key = cookie["terminalid"], cookie["aeskey"]
        if not terminal_id or not isinstance(aes_key, str) or not aes_key:
            raise ValueError
        paired = {"id": terminal_id, "key": aes_key, "aeskey": aes_key,
                  "devSession": device["devSession"], "did": device["endpointId"],
                  "pid": device["productId"], "mac": device["mac"]}
    except (KeyError, ValueError, TypeError, AttributeError):
        raise CloudError("The cloud device pairing is incomplete. Sign in again; if needed, reconnect the unit in Tornado WIFI 3.",
                         code="cloud_pairing", verified=False) from None
    request = {"directive": {
        "header": _header("DNA.KeyValueControl", "KeyValueControl", device["endpointId"]),
        "endpoint": {
            "devicePairedInfo": {"did": device["endpointId"], "pid": device["productId"], "mac": device["mac"],
                                 "devicetypeflag": device.get("devicetypeFlag", 0),
                                 "cookie": base64.b64encode(_json({"device": paired})).decode()},
            "endpointId": device["endpointId"], "cookie": {}, "devSession": device["devSession"],
        },
        "payload": {"act": act, "params": params, "vals": vals or [], "did": device["endpointId"]},
    }}
    if act == "get" and len(params) == 1:
        request["directive"]["payload"]["vals"] = [[{"val": 0, "idx": 1}]]
    path = "/device/control/v2/sdkcontrol?" + urllib.parse.urlencode({"license": LICENSE})
    result = _post(session, path, request)
    payload = _event_payload(result)
    # Only an explicit SDK response is accepted; inventory or pushed reports
    # cannot satisfy a direct read/write request.
    if result["event"].get("header", {}).get("name") != "Response":
        raise CloudError("Tornado cloud did not confirm a direct device response.", verified=False)
    try:
        data = json.loads(payload["data"]) if isinstance(payload.get("data"), str) else payload["data"]
        if not isinstance(data, dict):
            raise ValueError
        _check_status(data)
        names, values = data["params"], data["vals"]
        if not isinstance(names, list) or not isinstance(values, list) or len(names) != len(values):
            raise ValueError
        if len(names) != len(set(names)) or not all(isinstance(name, str) for name in names):
            raise ValueError
        return {name: values[i][0]["val"] for i, name in enumerate(names)}
    except (KeyError, ValueError, TypeError, IndexError):
        raise CloudError("Tornado cloud returned an incomplete direct device response.", verified=False) from None


def _normalize(params):
    try:
        if any(type(params[k]) is not int for k in ("pwr", "temp", "ac_mode", "ac_mark")):
            raise ValueError
        if params["pwr"] not in (0, 1) or not 160 <= params["temp"] <= 320:
            raise ValueError
        mode = next(name for name, value in MODES.items() if params["ac_mode"] == value)
        fan = next(name for name, value in FANS.items() if params["ac_mark"] == value)
        optional = {}
        for key, name in (("scrdisp", "display"), ("ac_vdir", "swing")):
            value = params.get(key)
            if value is not None and (type(value) is not int or value not in (0, 1)):
                raise ValueError
            optional[name] = bool(value) if value is not None else None
    except (KeyError, ValueError, StopIteration):
        raise CloudError("Tornado cloud returned unsupported or incomplete AC settings.", verified=False) from None
    ambient = params.get("envtemp")
    ambient = ambient / 10 if type(ambient) in (int, float) and math.isfinite(ambient) and -400 <= ambient <= 800 else None
    return {"power": bool(params["pwr"]), "temperature": params["temp"] / 10,
            "ambientTemperature": ambient, "mode": mode, "fan": fan, **optional}


def get_state(session, device):
    """Online check followed by direct SDK get; never inventory's cached params."""
    _ensure_online(session, device)
    params = _act(session, device, "get", PARAMETERS)
    if "envtemp" not in params:
        # Some older Tornado profiles expose the sensor via this special query.
        try:
            ambient = _act(session, device, "get", ["mode"])
            if "envtemp" in ambient:
                params["envtemp"] = ambient["envtemp"]
        except CloudError:
            pass  # The room-temperature sensor is optional.
    return _normalize(params)


def get_status(session, device):
    return {"device": public_device(device), "state": get_state(session, device),
            "verified": True, "verification": "cloud-readback", "readbackMatched": True}


def _validate_changes(changes):
    if not isinstance(changes, dict) or not changes or set(changes) - PARAMETER_MAP.keys():
        raise CloudError("Specify power, temperature, mode, fan, display, or swing.")
    for key, value in changes.items():
        if key in ("power", "display", "swing") and type(value) is not bool:
            raise CloudError(f"{key} must be on or off.")
        if key == "temperature" and (type(value) not in (int, float) or not math.isfinite(value)
                                     or not 16 <= value <= 32 or value * 2 != round(value * 2)):
            raise CloudError("Temperature must be 16–32 °C in 0.5 °C steps.")
        if key == "mode" and (not isinstance(value, str) or value not in MODES):
            raise CloudError("Mode must be auto, cool, dry, heat, or fan.")
        if key == "fan" and value not in ("auto", "low", "medium", "high"):
            raise CloudError("Fan must be auto, low, medium, or high.")


def set_state(session, device, changes, *, readback_attempts=3, readback_delay=0.5):
    """Send exactly once, then verify changes with separate online SDK reads."""
    _validate_changes(changes)
    if type(readback_attempts) is not int or not 1 <= readback_attempts <= 5:
        raise CloudError("Use between one and five verification reads.")
    if type(readback_delay) not in (int, float) or not math.isfinite(readback_delay) or not 0 <= readback_delay <= 5:
        raise CloudError("Verification read delay must be between zero and five seconds.")
    before = get_state(session, device)
    if any(before.get(key) is None for key in changes):
        raise CloudError("This AC did not report support for the requested setting.", verified=False)
    encoded = {}
    for key, value in changes.items():
        encoded[PARAMETER_MAP[key]] = (int(value * 10) if key == "temperature" else
                                      MODES[value] if key == "mode" else FANS[value] if key == "fan" else int(value))
    try:
        _act(session, device, "set", list(encoded), [[{"val": value, "idx": 1}] for value in encoded.values()])
    except CloudError as exc:
        raise CloudError("The cloud command was not acknowledged. It may have reached the AC; refresh before trying again.",
                         code=exc.details.get("code", "cloud_command"), verified=False) from None
    observed = None
    for attempt in range(readback_attempts):
        if attempt:
            time.sleep(readback_delay)
        try:
            observed = get_state(session, device)
        except CloudError:
            raise CloudError("The command was sent, but a fresh cloud read failed. Refresh the device before trying again.",
                             device=public_device(device), state=observed, verified=False) from None
        mismatches = {key: {"requested": value, "actual": observed[key]} for key, value in changes.items() if observed[key] != value}
        if not mismatches:
            return {"device": public_device(device), "before": before, "state": observed,
                    "verified": True, "verification": "cloud-readback", "readbackMatched": True}
    raise CloudError("The command was sent, but cloud readback did not confirm every requested setting.",
                     device=public_device(device), state=observed, verified=False,
                     verification="cloud-readback", readbackMatched=False, mismatches=mismatches)
