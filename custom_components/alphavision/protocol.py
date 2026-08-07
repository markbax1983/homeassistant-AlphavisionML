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


def bcd_encode(code: str) -> bytes:
    """Codeer een numerieke gebruikerscode in BCD, opgevuld tot 16 cijfers.

    Gebaseerd op Alphatronics' eigen UNii-library; empirisch bevestigd te werken
    voor AlphaVision ML's REQUEST_ARM_SECTION / REQUEST_DISARM_SECTION.
    """
    if not code.isdigit():
        raise ValueError("gebruikerscode mag alleen cijfers bevatten")
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
    from Crypto.Cipher import AES

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
    """Ontleed en (indien nodig) ontsleutel een ontvangen bericht."""
    from Crypto.Cipher import AES

    header = raw[:14]
    body = raw[14:-2]
    session_id = int.from_bytes(header[0:2], "big")
    tx_sequence = int.from_bytes(header[2:6], "big")
    protocol_id = header[10]

    if protocol_id == PROTOCOL_BASIC_ENCRYPTION:
        iv = header[:12] + b"\x00\x00\x00\x00"
        aes = AES.new(shared_key, AES.MODE_CTR, initial_value=iv, nonce=b"")
        body = aes.decrypt(body)

    command = int.from_bytes(body[0:2], "big")
    data_length = int.from_bytes(body[2:4], "big")
    data = body[4 : 4 + data_length]
    return ParsedMessage(session_id, tx_sequence, command, data)
