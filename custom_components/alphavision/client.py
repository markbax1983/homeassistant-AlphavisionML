"""
Async client voor de AlphaVision ML Generieke Interface -- permanente sessie.

Dit is de asyncio-versie van de architectuur die zich in de losstaande
MQTT-brug heeft bewezen: 1 verbinding zo lang mogelijk open houden (i.p.v.
elke opvraag opnieuw verbinden), en live meeluisteren naar spontane pushes
voor snelle reactietijd.

Waarom geen select() zoals in de blocking-socket-versie van de brug?
Bij asyncio is dat niet nodig: als een `readexactly()` door een timeout wordt
geannuleerd, blijven de al ontvangen bytes gewoon in de interne buffer van de
StreamReader staan (die onafhankelijk van onze eigen reads wordt gevuld via
de transport-laag). Een volgende leespoging gaat daar vanzelf verder. Bij een
kaal blocking socket is dat niet gegarandeerd, en gebruikten we daarom select()
om alleen te *peilen* zonder te consumeren -- hier is die omweg overbodig.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from . import protocol as proto
from .const import (
    DEVICE_FLAG_BATTERY_FAULT,
    DEVICE_FLAG_BATTERY_MISSING,
    DEVICE_FLAG_LOW_BATTERY,
    DEVICE_FLAG_MAINS_FAILURE,
    DEVICE_FLAG_POWER_UNIT_FAILURE,
    DEVICE_STATUS_RECORD_COUNT,
)

_LOGGER = logging.getLogger(__name__)

# Hoe lang na een afsluiting (netjes of niet) minimaal gewacht wordt voordat
# een nieuwe verbinding wordt geprobeerd naar hetzelfde host:poort. Uit
# pakketcaptures bleek dat het paneel na een sessie -- zelfs een perfect
# nette, volledig bevestigde TCP-afsluiting -- tot ongeveer een minuut 'in de
# war' kan blijven en nieuwe verbindingen in die tijd negeert. Dit geldt ook
# bij een snelle 'Herlaad integratie' in Home Assistant, die zonder deze
# ingebouwde pauze bijna altijd in dat venster terecht zou komen.
MIN_RECONNECT_COOLDOWN = 120.0

# Bewaart per (host, poort) het moment (time.monotonic()) van de laatste
# afsluiting, zolang dit Home Assistant-proces draait. Overleeft dus een
# 'Herlaad integratie' of het opnieuw toevoegen van de config-entry, maar
# uiteraard niet een volledige herstart van Home Assistant zelf (dan is deze
# module net zo vers geladen als de rest, en is de cooldown sowieso niet meer
# relevant omdat er dan al minstens zoveel tijd overheen is gegaan).
_last_disconnect: dict[tuple[str, int], float] = {}


class AlphaVisionError(Exception):
    """Algemene fout in de communicatie met het paneel."""


class AlphaVisionConnectionError(AlphaVisionError):
    """De verbinding met het paneel is mislukt of weggevallen."""


class AsyncPanelSession:
    """Houdt 1 asyncio-verbinding met het paneel, met alle bewezen bevindingen verwerkt."""

    def __init__(self, host: str, port: int, key: str, read_timeout: float = 20.0) -> None:
        self.host = host
        self.port = port
        self.key = key.encode("utf-8")
        self.read_timeout = read_timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.session_id = 0
        self.tx_seq = 0
        self.rx_seq = 0

    @property
    def connected(self) -> bool:
        return self.writer is not None

    async def connect(self) -> None:
        key = (self.host, self.port)
        last = _last_disconnect.get(key)
        if last is not None:
            elapsed = time.monotonic() - last
            remaining = MIN_RECONNECT_COOLDOWN - elapsed
            if remaining > 0:
                _LOGGER.info(
                    "Wacht nog %.0fs (van %.0fs) voordat een nieuwe verbinding wordt "
                    "geprobeerd -- het paneel heeft na een afsluiting tijd nodig om "
                    "zichzelf te herstellen",
                    remaining, MIN_RECONNECT_COOLDOWN,
                )
                await asyncio.sleep(remaining)
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=8.0
            )
        except (OSError, asyncio.TimeoutError) as ex:
            raise AlphaVisionConnectionError(
                f"Kon niet verbinden met {self.host}:{self.port} ({type(ex).__name__}: {ex or 'geen details'})"
            ) from ex

        self.session_id = 0xFFFF
        self.tx_seq = random.randint(0, 65535)
        self.rx_seq = 0

        msg = proto.build_message(
            self.session_id, self.tx_seq, self.rx_seq, proto.CMD_CONNECTION_REQUEST, b"", self.key
        )
        self.writer.write(msg)
        await self.writer.drain()
        parsed = await self._read_message(timeout=self.read_timeout)
        if parsed is None or parsed.command != proto.CMD_CONNECTION_REQUEST_RESPONSE:
            await self.close()
            raise AlphaVisionError("Onverwacht (of geen) antwoord op verbindingsverzoek")
        self.session_id = parsed.session_id
        self.rx_seq = parsed.tx_sequence

    async def close(self) -> None:
        if self.writer is None:
            return
        try:
            self.tx_seq += 1
            msg = proto.build_message(
                self.session_id, self.tx_seq, self.rx_seq, proto.CMD_NORMAL_DISCONNECT, b"", self.key
            )
            self.writer.write(msg)
            await asyncio.wait_for(self.writer.drain(), timeout=2.0)
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            try:
                self.writer.close()
                await asyncio.wait_for(self.writer.wait_closed(), timeout=2.0)
            except (OSError, asyncio.TimeoutError):
                pass
            self.reader = None
            self.writer = None
            _last_disconnect[(self.host, self.port)] = time.monotonic()

    async def _read_message(self, timeout: float) -> proto.ParsedMessage | None:
        """Lees 1 volledig bericht, of geef None terug bij een timeout (geen fout)."""
        assert self.reader is not None
        try:
            header = await asyncio.wait_for(self.reader.readexactly(14), timeout)
        except asyncio.TimeoutError:
            return None
        except (asyncio.IncompleteReadError, OSError) as ex:
            raise AlphaVisionConnectionError(f"Verbinding verbroken tijdens lezen: {ex}") from ex

        total_length = int.from_bytes(header[12:14], "big")
        try:
            rest = await asyncio.wait_for(self.reader.readexactly(total_length - 14), timeout)
        except asyncio.TimeoutError:
            # We hebben de header al -- dit zou niet mogen gebeuren bij een gezonde
            # verbinding, want de rest van het bericht hoort er vlak achteraan te
            # komen. Behandel dit als een verbindingsfout, niet als "geen data".
            raise AlphaVisionConnectionError("Onvolledig bericht ontvangen (timeout na header)") from None
        except (asyncio.IncompleteReadError, OSError) as ex:
            raise AlphaVisionConnectionError(f"Verbinding verbroken tijdens lezen: {ex}") from ex

        raw = header + rest
        parsed = proto.parse_message(raw, self.key)
        self.rx_seq = parsed.tx_sequence
        if parsed.session_id != self.session_id:
            self.session_id = parsed.session_id
        return parsed

    async def try_read_message(self, timeout: float) -> proto.ParsedMessage | None:
        """Voor opportunistisch meeluisteren: lees 1 bericht als er iets binnenkomt
        binnen 'timeout' seconden, anders None. Verbindingsfouten worden nog
        steeds doorgegeven (dat is geen normale 'niets ontvangen'-situatie)."""
        return await self._read_message(timeout)

    async def request(
        self, command: int, data: bytes, expected: int | None = None, total_timeout: float | None = None
    ) -> proto.ParsedMessage:
        """Verstuur een verzoek en wacht op het antwoord.

        Als 'expected' is opgegeven, blijven tussentijdse berichten (spontane
        pushes, gebeurtenislog-entries) genegeerd worden totdat het echte
        antwoord komt -- begrensd door een TOTALE tijdslimiet (standaard 60s),
        niet door een vast aantal pogingen. Dat bleek nodig: bij veel snel
        opeenvolgende tussentijdse berichten (bijv. rond een arm/disarm-
        commando) gaf een vast aantal pogingen te snel op, terwijl het echte
        antwoord vlak erna alsnog kwam.
        """
        assert self.writer is not None
        self.tx_seq += 1
        msg = proto.build_message(self.session_id, self.tx_seq, self.rx_seq, command, data, self.key)
        self.writer.write(msg)
        await self.writer.drain()

        if expected is None:
            parsed = await self._read_message(self.read_timeout)
            if parsed is None:
                raise AlphaVisionConnectionError(
                    f"Geen antwoord op commando 0x{command:04X} binnen {self.read_timeout}s"
                )
            return parsed

        budget = total_timeout if total_timeout is not None else max(self.read_timeout * 3, 60.0)
        deadline = time.monotonic() + budget
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AlphaVisionError(
                    f"Verwachte respons 0x{expected:04X} niet ontvangen binnen {budget:.0f}s"
                )
            parsed = await self._read_message(min(self.read_timeout, remaining))
            if parsed is None:
                # Stilte binnen 1 leespoging -- geen fout op zich, gewoon nog
                # niets ontvangen. Blijf binnen het totale tijdsbudget proberen.
                continue
            if parsed.command == expected:
                return parsed
            _LOGGER.debug(
                "Tussentijds bericht genegeerd (verwacht 0x%04X, kreeg 0x%04X)", expected, parsed.command
            )

    async def get_arrangement(self) -> tuple[dict[int, str], dict[int, str]]:
        section_resp = await self.request(
            proto.CMD_REQUEST_SECTION_ARRANGEMENT, b"", proto.CMD_RESPONSE_SECTION_ARRANGEMENT
        )
        input_resp = await self.request(
            proto.CMD_REQUEST_INPUT_ARRANGEMENT, (1).to_bytes(2, "big"), proto.CMD_RESPONSE_INPUT_ARRANGEMENT
        )
        return _parse_input_arrangement(input_resp.data), _parse_section_arrangement(section_resp.data)

    async def get_status(self) -> tuple[dict[int, int], dict[int, int]]:
        section_resp = await self.request(
            proto.CMD_REQUEST_SECTION_STATUS, bytes(range(1, 33)), proto.CMD_RESPONSE_SECTION_STATUS
        )
        input_resp = await self.request(
            proto.CMD_REQUEST_INPUT_STATUS, bytes(range(1, 33)), proto.CMD_INPUT_STATUS_CHANGED
        )
        return _parse_input_status(input_resp.data), _parse_section_status(section_resp.data)

    async def arm_section(self, section_nr: int, code: str) -> int:
        data = bytes([0x00]) + proto.bcd_encode(code) + bytes([section_nr & 0xFF, 0x01])
        resp = await self.request(proto.CMD_REQUEST_ARM_SECTION, data, proto.CMD_RESPONSE_ARM_SECTION)
        return resp.data[1]

    async def disarm_section(self, section_nr: int, code: str) -> int:
        data = bytes([0x00]) + proto.bcd_encode(code) + bytes([section_nr & 0xFF, 0x01])
        resp = await self.request(proto.CMD_REQUEST_DISARM_SECTION, data, proto.CMD_RESPONSE_DISARM_SECTION)
        return resp.data[1]

    async def get_device_status(self) -> dict[str, bool]:
        resp = await self.request(proto.CMD_REQUEST_DEVICE_STATUS, b"", proto.CMD_DEVICE_STATUS_CHANGED)
        return _parse_device_status(resp.data)


def _parse_section_arrangement(data: bytes) -> dict[int, str]:
    """18-byte records: 2 bytes vlag/index + 16 bytes naam."""
    names: dict[int, str] = {}
    record_size = 18
    for i in range(0, len(data) - record_size + 1, record_size):
        names[i // record_size + 1] = data[i + 2 : i + 18].decode("latin-1", "replace").strip()
    return names


def _parse_input_arrangement(data: bytes) -> dict[int, str]:
    """4-byte header, dan 22-byte records: 2 bytes vlaggen + 16 bytes naam + 4 bytes overig."""
    names: dict[int, str] = {}
    if len(data) < 4:
        return names
    body = data[4:]
    record_size = 22
    for i in range(0, len(body) - record_size + 1, record_size):
        names[i // record_size + 1] = body[i + 2 : i + 18].decode("latin-1", "replace").strip()
    return names


def _parse_section_status(data: bytes) -> dict[int, int]:
    return {data[i]: data[i + 1] for i in range(0, len(data) - 1, 2)}


def _parse_input_status(data: bytes) -> dict[int, int]:
    body = data[2:]
    return {i // 2 + 1: int.from_bytes(body[i : i + 2], "big") for i in range(0, len(body) - 1, 2)}


def _parse_device_status(data: bytes) -> dict[str, bool]:
    """Combineer alle geldige device-status-records (max. 52) tot 5 vlaggen.

    We nemen het OF van alle records (paneel + alle uitbreidingsmodules) --
    als 1 apparaat ergens een storing heeft, willen we dat zien.
    """
    valid = data[: DEVICE_STATUS_RECORD_COUNT * 2]
    combined = 0
    for i in range(0, len(valid) - 1, 2):
        combined |= int.from_bytes(valid[i : i + 2], "big")
    return {
        "mains_failure": bool(combined & DEVICE_FLAG_MAINS_FAILURE),
        "low_battery": bool(combined & DEVICE_FLAG_LOW_BATTERY),
        "battery_missing": bool(combined & DEVICE_FLAG_BATTERY_MISSING),
        "battery_fault": bool(combined & DEVICE_FLAG_BATTERY_FAULT),
        "power_unit_failure": bool(combined & DEVICE_FLAG_POWER_UNIT_FAILURE),
    }


def parse_event_record(data: bytes) -> dict | None:
    """Decodeert een EVENT_OCCURRED-bericht (vaste-breedte-velden, empirisch
    bevestigd tegen echte in-/uitschakelgebeurtenissen -- zie projectlog)."""
    if len(data) < 44:
        return None
    try:
        year = 2000 + data[4]
        month = data[5]
        day = data[6]
        hour, minute, second = data[7], data[8], data[9]
        timestamp = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}"
        description = data[10:22].decode("latin-1", "replace").strip()
        user_number = data[27]
        user_name = data[28:44].decode("latin-1", "replace").strip()
        result_code = data[-2:].decode("latin-1", "replace") if len(data) >= 2 else ""
    except (IndexError, ValueError):
        return None
    return {
        "description": description,
        "user_number": user_number,
        "user_name": user_name,
        "timestamp": timestamp,
        "result_code": result_code,
    }

