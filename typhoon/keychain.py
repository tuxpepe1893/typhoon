"""Private session storage in macOS Keychain, never in argv or config files."""

import ctypes
import json
import re
import sys

from .errors import TyphoonError

SERVICE = b"com.tuxpepe1893.typhoon"
NOT_FOUND = -25300


class _Keychain:
    def __init__(self):
        if sys.platform != "darwin":
            raise TyphoonError("Tornado account sessions require macOS Keychain.")
        self.security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.core.CFRelease.argtypes = [ctypes.c_void_p]
        self.core.CFRelease.restype = None
        pointer = ctypes.c_void_p
        uint = ctypes.c_uint32
        self.security.SecKeychainFindGenericPassword.argtypes = [
            pointer, uint, ctypes.c_char_p, uint, ctypes.c_char_p,
            ctypes.POINTER(uint), ctypes.POINTER(pointer), ctypes.POINTER(pointer),
        ]
        self.security.SecKeychainAddGenericPassword.argtypes = [
            pointer, uint, ctypes.c_char_p, uint, ctypes.c_char_p, uint, pointer, ctypes.POINTER(pointer),
        ]
        self.security.SecKeychainItemModifyAttributesAndData.argtypes = [pointer, pointer, uint, pointer]
        self.security.SecKeychainItemFreeContent.argtypes = [pointer, pointer]
        self.security.SecKeychainItemDelete.argtypes = [pointer]
        for name in ("SecKeychainFindGenericPassword", "SecKeychainAddGenericPassword",
                     "SecKeychainItemModifyAttributesAndData", "SecKeychainItemFreeContent", "SecKeychainItemDelete"):
            getattr(self.security, name).restype = ctypes.c_int32

    def find(self, account: bytes, *, content=False):
        size = ctypes.c_uint32()
        data = ctypes.c_void_p()
        item = ctypes.c_void_p()
        result = self.security.SecKeychainFindGenericPassword(
            None, len(SERVICE), SERVICE, len(account), account,
            ctypes.byref(size) if content else None,
            ctypes.byref(data) if content else None, ctypes.byref(item),
        )
        if result == NOT_FOUND:
            return None, None
        _check(result)
        try:
            payload = ctypes.string_at(data, size.value) if content else None
        finally:
            if data:
                self.security.SecKeychainItemFreeContent(None, data)
        return item, payload


def _account(name: str) -> bytes:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,100}", name):
        raise TyphoonError("Invalid Keychain session name.")
    return name.encode("utf-8")


def _check(result):
    if result != 0:
        raise TyphoonError(
            f"macOS Keychain could not access the saved session (status {result}). "
            "Unlock your login Keychain and allow Typhoon access if macOS asks."
        )


def save_secret(name: str, value: dict) -> None:
    account = _account(name)
    if not isinstance(value, dict):
        raise TyphoonError("A saved account session must be an object.")
    raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > 1_000_000:
        raise TyphoonError("The cloud returned an unexpectedly large account session.")
    api = _Keychain()
    item, _ = api.find(account)
    try:
        if item:
            result = api.security.SecKeychainItemModifyAttributesAndData(item, None, len(raw), raw)
        else:
            result = api.security.SecKeychainAddGenericPassword(
                None, len(SERVICE), SERVICE, len(account), account, len(raw), raw, None,
            )
        _check(result)
    finally:
        if item:
            api.core.CFRelease(item)


def load_secret(name: str) -> dict | None:
    api = _Keychain()
    item, payload = api.find(_account(name), content=True)
    if item is None:
        return None
    try:
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError) as exc:
        raise TyphoonError("The saved account session is invalid. Sign in to Tornado again.") from exc
    finally:
        api.core.CFRelease(item)


def delete_secret(name: str) -> None:
    api = _Keychain()
    item, _ = api.find(_account(name))
    if item is None:
        return
    try:
        _check(api.security.SecKeychainItemDelete(item))
    finally:
        api.core.CFRelease(item)
