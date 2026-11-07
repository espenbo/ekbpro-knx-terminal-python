[English](../en/02-own-project.md) · **Norsk**

# Bruke ditt eget KNX-prosjekt

Programmet er ikke knyttet til ett bestemt bygg. Alt bygges fra ETS-prosjektfila, så alle som har
`.knxproj`-fila for anlegget sitt, kan bruke det.

## 1. Eksporter prosjektet fra ETS

I ETS 5 eller 6: **Project overview → velg prosjektet → Export**. Da får du en `.knxproj`-fil
(en `.knxprojarchive` fra ETS 6 fungerer også).
Er prosjektet passordbeskyttet, trenger du passordet (`--password`).

Bruker anlegget **KNX Secure** (IP Secure eller Data Secure), trenger du også keyring-fila:
**Project → Security → Export keyring (.knxkeys)**.

## 2. Lag konfigurasjonen

```bash
./scada init ~/MyHouse.knxproj --gateway 192.168.1.50
```

| Valg | Betydning |
|---|---|
| `--gateway IP` | IP-adressen til KNX IP-grensesnittet. Utelatt betyr automatisk søk. |
| `--connection TYPE` | `tunneling` (standard når gateway er oppgitt), `tunneling_tcp`, `routing`, `automatic` (standard uten gateway), `tunneling_secure`, `routing_secure`, `usb` |
| `--password PW` | Passordet til `.knxproj` |
| `--keyring FIL` | KNX Secure-keyring (`.knxkeys`) eksportert fra ETS. Tilkoblingen blir `tunneling_secure` (med `--gateway`) eller `routing_secure`. |
| `--keyring-password PW` | Passordet til keyring-fila |
| `--user-id N` | Tunnelbruker for secure tunneling (valgfri) |
| `--usb-device ENHET` | KNX USB-grensesnitt i stedet for IP: serienummer, `/dev/hidrawN` eller `vvvv:pppp` (`./scada knx-usb` viser dem). Tilkoblingen blir `usb`. |
| `--language LANG` | Språk i brukergrensesnittet: `en` (engelsk, standard) eller `nb` (norsk bokmål). Skrives til `ui.language` i `config.yaml`. |
| `--force` | Overskriv en eksisterende `config.yaml`. Den gamle beholdes som `config.yaml.bak`. |

`init` gjør dette:

1. Skriver `config.yaml` ut fra malen [`config.example.yaml`](../../config.example.yaml).
2. Leser prosjektet. Det tar 5–30 sekunder, avhengig av størrelsen.
3. Lagrer resultatet i `var/project_cache.json`.
4. Bruker prosjektnavnet fra ETS som tittel.
5. Viser hva som ble funnet:

```
Project:         My House
Group addresses: 1323 (484 in use)
Cards:           39  (built from ETS functions)
  1 etg: Stue, Kjøkken, Bad, Gang, Trapp
  2 etg: Sov 1, Sov 2, Sov 3, Trapperom
```

Etasje- og romnavn kommer fra ETS og vises nøyaktig slik de står i prosjektet, uansett hvilket språk
brukergrensesnittet har.

For KNX Secure gir du keyring-fila til `init`
(`./scada init ~/MyHouse.knxproj --gateway 192.168.1.50 --keyring ~/MyHouse.knxkeys --keyring-password …`),
eller legger dette inn i `config.yaml`:

```yaml
knx:
  connection: tunneling_secure
  gateway_ip: 192.168.1.50
  keyring_file: MyHouse.knxkeys
  keyring_password: "the password you chose when exporting"
```

## 3. Slik bygges visualiseringen

Programmet bruker det som finnes i prosjektet, i denne rekkefølgen.

### A. ETS-funksjoner (best resultat)

Har prosjektet en **bygningsstruktur** (bygning → etasje → rom) med **funksjoner** i rommene, bruker
programmet dem direkte. Funksjoner er for eksempel «Taklys – dimbart lys» eller
«Romoppvarming – radiator», og de lages i ETS under *Buildings*. Hver funksjon blir et kort i
romvisningen, og rollene i ETS bestemmer hvilken kontroll hver gruppeadresse får:

| ETS-rolle | Blir |
|---|---|
| Av/På (SwitchOnOff) + Status (InfoOnOff) | Bryter med statustilbakemelding |
| Lysstyrke (ActualDimmingValue) | Dimmer (− / + / nøyaktig verdi) |
| Romtemperatur (TempRoom) | Måleverdi |
| Settpunkt (TempRoomSetpoint) | Settpunkt med − / + |
| Driftsmodus (HVACMode) | Nedtrekksliste: Auto, Komfort, Standby, Natt/Eco, Frostsikring |
| Vinduskontakt (WindowStatus), ventilstilling (ValvePosition) | Statusvisning |

