"""
Hub voor de AlphaVision ML integratie.

In plaats van Home Assistant's gebruikelijke DataUpdateCoordinator (die
uitgaat van periodiek *pollen*), houdt deze hub een permanente verbinding
open op een eigen achtergrondtaak en luistert live mee naar spontane pushes
van het paneel -- exact de architectuur die zich in de losstaande MQTT-brug
heeft bewezen na uitgebreid testen (zie de projectgeschiedenis: sessie-
limiet van het paneel, een firmwarebug die na een afsluiting nog 15-90 sec
'in de war' kan blijven, en events die alleen via live meeluisteren snel
genoeg binnenkomen).

Entiteiten registreren zich via Home Assistants dispatcher-mechanisme
(async_dispatcher_connect) op SIGNAL_UPDATE, en lezen de actuele status
rechtstreeks van dit hub-object.

Opzet en herverbinden zijn bewust gescheiden:
  - async_start() blokkeert nooit op de afkoelperiode van het paneel. Lukt de
    eerste verbinding niet, dan faalt de opzet direct (ConfigEntryNotReady) en
    herhaalt Home Assistant die zelf.
  - Alleen de achtergrondtaak wacht (MIN_RECONNECT_COOLDOWN) voordat hij na een
    verbroken sessie opnieuw verbindt.
"""

from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .client import (
    AlphaVisionConnectionError,
    AlphaVisionError,
    AsyncPanelSession,
    _parse_input_status,
    parse_event_record,
)
from .const import (
    COMMAND_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_RECONNECT_WAIT,
    DEVICE_STATUS_POLL_INTERVAL,
    RESULT_REJECTED,
    SIGNAL_UPDATE,
)
from . import protocol as proto

_LOGGER = logging.getLogger(__name__)


