"""Bounded Broadlink discovery, including shorter AUX discovery responses."""

import ipaddress
import re
import selectors
import socket
import subprocess
import time

from broadlink.protocol import Datetime

from .config import DeviceIdentity, SUPPORTED_TYPE
from .errors import TyphoonError

DISCOVERY_PORTS = (80, 15001, 2415)


def local_interfaces() -> list[dict]:
    """Read local interface addresses; this does not contact network devices."""
    try:
        output = subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True, timeout=3, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    result = []
    name = ""
    for line in output.splitlines():
        if line and not line[0].isspace():
            name = line.split(":", 1)[0]
        match = re.search(r"\binet (\d+(?:\.\d+){3})\b.*?\bbroadcast (\d+(?:\.\d+){3})\b", line)
        if match and not ipaddress.IPv4Address(match[1]).is_loopback:
            result.append({"name": name, "address": match[1], "broadcast": match[2]})
    return result


def discovery_packet(address: str, port: int, *, magic=False, reversed_address=True) -> bytes:
    packet = bytearray(0x30)
    if magic:
        packet[:8] = bytes.fromhex("5aa5aa555aa5aa55")
    packet[0x08:0x14] = Datetime.pack(Datetime.now())
    # Broadlink's official Python library specifies little-endian address bytes.
    address_bytes = socket.inet_aton(address)
    packet[0x18:0x1C] = address_bytes[::-1] if reversed_address else address_bytes
    packet[0x1C:0x1E] = port.to_bytes(2, "little")
    packet[0x26] = 6
    packet[0x20:0x22] = (sum(packet, 0xBEAF) & 0xFFFF).to_bytes(2, "little")
    return bytes(packet)


def parse_discovery_response(packet: bytes, host: tuple[str, int]) -> DeviceIdentity | None:
    # Device identity ends at byte 64. Legacy AUX responses are only 72 bytes;
    # Broadlink 0.19's scanner unconditionally reads byte 127 and misses them.
    if len(packet) < 64 or packet[0x26] != 7:
        return None
    devtype = int.from_bytes(packet[0x34:0x36], "little")
    if devtype != SUPPORTED_TYPE:
        return None
    mac = packet[0x3A:0x40][::-1]
    if mac in (bytes(6), bytes([255]) * 6):
        return None
    raw_name = packet[0x40:min(len(packet), 0x7F)].split(b"\0", 1)[0]
    name = raw_name.decode("utf-8", errors="replace").strip()
    name = "".join(c for c in name if ord(c) >= 32)
    return DeviceIdentity(
        host=host[0], mac=mac.hex(":"), devtype=devtype,
        name=name or "Tornado air conditioner", locked=bool(packet[0x7F]) if len(packet) > 0x7F else False,
        port=host[1],
    )


def discover(timeout: float = 5, host: str | None = None) -> list[DeviceIdentity]:
    if not 0 < timeout <= 60:
        raise TyphoonError("Discovery timeout must be greater than zero and at most 60 seconds.")
    if host:
        try:
            host = str(ipaddress.IPv4Address(host))
        except ValueError as exc:
            raise TyphoonError("Discovery host must be an IPv4 address.") from exc
    interfaces = local_interfaces()
    if not interfaces:
        interfaces = [{"address": "0.0.0.0", "broadcast": "255.255.255.255"}]
    sockets = []
    errors = []
    devices = {}
    with selectors.DefaultSelector() as selector:
        try:
            for interface in interfaces:
                conn = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sockets.append(conn)
                try:
                    conn.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    conn.bind((interface["address"], 0))
                    conn.setblocking(False)
                    packets = [discovery_packet(interface["address"], conn.getsockname()[1],
                                                magic=magic, reversed_address=reversed_address)
                               for magic in (False, True) for reversed_address in (True, False)]
                    destinations = [host] if host else list(dict.fromkeys([interface["broadcast"], "255.255.255.255"]))
                    selector.register(conn, selectors.EVENT_READ, (packets, destinations))
                except OSError as exc:
                    errors.append(str(exc))
                    conn.close()
            if not selector.get_map():
                raise TyphoonError("Cannot open local discovery sockets. Check macOS Local Network permission. " + "; ".join(errors))
            deadline = time.monotonic() + timeout
            next_send = 0
            sent = 0
            while time.monotonic() < deadline:
                now = time.monotonic()
                if now >= next_send:
                    for key in selector.get_map().values():
                        packets, destinations = key.data
                        for destination in destinations:
                            for port in DISCOVERY_PORTS:
                                for packet in packets:
                                    try:
                                        key.fileobj.sendto(packet, (destination, port))
                                        sent += 1
                                    except OSError as exc:
                                        errors.append(str(exc))
                    next_send = now + 1
                for key, _ in selector.select(max(0, min(deadline, next_send) - time.monotonic())):
                    try:
                        packet, address = key.fileobj.recvfrom(2048)
                    except (BlockingIOError, OSError):
                        continue
                    if host and address[0] != host:
                        continue
                    device = parse_discovery_response(packet, address)
                    if device:
                        devices[device.mac] = device
            if sent == 0:
                raise TyphoonError("Discovery packets could not be sent. Check macOS Local Network permission and the Wi-Fi connection. " + "; ".join(dict.fromkeys(errors)))
        finally:
            for conn in sockets:
                conn.close()
    return sorted(devices.values(), key=lambda device: (device.name, device.mac))