Relativ dimming (RelativeSetvalueControl, DPT 3) hoppes over. Gruppeadresser uten kjent rolle
klassifiseres etter datatype (DPT), om de kan skrives til, og navnet (se under).

### B. Gruppeadressestrukturen (reserve)

Har prosjektet ingen funksjoner, bygges rommene fra gruppeadressene:

- en **hovedgruppe** blir en «etasje» i menyen
- en **midtgruppe** blir et «rom»
- **gruppeadresser med samme navn**, bortsett fra ord som *switching / switch / status / feedback /
  state / value* (og de norske *tilbakemelding / verdi*), blir ett kort

Eksempel: «Stue taklys switching» (1/1/2), «Stue taklys status» (1/4/2) og «Stue taklys value» (1/3/2)
blir kortet *Stue taklys* med en bryter og en dimmer.

`./scada info` viser hvilken metode som ble brukt («bygd fra …»).

Funksjoner som ikke ligger i noe rom, samles under etasjen *Annet*, rommet *Uten rom*.

### C. Romnavn i gruppeadressenavnene

Er prosjektet strukturert etter *funksjon* («Actual temp Office 1», «Switching Office 1 ceiling») og har ingen
ETS-funksjoner, setter du `project.rooms` med et mønster som finner rommet i navnene. Alle adressene til et rom havner
da i det rommet. Se [Konfigurasjon](04-configuration.md#rom-fra-navnene-på-gruppeadressene).

### Tips for best resultat i ETS

- **Sett DPT på gruppeadressene.** Uten DPT kan ikke verdien tolkes, og den vises som råbytes.
  Har adressen ingen DPT, hentes den fra de koblede kommunikasjonsobjektene når det går.
- **Gi statusadressene navn med «status»**, «feedback», «state» eller «tilbakemelding», og styreadressene med
  «switching» eller «av/på». Da blir bryter og status paret.
- **Settpunktadresser** kjennes igjen på «setpoint», «setpunkt», «settpunkt», «skal», «ønsket» eller «basis» i navnet.
- **Leseflagget (R)** på statusobjektene lar serveren lese gjeldende verdi ved oppstart når
  `read_on_start: status` er satt (standard). Ellers vises den gamle verdien fra databasen (dempet) eller «–»
  til første telegram kommer.
- **Plassholdere** (navn som bare består av tegn som `-`, `_`, `.` eller `x`) og adresser uten koblede
  objekter er skjult som standard. Trykk `u` i gruppeadressevisningen for å vise dem.

## 4. Tilpass

| Hva | Hvor |
|---|---|
| Alarmregler | `alarms:` i `config.yaml`. Malen har generelle regler for røyk, vannlekkasje, feil og høy temperatur. Se [Konfigurasjon](04-configuration.md#alarmer). |
| Tittel, tema, språk, skjermsparer | `ui:` i `config.yaml` |
| Statusfliser (ute, effekt, energi) | Finnes automatisk ut fra navnet. Overstyr med `ui.outdoor_ga`, `ui.power_ga`, `ui.energy_ga`. |
| Plantegning | `plan.yaml`. Se [Plantegning](06-floor-plan.md). |

## 5. Når prosjektet endres i ETS

Eksporter `.knxproj` på nytt og bytt ut den gamle fila. Serveren og visualiseringen ser at fila
er nyere enn hurtiglageret og leser den inn på nytt ved neste start. Vil du gjøre det med en gang:

```bash
./scada import
sudo systemctl restart knxscada   # eller stopp og start serveren
```

## Flere installasjoner på samme maskin

(Ett anlegg som består av flere ETS-prosjekter – f.eks. et hovedbygg og et tilbygg – konfigureres i *én*
installasjon med `project.files`: se [Konfigurasjon](04-configuration.md#flere-ets-prosjekter-i-ett-anlegg).)

Lag én mappe per installasjon, f.eks. under `sites/` (ignoreres av git). Relative stier i
`config.yaml` der – socket, database, prosjekt – regnes fra den mappa, så de kolliderer aldri. For å kjøre
dem som tjenester, se [Flere installasjoner som tjenester](05-operation-and-ssh.md#flere-installasjoner-som-tjenester).

```bash
mkdir -p sites/cabin
./scada -c sites/cabin/config.yaml init ~/Cabin.knxproj --gateway 10.0.0.20
./scada -c sites/cabin/config.yaml server
./scada -c sites/cabin/config.yaml
```

Hver installasjon får sin egen `sites/<name>/var/` med socket, database og prosjekt-hurtiglager.