class AlphaVisionHub:
    """Beheert de permanente verbinding en de actuele status van het paneel."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        host: str,
        port: int,
        key: str,
        session: AsyncPanelSession | None = None,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        # Een door de config flow al gevalideerde (open) sessie wordt
        # overgenomen, zodat er bij het toevoegen niet opnieuw verbonden hoeft
        # te worden.
        self.client = session or AsyncPanelSession(
            host, port, key, read_timeout=DEFAULT_READ_TIMEOUT
        )

        self.zone_names: dict[int, str] = {}
        self.section_names: dict[int, str] = {}
        self.zone_status: dict[int, int] = {}
        self.section_status: dict[int, int] = {}
        self.device_status: dict[str, bool] = {}
        self.last_event: dict | None = None
        self.available = False

        self._task: asyncio.Task | None = None
        self._stopping = False
        self._arm_queue: asyncio.Queue = asyncio.Queue()
        self._ready = asyncio.Event()
        self._first_error: Exception | None = None

    async def async_start(self, *, startup_timeout: float = 160.0) -> None:
        """Start de achtergrondtaak en wacht tot de eerste sessie is opgezet.

        Keert terug zodra de eerste verbinding werkt, of laat een
        AlphaVisionError door als het binnen startup_timeout niet lukt (voor
        Home Assistant om als ConfigEntryNotReady te behandelen).
        """
        self._task = self.hass.loop.create_task(self._run(), name=f"alphavision_{self.entry_id}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=startup_timeout)
        except asyncio.TimeoutError as ex:
            await self.async_stop()
            raise AlphaVisionConnectionError(
                f"Geen verbinding met het paneel binnen {startup_timeout}s"
            ) from ex
        except BaseException:
            # Bijv. geannuleerd omdat Home Assistant de opzet zelf afbrak (een
            # nieuwe herlaadpoging, herstart, of het afsluiten van HA terwijl
            # deze nog bezig was). CancelledError is een BaseException, geen
            # Exception -- zonder deze brede except zou de achtergrondtaak
            # (met een mogelijk al open verbinding naar het paneel) een
            # weeskind worden die niemand meer kan stoppen.
            await self.async_stop()
            raise
        if self._first_error is not None:
            err = self._first_error
            await self.async_stop()
            raise err

    async def async_stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Alles wat nog op een antwoord wacht moet nu een fout krijgen; anders
        # blijft de aanroep (bijv. alarm_arm_away) eindeloos hangen.
        self._fail_pending(AlphaVisionConnectionError("Integratie wordt gestopt"))
        await self.client.close()

    async def async_arm(self, section_nr: int, code: str) -> bool:
        return await self._queue_command(section_nr, "ARM_AWAY", code)

    async def async_disarm(self, section_nr: int, code: str) -> bool:
        return await self._queue_command(section_nr, "DISARM", code)

    async def _queue_command(self, section_nr: int, action: str, code: str) -> bool:
        if self._stopping or not self.available:
            raise AlphaVisionConnectionError("Paneel is niet verbonden")
        fut: asyncio.Future = self.hass.loop.create_future()
        await self._arm_queue.put((section_nr, action, code, fut))
        try:
            return await asyncio.wait_for(fut, timeout=COMMAND_TIMEOUT)
        except asyncio.TimeoutError as ex:
            raise AlphaVisionConnectionError(
                f"Geen antwoord van het paneel binnen {COMMAND_TIMEOUT:.0f}s"
            ) from ex

    def _fail_pending(self, exc: Exception) -> None:
        """Laat alle in de wachtrij staande commando's falen met 'exc'."""
        while not self._arm_queue.empty():
            try:
                _section_nr, _action, _code, fut = self._arm_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not fut.done():
                fut.set_exception(exc)

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    async def _run(self) -> None:
        """Hoofdlus op de achtergrond: verbinden, live meeluisteren, periodiek pollen."""
        first_attempt = True
        while not self._stopping:
            try:
                if not self.client.connected:
                    # Alleen bij een herverbinding wachten op de afkoelperiode;
                    # de allereerste poging (opzet/reload) blokkeert nooit.
                    await self.client.connect(respect_cooldown=not first_attempt)
                _LOGGER.info("Verbonden met paneel, sessie opgezet")
                if not self.zone_names:
                    self.zone_names, self.section_names = await self.client.get_arrangement()
                self.available = True
                self._first_error = None
                self._ready.set()
                self._notify()
            except Exception as ex:  # noqa: BLE001 -- langlevende achtergrondtaak
                await self.client.close()
                self.available = False
                self._fail_pending(AlphaVisionConnectionError("Verbinding met het paneel verbroken"))
                _LOGGER.warning("Verbinden mislukt: %s -- wacht %ds", ex, DEFAULT_RECONNECT_WAIT)
                if not self._ready.is_set():
                    self._first_error = ex if isinstance(ex, AlphaVisionError) else AlphaVisionConnectionError(str(ex))
                    self._ready.set()
                    return
                self._notify()
                first_attempt = False
                await asyncio.sleep(DEFAULT_RECONNECT_WAIT)
                continue

            first_attempt = False
            try:
                await self._session_loop()
            except asyncio.CancelledError:
                raise
            except Exception as ex:  # noqa: BLE001
                _LOGGER.warning("Sessie verbroken: %s -- wacht %ds", ex, DEFAULT_RECONNECT_WAIT)
                await self.client.close()
                self.available = False
                self._fail_pending(AlphaVisionConnectionError("Verbinding met het paneel verbroken"))
                self._notify()
                await asyncio.sleep(DEFAULT_RECONNECT_WAIT)

    async def _session_loop(self) -> None:
        self.zone_status, self.section_status = await self.client.get_status()
        self._notify()

        next_poll = time.monotonic() + DEFAULT_POLL_INTERVAL
        next_device_status_poll = time.monotonic()  # meteen bij de eerste gelegenheid
        while True:
            # -- In wachtrij staande arm/disarm-commando's meteen afhandelen --
            while not self._arm_queue.empty():
                section_nr, action, code, fut = self._arm_queue.get_nowait()
                try:
                    if action == "ARM_AWAY":
                        result = await self.client.arm_section(section_nr, code)
                    else:
                        result = await self.client.disarm_section(section_nr, code)
                    self.zone_status, self.section_status = await self.client.get_status()
                    self._notify()
                    if not fut.done():
                        fut.set_result(result != RESULT_REJECTED)
                except BaseException as ex:  # noqa: BLE001
                    # Ook bij annulering (stoppen van de taak) mag de aanroeper
                    # niet blijven wachten op dit al uit de wachtrij gehaalde
                    # commando.
                    if not fut.done():
                        fut.set_exception(
                            ex
                            if isinstance(ex, Exception)
                            else AlphaVisionConnectionError("Integratie wordt gestopt")
                        )
                    raise

            # -- Live meeluisteren voor tot 1 sec, dan opnieuw checken --------
            remaining = next_poll - time.monotonic()
            listen_time = min(max(remaining, 0.1), 1.0)
            parsed = await self.client.try_read_message(listen_time)
            if parsed is not None and parsed.command == proto.CMD_INPUT_STATUS_CHANGED:
                self.zone_status = _parse_input_status(parsed.data)
                self._notify()
            elif parsed is not None and parsed.command == proto.CMD_EVENT_OCCURRED:
                event = parse_event_record(parsed.data)
                if event is not None:
                    self.last_event = event
                    _LOGGER.info(
                        "Gebeurtenis: %s door %s (#%d) om %s",
                        event["description"], event["user_name"], event["user_number"],
                        event["timestamp"],
                    )
                    self._notify()

            if time.monotonic() >= next_poll:
                self.zone_status, self.section_status = await self.client.get_status()
                self._notify()
                next_poll = time.monotonic() + DEFAULT_POLL_INTERVAL

            if time.monotonic() >= next_device_status_poll:
                self.device_status = await self.client.get_device_status()
                self._notify()
                next_device_status_poll = time.monotonic() + DEVICE_STATUS_POLL_INTERVAL
