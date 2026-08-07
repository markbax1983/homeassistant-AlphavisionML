# AlphaVision ML — Home Assistant integratie

Home Assistant custom integratie voor Alphatronics AlphaVision ML-alarmsystemen, via de
ingebouwde "Generieke Interface" (TCP, standaard poort 6502). De integratie houdt een
permanente verbinding open met het paneel en luistert live mee naar gebeurtenissen
(in-/uitschakelen, zone-open/dicht, storingen), in plaats van te pollen.

## Functionaliteit

- **Alarm control panel** — één entiteit per sectie van het paneel, met volledige
  in-/uitschakelbediening (arm away / disarm) via een pincode.
- **Binary sensors** — één entiteit per geïnstalleerde zone (deur/raam, beweging, brand,
  sabotage, etc.), plus systeemsensoren voor netvoeding, accu en voedingseenheid.
- **Sensor** — toont de laatste gebeurtenis op het paneel (bijv. wie in-/uitschakelde),
  met gebruiker en tijdstip als attributen.
- Live updates via het paneel se eigen pushberichten, aangevuld met periodieke
  statuscontrole als vangnet.

## Vereisten

- Een Alphatronics AlphaVision ML-paneel met de Generieke Interface ingeschakeld
  (AlphaTool: *Systeem instellingen > IP instellingen > Generieke interface*).
- IP-adres, poort en de bijbehorende encryptiesleutel van het paneel.

## Installatie

### Via HACS (aanbevolen)

1. Ga in Home Assistant naar **HACS**.
2. Klik rechtsboven op de drie puntjes (⋮) → **Aangepaste repositories**.
3. Voeg toe: `https://github.com/markbax1983/homeassistant-AlphavisionML`, categorie
   **Integratie**.
4. Zoek **AlphaVision ML** op in HACS en installeer.
5. Herstart Home Assistant.

### Handmatig

1. Kopieer de map `custom_components/alphavision` naar de `custom_components`-map van je
   Home Assistant-installatie.
2. Herstart Home Assistant.

## Configuratie

Configuratie verloopt volledig via de UI:

1. Ga naar **Instellingen → Apparaten & diensten → Integratie toevoegen**.
2. Zoek naar **AlphaVision ML**.
3. Vul het IP-adres, de poort (standaard `6502`) en de encryptiesleutel van het paneel in.

## Let op

Dit is een niet-officiële integratie, gebaseerd op reverse-engineering van het protocol
(afgeleid van Alphatronics' eigen open-source `unii`-library voor hun UNii-productlijn).
Gebruik op eigen risico.

## Attributie

Het protocol in deze integratie is gereverse-engineerd, met als vertrekpunt Alphatronics'
eigen open-source [`unii`-library](https://pypi.org/project/unii/)
([unii-security/py-unii](https://github.com/unii-security/py-unii), licentie:
Apache License 2.0) voor hun UNii-productlijn. AlphaVision ML deelt de framing, CRC en
encryptielaag met UNii; een deel van de dataformaten (o.a. equipment-informatie) wijkt af
en is apart bevestigd via eigen testen tegen een AlphaVision ML-paneel. Er is geen code
uit die library overgenomen; de code in deze repo is zelf herschreven op basis van dat
protocolbegrip.

## Licentie

[MIT](LICENSE)
