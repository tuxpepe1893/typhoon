"""AUX UART frames inside Broadlink's authenticated, encrypted UDP transport.

Wire references:
https://github.com/GrKoR/AUX_HVAC_Protocol
https://github.com/makleso6/broadlink-aircon-api/blob/master/src/index.ts

Broadlink 0.19 provides the outer transport but its HVAC inner checksum, heat/
fan enum values, fractional temperature, and write-ack parsing differ from AUX.
"""

import enum
import struct

import broadlink
from broadlink import exceptions as broadlink_errors

from .errors import TyphoonError


# Physically verified on the user's Tornado SMART-22X SQ: DS=0 shows the
# numbers, DS=1 hides them. Other AUX profiles retain their normal polarity.
DISPLAY_INVERTED_TYPES = frozenset({0x4E2A})


def aux_checksum(data: bytes) -> bytes:
    """16-bit one's-complement sum of big-endian words; odd bytes pad right."""
    padded = data + b"\x00" if len(data) % 2 else data
    total = sum(int.from_bytes(padded[i:i + 2], "big") for i in range(0, len(padded), 2))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (total ^ 0xFFFF).to_bytes(2, "big")


class TornadoHVAC(broadlink.hvac):
    @enum.unique
    class Mode(enum.IntEnum):
        AUTO = 0
        COOL = 1
        DRY = 2
        HEAT = 4
        FAN = 6

    @enum.unique
    class SwHoriz(enum.IntEnum):
        # Some AUX variants report 1 for stopped, others 7. Preserve the raw
        # three-bit horizontal position; the public control is vertical only.
        ON = 0
        POS1 = 1
        POS2 = 2
        POS3 = 3
        POS4 = 4
        POS5 = 5
        POS6 = 6
        OFF = 7

    def _encode(self, data: bytes) -> bytes:
        length = 10 + len(data)
        packet = struct.pack("<HHHHH", length, 0x00BB, 0x8006, 0, len(data)) + data
        return packet + aux_checksum(packet[2:])

    def _decode(self, response: bytes) -> bytes:
        payload = self.decrypt(response[0x38:])
        if len(payload) < 12:
            raise TyphoonError("The air conditioner returned a truncated AUX frame.")
        length = int.from_bytes(payload[:2], "little")
        data_length = int.from_bytes(payload[8:10], "little")
        if length != 10 + data_length or length + 2 > len(payload):
            raise TyphoonError("The air conditioner returned inconsistent AUX frame lengths.")
        if payload[2:5] != b"\xbb\x00\x07":
            raise TyphoonError("The device returned an unsupported AUX response type.")
        if aux_checksum(payload[2:length]) != payload[length:length + 2]:
            raise TyphoonError("The air conditioner's AUX response checksum is invalid.")
        return payload[10:length]

    def _send(self, command: int, data: bytes = b"") -> bytes:
        opcode = (command << 4) | 1
        packet = self._encode(bytes([opcode, 1]) + data)
        response = self.send_packet(0x6A, packet)
        broadlink_errors.check_error(response[0x22:0x24])
        reply = self._decode(response)
        if len(reply) < 2 or reply[:2] != bytes([1, opcode]):
            raise TyphoonError("The device response did not match the requested AUX command.")
        body = reply[2:]
        if command == 0 and len(body) == 2 and body != packet[-2:]:
            raise TyphoonError("The device acknowledged a different command checksum.")
        return body

    def _parse_state(self, data: bytes) -> dict:
        if len(data) < 13:
            raise TyphoonError("The air conditioner returned an incomplete state.")
        try:
            state = super()._parse_state(data)
        except (ValueError, IndexError) as exc:
            codes = {"mode": data[5] >> 5, "fan": data[3] >> 5,
                     "preset": data[4] >> 6, "verticalSwing": data[0] & 7}
            raise TyphoonError("The device returned an unsupported state code; no settings were changed.",
                               protocolCodes=codes) from exc
        state["target_temp"] = 8 + (data[0] >> 3) + ((data[2] >> 7) & 1) * 0.5
        if self.devtype in DISPLAY_INVERTED_TYPES:
            state["display"] = not state["display"]
        self._last_state_payload = bytes(data[:13])
        return state

    def set_state(self, **state) -> dict:
        original = getattr(self, "_last_state_payload", None)
        if original is None:
            raise TyphoonError("Read the current air conditioner state before changing it.")
        # Keep timer, Fahrenheit, inverter-limit, and other undocumented bits.
        data = bytearray(original)
        temperature = state["target_temp"]
        data[0] = ((int(temperature) - 8) << 3) | state["swing_v"]
        data[1] = (data[1] & 0x1F) | (state["swing_h"] << 5)
        data[2] = (data[2] & 0x7F) | ((temperature % 1 == 0.5) << 7)
        data[3] = (data[3] & 0x1F) | (state["speed"] << 5)
        data[4] = (data[4] & 0x3F) | (state["preset"] << 6)
        data[5] = (data[5] & 0x13) | (state["mode"] << 5) | (state["sleep"] << 2) | (state["ifeel"] << 3)
        data[8] = (data[8] & 0xD9) | (state["power"] << 5) | (state["clean"] << 2) | (state["health"] << 1)
        display_wire = bool(state["display"]) ^ (self.devtype in DISPLAY_INVERTED_TYPES)
        data[10] = (data[10] & 0xE7) | (display_wire << 4) | (state["mildew"] << 3)
        # Byte 12 is a read-only mirror of fractional temperature on AUX;
        # command frames use byte 2 and send this byte as zero.
        data[12] = 0
        reply = self._send(0, bytes(data))
        if len(reply) not in (2, 13):
            raise TyphoonError("The device returned an unsupported write acknowledgement.")
        # A normal AUX write ack contains the command checksum, not a state.
        # Controller.set performs its own independent get_state afterwards.
        return {}

    def get_ac_info(self) -> dict:
        data = self._send(2)
        if len(data) < 22:
            raise TyphoonError("The air conditioner returned incomplete temperature sensor information.")
        result = {"power": bool(data[1] & 1)}
        # AUX reports the whole ambient temperature offset by +32, followed
        # by a decimal digit. Masking to five bits wraps temperatures >=32 C.
        if data[5] and data[21] <= 9:
            result["ambient_temp"] = data[5] - 32 + data[21] / 10
        return result
