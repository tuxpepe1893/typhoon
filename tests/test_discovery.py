import pytest
from types import SimpleNamespace

from typhoon import discovery
from typhoon.discovery import discovery_packet, parse_discovery_response


@pytest.mark.parametrize("size", [72, 88, 136])
def test_supported_short_and_long_discovery_packets(size):
    packet = bytearray(size)
    packet[0x26] = 7
    packet[0x34:0x36] = bytes.fromhex("2a4e")
    packet[0x3A:0x40] = bytes.fromhex("ffeeddccbbaa")
    packet[0x40:0x44] = b"AUX\0"
    if size >= 128:
        packet[0x7F] = 1
    device = parse_discovery_response(packet, ("192.168.1.77", 80))
    assert device.mac == "aa:bb:cc:dd:ee:ff"
    assert device.name == "AUX"
    assert device.devtype == 0x4e2a
    assert device.locked == (size >= 128)


def test_unrelated_or_malformed_packets_are_ignored():
    assert parse_discovery_response(bytes(40), ("192.168.1.77", 80)) is None
    packet = bytearray(72)
    packet[0x34:0x36] = bytes.fromhex("2a4e")
    packet[0x3A:0x40] = bytes.fromhex("ffeeddccbbaa")
    assert parse_discovery_response(packet, ("192.168.1.77", 80)) is None


@pytest.mark.parametrize("magic", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_discovery_packet_has_valid_checksum_and_local_endpoint(magic, reverse):
    packet = discovery_packet("192.168.1.42", 49152, magic=magic, reversed_address=reverse)
    address = bytes([192, 168, 1, 42])
    assert packet[24:28] == (address[::-1] if reverse else address)
    assert int.from_bytes(packet[28:30], "little") == 49152
    assert int.from_bytes(packet[32:34], "little") == (sum(packet) - sum(packet[32:34]) + 0xBEAF) & 0xffff
    assert packet[:8] == (bytes.fromhex("5aa5aa555aa5aa55") if magic else bytes(8))


@pytest.mark.parametrize("send_failure", [False, True])
def test_discovery_closes_every_socket_on_timeout_or_send_failure(monkeypatch, send_failure):
    sockets = []

    class FakeSocket:
        def __init__(self, *_):
            self.closed = False
            sockets.append(self)

        def setsockopt(self, *_):
            pass

        def bind(self, address):
            self.address = address

        def setblocking(self, *_):
            pass

        def getsockname(self):
            return self.address[0], 49152

        def sendto(self, *_):
            if send_failure:
                raise PermissionError("Local Network denied")

        def close(self):
            self.closed = True

    class FakeSelector:
        def __init__(self):
            self.keys = {}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def register(self, socket, _, data):
            self.keys[socket] = SimpleNamespace(fileobj=socket, data=data)

        def get_map(self):
            return self.keys

        def select(self, *_):
            return []

    clock = iter(index / 10 for index in range(100))
    monkeypatch.setattr(discovery.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(discovery.socket, "socket", FakeSocket)
    monkeypatch.setattr(discovery.selectors, "DefaultSelector", FakeSelector)
    monkeypatch.setattr(discovery, "local_interfaces", lambda: [
        {"address": "192.0.2.1", "broadcast": "192.0.2.255"},
        {"address": "198.51.100.1", "broadcast": "198.51.100.255"},
    ])
    if send_failure:
        with pytest.raises(discovery.TyphoonError, match="could not be sent"):
            discovery.discover(timeout=1)
    else:
        assert discovery.discover(timeout=1) == []
    assert len(sockets) == 2
    assert all(socket.closed for socket in sockets)
