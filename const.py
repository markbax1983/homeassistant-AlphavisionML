"""Constanten voor de AlphaVision ML integratie."""

DOMAIN = "alphavision"

CONF_KEY = "key"

DEFAULT_PORT = 6502

# Hoe lang op een antwoord wachten voordat een verzoek als mislukt geldt. Een
# pakketcapture toonde aan dat het paneel bij een echte gebeurtenis (deur/
# beweging) soms even te druk is om meteen te antwoorden, ook al blijft de
# TCP-verbinding zelf gezond -- een ruime marge voorkomt dan een onnodige
# sessie-reset.
DEFAULT_READ_TIMEOUT = 20.0

# Hoe lang wachten voor een herverbindpoging na een storing. Uit een
# pakketcapture bleek dat het paneel na het afsluiten van een sessie nog
# 15-20 sec (en in een enkel geval zelfs tot een minuut) 'in de war' kan
# blijven over zijn vorige client en gedurende die tijd nieuwe verbindingen
# simpelweg negeert.
DEFAULT_RECONNECT_WAIT = 30.0

# Periodieke actieve statusopvraag als vangnet naast het live meeluisteren
# (voor sectiestatus, die niet spontaan gepusht wordt zoals zonestatus).
DEFAULT_POLL_INTERVAL = 10.0

# Zone-statuscodes (empirisch bevestigd)
STATUS_NORMAL = 0x0000
STATUS_OPEN = 0x0001
STATUS_NOT_INSTALLED = 0x000F

# Sectiestatuscodes. LET OP: dit was aanvankelijk verkeerd aangenomen als
# "0x01 = niet geconfigureerd" -- een arm/disarm-test met paneel-logging
# bewees het tegendeel: 0x01 = ingeschakeld, 0x02 = uitgeschakeld. Er is geen
# aparte "niet geconfigureerd"-code gevonden; secties die niet in de sectie-
# indeling van het paneel voorkomen worden simpelweg genegeerd.
SECTION_ARMED = 0x01
SECTION_DISARMED = 0x02

RESULT_REJECTED = 0x03

SIGNAL_UPDATE = f"{DOMAIN}_update"

# Device-statusvlaggen -- gebaseerd op Alphatronics' eigen UNii-library en
# bevestigd tegen een echte opvraag van de MQTT-brug. Een respons bestaat uit
# maximaal 52 records van 2 bytes (paneel + 15 IO + 16 toetsenbord + 16
# Wiegand + 1 KNX + 2 UWI + 1 redundant) -- alles daarna in de ruwe data is
# onbetrouwbare restdata en wordt genegeerd.
DEVICE_STATUS_RECORD_COUNT = 52
DEVICE_FLAG_MAINS_FAILURE = 0x0001
DEVICE_FLAG_LOW_BATTERY = 0x0004
DEVICE_FLAG_BATTERY_MISSING = 0x0200
DEVICE_FLAG_BATTERY_FAULT = 0x0800
DEVICE_FLAG_POWER_UNIT_FAILURE = 0x2000

# (naam, HA device_class) per systeemsensor
SYSTEM_SENSORS = {
    "mains_failure": ("Netvoeding storing", "problem"),
    "low_battery": ("Accu laag", "battery"),
    "battery_missing": ("Accu ontbreekt", "problem"),
    "battery_fault": ("Accu defect", "problem"),
    "power_unit_failure": ("Voedingseenheid storing", "problem"),
}

# Hoe vaak (seconden) de device-status wordt opgevraagd -- minder tijdkritisch
# dan zone-/sectiestatus, dus een ruimer interval.
DEVICE_STATUS_POLL_INTERVAL = 60.0
