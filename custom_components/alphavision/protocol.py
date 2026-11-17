"""
Laagniveau protocolimplementatie voor de AlphaVision ML "Generieke Interface"
(Basis encryptie, standaard poort 6502).

Dit protocol is empirisch gereverse-engineerd, met als vertrekpunt Alphatronics'
eigen open-source `unii`-library (https://pypi.org/project/unii/) voor hun UNii-
productlijn. AlphaVision ML blijkt de framing, CRC en encryptielaag 1-op-1 te delen
met UNii; alleen een deel van de dataformaten (o.a. equipment-informatie) wijkt af.

Frame-indeling:
    14-byte header:
        2 bytes  session-id
        4 bytes  tx-sequence
        4 bytes  rx-sequence
        1 byte   protocol-id (0x04 = geen encryptie, 0x05 = basis encryptie)
        1 byte   packet-type (0x01 = sessie-opzet, 0x02 = normale verbinding)
        2 bytes  totale berichtlengte
    payload (evt. AES-CTR versleuteld, IV = eerste 12 header-bytes + 4 nulbytes):
        2 bytes  command-id
        2 bytes  datalengte
        N bytes  data
    2-byte CRC16 (poly 0x1021, geen reflectie, init/xorout 0) over het geheel.
"""

from __future__ import annotations

import re

from Crypto.Cipher import AES  # nosec B413 -- pycryptodome (onderhouden), niet het oude PyCrypto

PROTOCOL_NO_ENCRYPTION = 0x04
PROTOCOL_BASIC_ENCRYPTION = 0x05
PACKET_SESSION_SETUP = 0x01
PACKET_NORMAL_CONNECTION = 0x02

CMD_CONNECTION_REQUEST = 0x0001
CMD_CONNECTION_REQUEST_RESPONSE = 0x0002
CMD_CONNECTION_REQUEST_DENIED = 0x0003
CMD_POLL_ALIVE_REQUEST = 0x0012
CMD_POLL_ALIVE_RESPONSE = 0x0013
CMD_NORMAL_DISCONNECT = 0x0014
CMD_REQUEST_SECTION_STATUS = 0x0116
CMD_RESPONSE_SECTION_STATUS = 0x0117
CMD_REQUEST_SECTION_ARRANGEMENT = 0x0130
CMD_RESPONSE_SECTION_ARRANGEMENT = 0x0131
CMD_REQUEST_INPUT_ARRANGEMENT = 0x0140
CMD_RESPONSE_INPUT_ARRANGEMENT = 0x0141
CMD_REQUEST_EQUIPMENT_INFORMATION = 0x0142
CMD_RESPONSE_EQUIPMENT_INFORMATION = 0x0143
CMD_REQUEST_INPUT_STATUS = 0x0106
CMD_INPUT_STATUS_CHANGED = 0x0105
CMD_REQUEST_ARM_SECTION = 0x0112
CMD_RESPONSE_ARM_SECTION = 0x0113
CMD_REQUEST_DISARM_SECTION = 0x0114
CMD_RESPONSE_DISARM_SECTION = 0x0115
CMD_REQUEST_DEVICE_STATUS = 0x0108
CMD_DEVICE_STATUS_CHANGED = 0x0107
CMD_EVENT_OCCURRED = 0x0102

HEADER_LENGTH = 14
CRC_LENGTH = 2
# Kleinst mogelijke bericht: header + command-id + datalengte + CRC.
MIN_MESSAGE_LENGTH = HEADER_LENGTH + 4 + CRC_LENGTH
# Ruime bovengrens; de grootste bekende antwoorden zijn enkele honderden bytes.
MAX_MESSAGE_LENGTH = 4096

# Een gebruikerscode is 1..16 ASCII-cijfers (BCD-veld van 8 bytes). Bewust
# [0-9] en niet str.isdigit(): die laat ook Unicode-cijfers zoals '²' door.
_USER_CODE_RE = re.compile(r"[0-9]{1,16}")


class ProtocolError(ValueError):
    """Een ontvangen bericht voldoet niet aan het protocol."""


def is_valid_user_code(code: str) -> bool:
    """True als 'code' een geldige gebruikerscode (1-16 ASCII-cijfers) is."""
    return bool(_USER_CODE_RE.fullmatch(code))


def bcd_encode(code: str) -> bytes:
    """Codeer een numerieke gebruikerscode in BCD, opgevuld tot 16 cijfers.

    Gebaseerd op Alphatronics' eigen UNii-library; empirisch bevestigd te werken
    voor AlphaVision ML's REQUEST_ARM_SECTION / REQUEST_DISARM_SECTION.
    """
    if not is_valid_user_code(code):
        raise ValueError("gebruikerscode moet uit 1 tot 16 cijfers (0-9) bestaan")
    padded = code + "0" * (16 - len(code))
    return bytes.fromhex(padded)


