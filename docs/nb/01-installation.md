[English](../en/01-installation.md) · **Norsk**

# Installasjon

## Krav

| | |
|---|---|
| **Operativsystem** | Linux, for eksempel Debian, Ubuntu eller Raspberry Pi OS. macOS fungerer også. Windows anbefales ikke for serveren. |
| **Python** | 3.10 eller nyere. Utviklet og testet med 3.13. |
| **KNX** | Et KNX IP-grensesnitt (tunneling) eller en IP-ruter (routing) på samme nettverk |
| **ETS-prosjekt** | `.knxproj` eksportert fra ETS 4, 5 eller 6 |
| **Terminal** | En terminal med 256 farger eller truecolor og en Unicode-font, minst ca. 120 × 35 tegn |

Maskinen trenger ikke være kraftig. En Raspberry Pi 4 holder for en vanlig bolig.

Debian/Ubuntu trenger pakken for virtuelle miljøer:

```bash
sudo apt install python3 python3-venv git
```

## Installer

```bash
git clone https://github.com/espenbo/ekbpro-knx-terminal-python.git
cd ekbpro-knx-terminal-python
./setup.sh
```

`setup.sh` lager et virtuelt miljø i `venv/`, installerer grunnpakkene fra `requirements.txt` og deretter
protokollmodulene du velger (den spør; Enter eller ingen terminal = `knx`):

- grunnpakker: `textual` og `textual-plotext` (brukergrensesnitt i terminalen og grafer), `PyYAML` (konfigurasjon)
- modulen `knx`: `xknx` (KNX-kommunikasjon) og `xknxproject` (leser `.knxproj`-filer)
- modulene `modbus`, `mqtt`, `opcua`, `bacnet`: se [Protokoller og gatewayer](11-protocols-gateways.md)

```bash
./setup.sh knx modbus     # KNX og Modbus
./setup.sh modbus opcua   # et anlegg uten KNX: KNX-pakkene utelates
```

`./scada modules` viser hva som er installert; `./scada modules install NAVN …` legger til en modul senere. Et anlegg
med KNX der modulen mangler, stopper med kommandoen som installerer den. Finnes det allerede en `config.yaml`, leser
`setup.sh` også inn prosjektet på nytt (`./scada import`).

## Sett opp prosjektet ditt

```bash
./scada init /path/to/MyHouse.knxproj --gateway 192.168.1.50
```

Legg til `--language nb` hvis brukergrensesnittet skal være på norsk bokmål. Se
[Bruke ditt eget KNX-prosjekt](02-own-project.md) for detaljer.

## Første test uten anlegg

Demomodus simulerer bussen ut fra prosjektet ditt. Følere endrer seg, aktuatorer svarer med status,
og feilmeldinger dukker opp nå og da. Demomodus bruker en egen database (`var/knxscada-demo.db`)
og blandes ikke med ekte logger.

```bash
./scada server --demo        # i én terminal
./scada                      # i en annen terminal eller SSH-økt
```

## Koble til anlegget

```bash
./scada server
```

Ved oppstart kobler serveren seg til KNX og leser tilbakemeldings-, føler- og alarmadressene (`read_on_start: status`,
ca. 80 ms per adresse, se [Konfigurasjon](04-configuration.md#server)). Til en adresse har svart, viser TUI-en
siste verdi fra databasen i dempet farge. Følg med i loggen:

```
[knx] KNX connection: CONNECTED
[knx] Reading the status of 107 group addresses
```

Start så visualiseringen med `./scada`.

For å kjøre serveren fast som en tjeneste som starter av seg selv, se [Drift og SSH](05-operation-and-ssh.md).

## Oppdatering

```bash
git pull
venv/bin/pip install -r requirements.txt
./scada modules install knx          # én gang, ved oppdatering fra en versjon før 2026-11 (KNX ble en modul)
sudo systemctl restart knxscada      # hvis serveren kjører som tjeneste
```

Start også visualiseringen på nytt i åpne økter.