def crc16(data: bytes) -> int:
    """CRC16 (poly 0x1021, geen reflectie, init/xorout 0)."""
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def build_message(
    session_id: int,
    tx_sequence: int,
    rx_sequence: int,
    command: int,
    data: bytes,
    shared_key: bytes,
) -> bytes:
    """Bouw een compleet, versleuteld en gechecksumd bericht."""
    header = bytearray()
    header += session_id.to_bytes(2, "big")
    header += tx_sequence.to_bytes(4, "big")
    header += rx_sequence.to_bytes(4, "big")
    header += PROTOCOL_BASIC_ENCRYPTION.to_bytes(1, "big")
    header += (
        PACKET_SESSION_SETUP if command < 0x0008 else PACKET_NORMAL_CONNECTION
    ).to_bytes(1, "big")
    header += b"\x00\x00"  # lengte wordt later ingevuld

    payload = bytearray()
    payload += command.to_bytes(2, "big")
    payload += len(data).to_bytes(2, "big")
    payload += data

    packet_length = len(header) + len(payload) + 2
    n_padding = 16 - (packet_length % 16)
    if n_padding != 16:
        payload += b"\x00" * n_padding

    iv = bytes(header[:12]) + b"\x00\x00\x00\x00"
    aes = AES.new(shared_key, AES.MODE_CTR, initial_value=iv, nonce=b"")
    payload = aes.encrypt(bytes(payload))

    message = bytearray(header) + bytearray(payload)
    total_length = len(message) + 2
    message[12] = (total_length >> 8) & 0xFF
    message[13] = total_length & 0xFF

    checksum = crc16(bytes(message))
    message += checksum.to_bytes(2, "big")
    return bytes(message)


class ParsedMessage:
    """Eenvoudige houder voor een ontvangen, ontsleuteld bericht."""

    __slots__ = ("session_id", "tx_sequence", "command", "data")

    def __init__(self, session_id: int, tx_sequence: int, command: int, data: bytes) -> None:
        self.session_id = session_id
        self.tx_sequence = tx_sequence
        self.command = command
        self.data = data


def parse_message(raw: bytes, shared_key: bytes) -> ParsedMessage:
    """Controleer, ontleed en ontsleutel een ontvangen bericht.

    Gooit ProtocolError bij een ongeldige lengte, een foute CRC of een
    onversleuteld bericht. Deze integratie verstuurt zelf altijd versleuteld
    (basis encryptie); een onversleuteld antwoord is dus nooit legitiem en zou
    een aanvaller op het netwerk anders toestaan zonder de sleutel nep-statussen
    te injecteren.
    """
    if not MIN_MESSAGE_LENGTH <= len(raw) <= MAX_MESSAGE_LENGTH:
        raise ProtocolError(f"ongeldige berichtlengte: {len(raw)} bytes")

    header = raw[:HEADER_LENGTH]
    body = raw[HEADER_LENGTH:-CRC_LENGTH]

    declared_length = int.from_bytes(header[12:14], "big")
    if declared_length != len(raw):
        raise ProtocolError(
            f"lengte in header ({declared_length}) wijkt af van ontvangen lengte ({len(raw)})"
        )

    received_crc = int.from_bytes(raw[-CRC_LENGTH:], "big")
    expected_crc = crc16(raw[:-CRC_LENGTH])
    if received_crc != expected_crc:
        raise ProtocolError(
            f"CRC klopt niet (verwacht 0x{expected_crc:04X}, ontvangen 0x{received_crc:04X})"
        )

    protocol_id = header[10]
    if protocol_id != PROTOCOL_BASIC_ENCRYPTION:
        raise ProtocolError(
            f"onverwacht protocol-id 0x{protocol_id:02X} (alleen versleutelde berichten worden geaccepteerd)"
        )

    session_id = int.from_bytes(header[0:2], "big")
    tx_sequence = int.from_bytes(header[2:6], "big")

    iv = header[:12] + b"\x00\x00\x00\x00"
    aes = AES.new(shared_key, AES.MODE_CTR, initial_value=iv, nonce=b"")
    body = aes.decrypt(body)

    command = int.from_bytes(body[0:2], "big")
    data_length = int.from_bytes(body[2:4], "big")
    data = body[4 : 4 + data_length]
    return ParsedMessage(session_id, tx_sequence, command, data)
