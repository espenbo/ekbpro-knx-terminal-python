[English](../en/04-configuration.md) · **Norsk**

# Konfigurasjon (`config.yaml`)

`config.yaml` ligger i programmappen. Velg en annen fil med `-c file.yaml` eller `KNXSCADA_CONFIG=file.yaml`.
Relative stier regnes fra mappen fila ligger i. Malen er [`config.example.yaml`](../../config.example.yaml).

Endringer i `knx`, `project`, `server`, `alarms` og `plc` krever omstart av serveren. Endringer i `ui` krever
omstart av visualiseringen. `ui.language` brukes også av serveren (til tekster i hendelsesloggen og alarmene), så
start serveren på nytt også når du endrer den.

## `knx` – tilkobling

| Nøkkel | Standard | Beskrivelse |
|---|---|---|
| `enabled` | på når `knx:` eller `project:` er fylt ut | `false` = kjør uten KNX (bare tilkoblingene og gatewayene i `protocols.yaml`; TUI-en starter på F11) |
| `connection` | `automatic` | `tunneling` (UDP), `tunneling_tcp`, `routing` (multicast), `automatic` (automatisk søk), `tunneling_secure`, `routing_secure`, `usb` (KNX USB-grensesnitt, se under) |
| `gateway_ip` | – | IP-adressen til IP-grensesnittet. Trengs ikke for `routing` og `automatic`. |
| `gateway_port` | `3671` | |
| `local_ip` | auto | Lokal IP det sendes fra. Sett den når maskinen har flere nettverkskort. |
| `individual_address` | – | Fysisk adresse ved routing, for eksempel `1.1.250` (USB: valgfri avsenderadresse) |
| `usb_device` | det første som finnes | Bare `usb`: hvilket KNX USB-grensesnitt – serienummer, `/dev/hidrawN` eller `vvvv:pppp` |
| `route_back` | `false` | `true` når IP-grensesnittet nås gjennom NAT eller VPN (Tailscale subnet router, WireGuard, en ruter med portvideresending): grensesnittet svarer til adressen forespørselen kom fra, i stedet for adressen i forespørselen. Bare `tunneling` (UDP). |
| `keyring_file` | – | `.knxkeys` fra ETS. Trengs for KNX Secure. |
| `keyring_password` | – | Passordet til keyring-fila |
| `user_id` | – | Tunnelbruker for secure tunneling |
| `name` | `knx` | Navnet på grensesnittet i TUI-en og hendelsesloggen (form med ett grensesnitt) |

**Tunneling eller routing?** Et IP-grensesnitt har et begrenset antall tunneler, ofte 1–5. Programmet bruker
én tunnel per grensesnitt uansett hvor mange brukere som er logget inn. Med en IP-ruter kan du bruke `routing` og
slippe å bruke tunnel i det hele tatt.

`tunneling_secure` bruker secure tunneling over TCP.

### KNX USB-grensesnitt

Et KNX USB-grensesnitt (Weinzierl, MDT, Gira, Jung, ABB …) virker som et IP-grensesnitt: `connection: usb`. Det kan
være det eneste grensesnittet eller ett av flere i `interfaces` (for eksempel en USB-pinne på en linje uten
IP-grensesnitt).

```yaml
knx:
  connection: usb
  usb_device: 00C512345678        # valgfri: serienummer, /dev/hidrawN eller vvvv:pppp – tom = det første som finnes
```

- Bare grensesnitt som støtter **cEMI**, kan brukes (praktisk talt alle nye, f.eks. Weinzierl KNX USB Interface 312).
  Ved oppstart setter serveren grensesnittet i cEMI-modus og i data link layer (mange grensesnitt starter i
  «no layer» og sender ingenting videre før det) og spør om TP-linjen har spenning. Dette er innstillinger i selve
  grensesnittet – **ingenting sendes på bussen**. Et grensesnitt som bare har EMI1/EMI2, avvises med en melding
  i hendelsesloggen.
- Topplinjen viser grensesnittet som tilkoblet så lenge TP-linjen har spenning. Trekkes pinnen ut, blir det rødt, og
  serveren finner den igjen når den er tilbake – med serienummer også når Linux gir den en annen `/dev/hidrawN`.
- Bare Linux (`/dev/hidraw`). Enheten er bare tilgjengelig for root som standard: installer udev-regelen og legg
  tjenestebrukeren i gruppen `plugdev`, og sett pinnen inn på nytt:

  ```bash
  sudo install -m 644 deploy/99-knx-usb.rules /etc/udev/rules.d/
  sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw
  sudo usermod -aG plugdev DIN_BRUKER      # start deretter tjenesten på nytt
  ```

- `./scada knx-usb` viser KNX USB-grensesnittene med serienummer og om du har tilgang.
  `./scada knx-usb --listen 30` åpner det første (eller det du navngir) og skriver ut telegrammene i 30 sekunder
  uten å sende noe – bruk det til å kontrollere pinnen før serveren tar den i bruk.
- `individual_address` (valgfri) er avsenderadressen for telegrammene våre. Uten den bruker grensesnittet sin egen.
- I Docker må enheten sendes inn med `--device /dev/hidraw2` (se [Docker](09-docker.md)).

### Flere KNX IP-grensesnitt

Et bygg med mer enn ett IP-grensesnitt (for eksempel ett per TP-linje eller bygningsdel, uten IP-stamlinje
mellom dem) settes opp med `interfaces`. Serveren åpner én tilkobling per grensesnitt og behandler dem som én
buss: alt logges i samme database, alarmene og PLS-broen ser alt, og TUI-en viser én prikk per grensesnitt i
toppfeltet og grensesnittet i busmonitoren (*Via*).

```yaml
knx:
  keyring_file: MyProject.knxkeys      # se under hvilke nøkler på dette nivået som er standard for alle grensesnitt
  interfaces:
    - name: house                      # kort navn, vises i TUI-en og hendelsesloggen
      connection: tunneling
      gateway_ip: 192.168.1.50
      default: true                    # brukes for adresser ingen andre grensesnitt tar
    - name: garage
      connection: tunneling
      gateway_ip: 192.168.1.51
      addresses: ["4/*", "5/1/*"]      # gruppeadresser som sendes gjennom dette grensesnittet
```

| Nøkkel (per grensesnitt) | Standard | Beskrivelse |
|---|---|---|
| `name` | `gateway_ip`, ellers `knx1`, `knx2` … | Bokstaver, sifre, `-`, `_`, `.`; høyst 16 tegn; unikt. |
| `connection`, `local_ip`, `route_back`, `keyring_file`, `keyring_password`, `user_id`, `dedup_ms` | fra `knx:`-nivået | Som over, for dette grensesnittet. Dette er de eneste nøklene som hentes fra `knx:`-nivået. |
| `gateway_ip`, `gateway_port`, `individual_address`, `usb_device` | som i tabellen over | Bare per grensesnitt – hentes aldri fra `knx:`-nivået. |
| `addresses` | – | Gruppeadresser der telegrammene **sendes** gjennom dette grensesnittet: en liste med mønstre, `*` passer med hva som helst (`"4/*"`, `"1/2/1?"`, `"3/1/0"`). |
| `project` | første prosjekt | Med flere ETS-prosjekter (`project.files`): prosjektet som har bussen grensesnittet er koblet til. |
| `default` | første grensesnitt i prosjektet | Grensesnittet for adresser som ingen `addresses`-mønstre passer med, og som ikke er sett ennå. |

| Nøkkel (`knx:`-nivå) | Standard | Beskrivelse |
|---|---|---|
| `dedup_ms` | `1000` | Telegrammer med samme adresse, type og verdi som kommer gjennom et *annet* grensesnitt innen denne tiden, er kopier og forkastes. `0` = av. |

**Mottak** virker uten oppsett: hvert grensesnitt leverer det det ser. **Sending** (betjening fra TUI-en,
skriving fra PLS-en, `read_on_start`) bruker ett grensesnitt per gruppeadresse:

1. det første grensesnittet der `addresses` passer;
2. ellers grensesnittet adressen sist ble *mottatt* på – serveren lærer dette mens den kjører;
3. ellers standardgrensesnittet.

`addresses` trengs altså bare for gruppeadresser som serveren må *sende* til før noe er hørt på dem – typisk
styreadressene til linjer som ikke ligger bak standardgrensesnittet.

**Koblede linjer.** Sitter grensesnittene på linjer som er koblet sammen (linjekoblere, IP-rutere), ser flere
grensesnitt det samme telegrammet. Kopiene forkastes (`dedup_ms`), så loggen og alarmene ser det én gang. Et
telegram du sender, går ut gjennom bare ett grensesnitt; koblerne fører det videre til de andre linjene som vanlig.

Formen med ett grensesnitt over (`connection`, `gateway_ip` … rett under `knx:`) virker fortsatt og er det samme
som ett grensesnitt med navnet `knx`. Med flere grensesnitt viser toppfeltet `KNX` i grønt bare når alle er
tilkoblet; betjening virker så lenge grensesnittet for den aktuelle adressen er tilkoblet.

## `project` – ETS-prosjekt

| Nøkkel | Standard | Beskrivelse |
|---|---|---|
| `file` | `project.knxproj` | Sti til `.knxproj` (eller `.knxprojarchive`) |
| `password` | – | Passord hvis prosjektet er beskyttet |
| `cache` | `var/project_cache.json` | Det innleste prosjektet. Bygges på nytt automatisk når prosjektfila er nyere, eller når `files` eller `rooms` endres. |
| `files` | – | Flere ETS-prosjekter i ett anlegg, i stedet for `file` (se under). `password` oppgis da per oppføring. |
| `rooms` | – | Rom fra navnene på gruppeadressene (se under) |
| `extra_rooms` | – | Egne rom for adresser som ikke ligger i noe ETS-rom (se [Ekstra rom](#extra-rooms)) |
| `point_rooms` | – | Rom med kort fra protokolltagger, f.eks. et sidebygg bak en gateway (se [Rom fra protokolltagger](#point-rooms)) |

`.knxprojarchive`-filer fra ETS 6 kan også brukes direkte.

### Rom fra navnene på gruppeadressene

Uten ETS-funksjoner bygges kortene fra strukturen i gruppeadressene: hovedgruppe → etasje, midtgruppe →
rom. Det passer for prosjekter som er bygd opp etter rom, men ikke for prosjekter bygd opp etter *funksjon*, der
rommet bare er en del av navnet:

```
2/0  Actual temp     2/0/5 Actual temp Office 1       2/0/6 Actual temp Office 2 …
2/2  Setpoint        2/2/5 Setpoint Office 1          2/2/6 Setpoint Office 2 …
0/0  Switching       0/0/0 Switching Office 1 ceiling 0/0/1 Switching Office 2 left …
```

`rooms` samler adressene per rom ut fra navnene:

```yaml
project:
  rooms:
    # Finner rommet i et navn. Første gruppe (eller hele treffet) er romnavnet.
    pattern: "(?i)\\b(office\\s*\\d+|meeting room|kitchen|storage|corridor)\\b"
    # Hovedgruppe → etasje. Uten denne er etasjen navnet på hovedgruppen.
    floors: {0: "2nd floor", 3: "2nd floor", 1: "1st floor", 2: "1st floor"}
    # Romnavn som vises i stedet for teksten som ble funnet, som "etasje/rom" (valgfritt).
    names:
      "2nd floor/Office 6": "Office 6 · RT6206"
    # Flere navn på settpunkter, som får − / + (valgfritt; "setpoint", "setpunkt", "ønsket" … er innebygd).
    setpoint: "(?i)set temp"
    # Navn som bare vises og aldri skrives fra et kort, selv med skriveflagg i ETS (valgfritt).
    read_only: "(?i)desired temp|valve position"
    # Adresser som holdes utenfor kortene, f.eks. en ubrukt termostatkanal (valgfritt; * passer med hva som helst).
    exclude: ["2/*/46"]
```

- Teksten *foran* rommet er radetiketten ("Actual temp", "Switching status"); et navn som begynner med rommet
  ("Office 1 Request"), har etiketten etter rommet.
- Teksten *etter* rommet er armaturen/kursen ("ceiling", "left"). Et rom får ett kort per hovedgruppe og
  armatur, med navnet på hovedgruppen uten etasjeordene som tittel: "Lighting 2nd floor" + "ceiling" i etasjen
  "2nd floor" → **Lighting ceiling**; "Heating 2nd floor" → **Heating**.
- Styre- og statusadresser pares som vanlig ("Switching Office 1 ceiling" + "Switching status Office 1 ceiling").
- Adresser uten rom i navnet vises som før, ut fra strukturen i gruppeadressene.
- Alle adresser med skriveflagg i ETS kan skrives fra kortet sitt. Når objektet til en termostat har flagget, men
  bare skal leses (ønsket temperatur den regner ut, ventilstillingen), legger du navnet inn i `read_only`;
  `read_only` går foran `setpoint` og de innebygde settpunktordene.
- Oversikten (F1) henter settpunktet til rommet fra settpunktraden i varmekortet og viser varme når en
  ventilstilling (5.001, bare lesing) er over 0 %.
- Ignoreres når prosjektet har ETS-funksjoner. Med flere prosjekter gjelder `rooms` for alle; en oppføring
  i `files` kan ha sine egne `rooms`.

Kjør `./scada import` etter endringer (`./scada info` viser rommene).

<a id="extra-rooms"></a>

### Ekstra rom

Adresser som ikke ligger i noe ETS-rom – f.eks. en føler og lys på et loft som mangler i bygningsstrukturen i
ETS – kan legges i egne rom. De vises i F2 og i oversikten, og `plan.yaml` kobler til dem med navn på samme måte
som til andre rom.

```yaml
project:
  extra_rooms:
    - floor: Loft                     # en etasje som finnes, eller en ny
      name: Loft                      # navnet plan.yaml kobler til (room: "Loft")
      cards:
        - name: Temperatur
          addresses: ["3/0/35"]
        - name: Loftslys
          addresses: ["1/1/70", "1/4/70", "1/3/70", "1/5/70"]
        - name: Lys bod
          addresses: ["1/0/5", "1/0/7"]
          status: {"1/0/5": "1/4/81"} # styring → tilbakemelding, når navnene ikke parer dem
          usage: light                # light | dimmer | heating | other (standard: gjettet)
```

Standardverdier: `floor` = `Other`, `name` = `Room 1`, `Room 2` …, `usage` gjettes ut fra adressene. Med flere
ETS-prosjekter må adressene i det andre og senere prosjekter ha prefikset sitt (`S:3/0/35`).

Hvert kort bygges som kortene fra ETS: styre- og statusadresser pares etter navn (eller med `status`), og
DPT-en avgjør om det blir bryter, dimmer, settpunkt eller verdi. Et kort med temperatur regnes som
romtemperaturen. Adresser som ikke finnes i prosjektet, stopper oppstarten med en feil. Rommene leses fra
`config.yaml` ved hver oppstart – du trenger ikke `./scada import`.

<a id="point-rooms"></a>

### Rom fra protokolltagger (et annet bygg bak en gateway)

Rom kan også vise tagger fra `protocols.yaml` – for eksempel et sidebygg der KNX kommer til oss gjennom en
KNX→BACnet-gateway. `project.point_rooms` bygges som `extra_rooms`, men hvert kort har `rows` med `point`
(`tilkobling/tagg`). Med `building` grupperer menyen etasjene per bygg: «Etasjer og rom → Hovedbygg / Sidebygg →
etasjer → rom»; `ui.building` gir navnet på bygget med ETS-etasjene (standard «Hovedbygg»).

```yaml
project:
  point_rooms:
    - building: Sidebygg
      floor: 2. etg
      name: Kontor 205
      cards:
        - name: Lys
          usage: light                # light | dimmer | heating | other
          rows:
            - {point: "gateway/Lys 205 On/Off", status: "gateway/Lys 205 Status", label: Lys}
        - name: Varme
          usage: heating
          rows:
            - {point: "gateway/RT205_PV", role: temp, label: Temperatur}
            - {point: "gateway/RT205_SP", role: setpoint, label: Settpunkt}
            - {point: "gateway/RT205_VP", role: heat, label: Varme}
ui:
  building: Hovedbygg
```

Radtypen følger av taggen: skrivbar av/på → bryter, skrivbar med `role: setpoint` → settpunkt (− / +), skrivbar %
→ dimmer, alt annet en verdi (`kind:` overstyrer). `role` `temp`, `setpoint` og `heat` gir romtemperatur, settpunkt
og varme på romkortet i oversikten. Skriving går til taggen (`writable: true` i `protocols.yaml`) og logges som
annen skriving. En tagg som ikke finnes, meldes når TUI-en starter; de andre rommene virker.

### Flere ETS-prosjekter i ett anlegg

Et anlegg kan bestå av flere ETS-prosjekter med hver sin KNX-installasjon – for eksempel et hovedbygg og et
sidebygg. List dem under `files` i stedet for `file`:

```yaml
project:
  files:
    - name: main                 # brukes av knx.interfaces[].project
      label: Hovedbygg           # vises i TUI-en (etasjer, adressetre)
      file: Hovedbygg.knxproj
    - name: annex
      label: Sidebygg
      prefix: S                  # bokstaver/sifre; standard: navnet
      file: Sidebygg.knxprojarchive
      # password: ""
  cache: var/project_cache.json

knx:
  interfaces:
    - {name: main-1, project: main, gateway_ip: 192.168.1.50}
    - {name: annex,  project: annex, gateway_ip: 192.168.2.50}
```

To prosjekter kan bruke samme gruppeadresse til forskjellige ting (`3/0/1` er romtemperatur i det ene og
utetemperatur i det andre). Adressene i det første prosjektet beholdes derfor som de er, og adressene i de
andre får prefikset sitt: **`S:3/0/1`**. Det er adressen overalt i programmet – i TUI-en, databasen,
alarmreglene (`match_address: "S:3/*"`), `plan.yaml` og PLS-koblingen (variabelen `GA_S_3_0_1_…`).
På bussen fjernes prefikset igjen: et telegram fra et grensesnitt i sidebygget lagres som `S:…`, og
`S:…` sendes bare gjennom et grensesnitt i sidebygget. `addresses`-mønstre for et grensesnitt skrives uten
prefikset.

Etasjene får navn som "<label>: <etasje>", og adressetreet (F3) har én toppnode per prosjekt.

| Nøkkel (per oppføring i `files`) | Standard | Beskrivelse |
|---|---|---|
| `file` | påkrevd | Sti til `.knxproj` / `.knxprojarchive`. En oppføring kan også bare være stien som en streng. |
| `name` | `p1`, `p2` … | Brukes av `knx.interfaces[].project` |
| `label` | navnet | Vises i TUI-en (etasjer, adressetre) |
| `prefix` | navnet | Bokstaver og sifre. Det første prosjektet får aldri prefiks. |
| `password` | – | Passord hvis prosjektet er beskyttet |
| `rooms` | `project.rooms` | Rom fra navn, bare for dette prosjektet |

## `server`

| Nøkkel | Standard | Beskrivelse |
|---|---|---|
| `socket` | `var/knxscada.sock` | Unix-socket mellom server og visualisering |
| `database` | `var/knxscada.db` | SQLite med telegrammer, hendelser og alarmer. Demomodus bruker `var/knxscada-demo.db`. |
| `retention_days` | `90` | Hvor lenge telegrammer og hendelser tas vare på. Gamle data slettes hver time. Alarmer beholdes. |
| `read_on_start` | `status` | Lese verdier fra bussen når serveren starter: `status` (bare tilbakemeldings-, føler- og alarmadresser), `false` (send ingen telegrammer; de siste verdiene fra databasen vises, merket som gamle) eller `all` (se advarselen under). `true` betyr `status`, og `off`, `no`, `0` betyr `false`. Alle andre verdier tolkes som `status`. Verdier fra databasen sendes aldri videre til gatewayer. |
| `read_interval_ms` | `80` | Pause mellom lesingene, så bussen ikke blir overbelastet |
| `protocols` | `protocols.yaml` | Tilkoblinger og gatewayer (Modbus, MQTT, OPC UA, BACnet): se [Protokoller og gatewayer](11-protocols-gateways.md). Bruk `var/protocols.yaml`, slik `config.example.yaml` gjør: systemd-tjenesten får bare skrive i `var/`, så F11 kan bare lagre filen der. |

**Hvorfor `status` er standard, og `all` ikke anbefales.** En GroupValueRead på en *styreadresse*
(f.eks. "Lys gang kobling") besvares av alle enheter som har leseflagget (R) på den adressen – også av en
trykknapp eller logikkmodul som fortsatt har en gammel verdi. Aktorer der objektet har oppdateringsflagget (U),
behandler et slikt svar som en skriving, så en lesing kan slå av et lys. `status` leser derfor aldri en adresse
som brukes til å styre noe; den leser bare tilbakemeldings-, føler- og alarmadresser, så verdiene er ferske rett
etter oppstart. Med `false` sender serveren ingenting ved oppstart: de siste verdiene fra databasen vises
dempet og i kursiv («ikke lest ennå») til bussen sender en ny, og de sendes aldri videre til PLS-broen eller til
gatewayer.

Databasen vokser med trafikken, ca. 115 byte per telegram. Ved 1–2 telegrammer/s blir det ca. 10–20 MB
per døgn, og 0,9–1,8 GB med `retention_days: 90`.

## Alarmer

`alarms:` er en liste med regler. For hver gruppeadresse brukes **den første regelen som passer**, så sett
spesifikke regler foran generelle.

```yaml
alarms:
  - match_address: "0/6/*"          # adressemønster (valgfritt)
    match_name: "(?i)alarm$"        # regex mot GA-navnet (valgfritt)
    exclude_name: "(?i)acknowledg"  # regex som utelukker (valgfritt)
    dpt_main: 1                     # hoved-DPT (valgfritt)
    condition: "true"               # når regelen er aktiv
    severity: critical              # critical | warning (alle navn er tillatt)
    text: "Røykalarm: {name}"       # {name}, {address}, {value}
```

`address` godtas som alias for `match_address`. `dpt_main` må være et tall (`1`, ikke `"1"`). En regel
der `condition` ikke har noen av formene under, blir aldri aktiv – og du får ingen advarsel. En regel uten
`condition` bruker `"true"`, uten `text` brukes GA-navnet. Plassholderadresser og adresser uten noe tilknyttet
kommunikasjonsobjekt overvåkes aldri.

`delay_s: 60` gir alarm først når tilstanden har vart i 60 sekunder. Taggene i `protocols.yaml` (Modbus, OPC UA,
MQTT, BACnet) får alarmer fra regler med `match_point: "tilkobling/tagg-mønster"` – se
[Alarmer på tagger](11-protocols-gateways.md#alarmer-på-tagger).

| `condition` | Aktiv når |
|---|---|
| `true` / `on` / `1` | verdien er sann, for eksempel DPT 1 = 1 |
| `false` / `off` / `0` | verdien er usann. Nyttig for «OK»-signaler som faller ut. |
| `> 30`, `< 5`, `>= x`, `<= x`, `== x`, `!= x` | tallverdien oppfyller betingelsen (`.` eller `,` som desimaltegn) |

**Alvorlighet:** `critical` og `warning` er standardnivåene. Kritiske alarmer vises i rødt og tar over
skjermspareren med «KRITISK ALARM». De norske navnene `kritisk` og `advarsel` fra eldre konfigurasjoner godtas
fortsatt og gjøres om til `critical` og `warning`. Andre navn er tillatt og vises som de er skrevet. Er
`severity` utelatt, brukes `warning`.

**Tekster:** `text` brukes som den er skrevet (den oversettes ikke), så skriv den på det språket du vil se.
`{name}` er GA-navnet fra ETS, `{address}` gruppeadressen og `{value}` den formaterte verdien.

**Livsløp:** En alarm går når betingelsen blir sann, og er opphørt når den blir usann. Den kvitteres av en
bruker. Alarmene lagres i databasen og overlever en omstart.

Slik sjekker du hvilke adresser reglene treffer:

```bash
venv/bin/python -c "
from knxscada.config import load_config; from knxscada.project import load_project; from knxscada.alarms import build_rules
c=load_config(); m=load_project(c)
for a,r in build_rules(c.alarms, m['gas']).items(): print(a, m['gas'][a]['name'], '→', r.severity, r.condition)"
```

## `ui` – visualisering

| Nøkkel | Standard | Beskrivelse |
|---|---|---|
| `title` | prosjektnavnet | Tittel i toppfeltet |
| `building` | `Hovedbygg` | Navnet på ETS-bygget i menyen når `project.point_rooms` legger til et annet bygg |
| `language` | `en` | Språk: `en` (engelsk) eller `nb` (norsk bokmål; `no` godtas som alias). Også språket i tekstene serveren lager til hendelsesloggen og alarmene. Hver brukers eget valg (Ctrl+L) og `./scada tui --language` går foran i visualiseringen. |
| `theme` | `retro` | Standard fargetema: `retro`, `retro-classic`, `amber`, `modern`, `solarized`, `storm`. Brukerens eget valg for denne installasjonen (Ctrl+T) går foran. De gamle navnene `retro-klassisk` og `moderne` godtas fortsatt. |
| `logo_file` | – | En egen logo til oppstartsbildet og Om (en tekstfil). Temaet `storm` viser alltid Storm Automasjon-logoen i stedet. Tegnes med `█ ▀ ▄` (heltrukket, i hovedfargen til temaet) og `▒` (skygge); blokker skilles med en tom linje – når logoen er for stor for vinduet, vises bare den første blokken. |
| `splash` | `true` | Logo ved oppstart |
| `big_digits` | `false` | Store blokksifre i statusfliser og skjermsparer. Vanlig tekst er lettest å lese. |
| `ascii` | `false` | Bare ASCII-tegn i grafer, for eldre terminaler |
| `screensaver_minutes` | `5` | Minutter uten betjening før skjermspareren starter. `0` slår den av. |
| `screensaver_rotate_s` | `20` | Sekunder per side i skjermspareren |
| `screensaver_pages` | alle | Sidene i skjermspareren og rekkefølgen, f.eks. `[clock, sky, heatmap, meters, records]`. Nøkler: `clock`, `home`, `sky`, `temps`, `ventilation`, `heatmap`, `energy`, `meters`, `rain`, `air`, `records`, `knx`, `bounce` (se brukerveiledningen). Ukjente nøkler ignoreres. |
| `latitude`, `longitude` | `59.22`, `10.93` (Fredrikstad) | Posisjonen for soloppgang, solnedgang og månen på skjermsparersiden `sky` (desimalgrader) |
| `plan` | `plan.yaml` | Fila med plantegningen |
| `export_dir` | `~` | Hvor eksporten fra busmonitoren (`e`: ETS XML, CSV eller JSON) lagres. I Docker: f.eks. `/data/export`. |
| `outdoor_ga` | auto | Gruppeadressen til utetemperaturen. Auto finner en DPT 9-adresse der navnet begynner med "ute temp", "outdoor" eller "outside". Finnes den ikke, brukes `outdoor`-taggen til det første aggregatet i `ui.systems`. |
| `power_ga` | auto | Effekt (DPT 14.056) til statusflisen. Auto finner et navn som slutter på "power" eller "W", eller som inneholder "effekt". Finnes den ikke, roterer flisen gjennom energimålerne i `ui.systems`. |
| `energy_ga` | auto | Energimåler (DPT 13.x). Auto finner et navn som inneholder "kwh" eller "energy". |
| `setpoint_step` | `0.5` | Steg for − / + på settpunkter |
| `dim_step` | `10` | Steg for − / + på dimmere |
| `confirm_raw_write` | `true` | Bekreftelse før skriving fra gruppeadressevisningen |

Et ukjent `language` faller tilbake til engelsk.

### Anleggsvisning (Ctrl+E)

`ui.systems` beskriver ventilasjonsaggregatene i `protocols.yaml` for visningen **Anlegg** og målerne for
visningen **Energimålere** (Ctrl+G). Hvert aggregat peker på en tilkobling og knytter taggene til roller; `prefix`
settes foran hvert taggnavn. Hvert aggregat får en oppføring under Anlegg i menyen: `short`, ellers `360.001`-delen
av et navn som begynner med `=360.001`, ellers navnet.

```yaml
ui:
  systems:
    - name: "=360.001 Ventilasjon 1. etg"
      short: "360.001"                  # oppføring i menyen – valgfri
      type: ventilation
      connection: vent1                 # en tilkobling i protocols.yaml
      prefix: BY_360_001_
      meter: meter1                     # energimåleren til aggregatet (en tilkobling med målermal) – valgfri
      tags: {outdoor: RT900_PV, supply: RT401_PV, extract: RT501_PV, setpoint: AktReg_SP,
             supply_fan: JV401_OP, extract_fan: JV501_OP, recovery: LX001_OP, heating: LV001_OP,
             cooling: LK001_OP, alarm: SYS_AL, filter_supply: QD401_AL, filter_extract: QD501_AL, mode: SD_CMD}
      mode_texts: {0: Auto, 1: Manuell av, 2: Manuell på}
      status_texts: {12: Tidsprogram}   # tekst per verdi av status-taggen – valgfri
      alarm_normal: 1                   # verdien av alarm-taggen som betyr «ingen alarm» – valgfri
      settings: ["*_SP", "*_Set*"]      # tagger i fanen Innstillinger (glob) – valgfri, standard: alle tagger
      hide: [Act_Time, DayOfWeek]       # aldri i fanen Innstillinger (glob uten prefiks) – valgfri
      groups:                           # grupper i fanen Innstillinger (glob uten prefiks) – valgfri
        - {title: "Drift og alarmer", tags: [SYS_*, SD_CMD, QD401_AL, QD501_AL]}
        - {title: "Tilluftsvifte JV401", tags: [JV401_*]}
    - name: Energi
      type: energy
      meters:
        - {name: Hovedtavle, connection: meter_main}
        - {name: Ventilasjon 1. etg, connection: meter1, tags: {pf: "Power factor"}}
```

Roller for et aggregat: `outdoor` (ute/inntak), `supply` (tilluft), `extract` (avtrekk), `exhaust` (avkast),
`setpoint`, `supply_fan`, `extract_fan`, `supply_fan_run`, `extract_fan_run`, `recovery` (gjenvinner), `heating`,
`cooling`, `supply_flow`, `extract_flow`, `supply_pressure`, `extract_pressure`, `running`, `alarm`,
`filter_supply`, `filter_extract`, `mode`, `status`. Utelat det aggregatet ikke har. «I drift» er `running`,
ellers `supply_fan_run`, ellers `supply_fan` > 0.
BACnet multi-state-verdier er tall (1, 2, 3 …): `status_texts` viser en tekst for verdien av `status` (Climatix
`ActOpSta` 12 = «Tidsprogram»), og `alarm_normal` gir verdien av `alarm` som betyr «ingen alarm» (en alarmklasse
med 1 = Normal, 2 = Alarm – uten den regnes hver verdi over 0 som alarm). Tekstene i en enhet kan leses med
`property: state-text` på en tagg.
Ukeprogrammet vises når `week_start_hour` og `week_stop_hour` er satt: tabeller med én verdi per dag, mandag først
(`week_start_minute`, `week_stop_minute`, `week_enabled` valgfrie), og `overtime` (av/på).

#### Rørskjema (`type: diagram`)

Vannbårne anlegg ser forskjellige ut på hvert anlegg, så tegningen skrives som tekst, slik en HMI-side tegnes, og
de levende verdiene settes inn i den:

```yaml
    - name: "=320.002 Gulvvarme"
      type: diagram
      connection: pls1
      prefix: "320_002_"
      diagram: |2
         tur   ━━━━ SB501 {ventil} ━━ {p1 } JP401 ━━━━━━━━▶ RT401 {t_tur  }
         retur ◀══════════════════════════════════════════ RT501 {t_ret  }
      fields:
        ventil: {tag: RelPos, connection: ventil1, decimals: 0}   # en tagg i en annen tilkobling
        p1: {kind: pump, run: JP401_V, alarm: [JP401_AL], safety: JP401_SB}
        t_tur: {tag: RT401_PV, alarm: [RT401_L_AL, RT401_H_AL]}
        t_ret: {tag: RT501_PV}
      charts:                           # grafene for siste døgn: forklaring → felt
        temperatures: {"Tur": t_tur, "Retur": t_ret}
        outputs: {"Ventil %": ventil}
```

- `{nøkkel}` byttes ut med feltet *nøkkel*, like bredt som plassholderen selv (med klammene): mellomrom inne i
  klammene gjør feltet bredere, så tegningen holder kolonnene. Skriv `diagram: |2`, så kan første linje begynne
  med mellomrom.
- Tykke strektegn (`━ ┃ ┏ ┓ ┗ ┛ ┣ ┫ ┳ ┻ ╋`) er tur (rødt), doble (`═ ║ ╔ ╗ ╚ ╝ ╠ ╣ ╦ ╩ ╬`) retur eller kaldtvann
  (blått); pilene `▶ ◀ ▲ ▼` får fargen til røret foran.
- Felttyper (`kind`): `value` (standard; `decimals`, `unit: false`), `pump` (`run`, `alarm`, `safety`), `state`
  (en av/på-tagg vist med tekstene `on`/`off`) og `text` (`texts` for tallene i en multi-state-tagg). `alarm` er
  en tagg eller en liste: er én av dem på, blir feltet rødt. Taggene får oppføringens `prefix`; et felt med egen
  `connection` leser den tilkoblingen uten prefiks (eller med egen `prefix`). `align` (`left` / `center` /
  `right`, standard `center`) plasserer teksten i feltet.
- `groups`, `settings` og `hide` virker som for et aggregat (fanen Innstillinger).

En måler bruker taggnavnene i utstyrsmalene (`Total active power`, `Total active energy`, `L1 current` …,
`L1 voltage` …, `Power factor`, `Frequency`); `tags:` overstyrer enkeltroller (`power`, `energy`, `i1`–`i3`,
`u1`–`u3`, `pf`, `freq`). Kurvene for en måler krever at effekt- og energitaggene logges (`log` i
`protocols.yaml`); grafene for et aggregat krever at temperaturene og pådragene logges.

`type: panel` er et anlegg uten tegning – f.eks. en KNX→BACnet-gateway: det får en oppføring under Anlegg i menyen
og åpner i fanen Innstillinger med taggene i `groups` (de skrivbare med Enter). Roller brukes ikke.

#### Effektstyring (`type: power`)

En effektstyring (maksvokter) i en PLS kobler ut laster (varmtvannsbereder, gulvvarme, elbillader) når forbruket
i bygget nærmer seg en grense. Kortet viser forbruket som en stolpe mot grensen (grønn, gul fra 80 %, rød over;
rammen blir rød over grensen), hvor mye som er ledig, hvor mange laster som er koblet ut, månedens høyeste forbruk
og én linje per last. Grafene viser forbruk og grense, og antall laster koblet ut (logg de taggene). Kortet bare
leser; innstillingene skrives i fanen Innstillinger.

```yaml
    - name: Effektstyring
      type: power
      connection: plc2
      tags:                                       # alle valgfrie
        consumption: {connection: knx, tag: "0/1/30"}   # finnes også på KNX: les den der
        setpoint: forbrukSetpunkt
        overuse: overforbruk                      # forbruk − grense (negativ = ledig)
        shed_count: PRG_iTotalLoadsDisconnected
        peak: {connection: plc2_bacnet, tag: rkWMaxMoth}
      loads: 9                                    # eller en liste med navn: [VVB, Bad, …]
      load_tags:                                  # {n} = 1, 2, …
        name: "sLast_{n}_Navn"                    # navnet i PLS-en (en liste i loads vinner)
        shed: "Last_{n}_kobletUt"
        status: "Last_{n}_status"
        count: "Last_{n}_gangerUtkoblet"
        min_off: "Last_{n}_minUtkoblingstid"
        max_off: "Last_{n}_maksUtkoblingstid"
        min_on: "Last_{n}_minInnkoblingstid"
      time_unit: min
      groups:                                     # fanen Innstillinger, som for et panel
        - {title: "Last 1", tags: ["Last_1_*"]}
```

En tagg er et navn i anleggets tilkobling (med `prefix`) eller `{connection: …, tag: …}` for en annen tilkobling.

For elektriske varmelaster kan `loads` være en liste med `tags` per last (over `load_tags`): `relay` (aktuatorens
status) gir «Relé» (en utkoblet last der reléet fortsatt melder på, merkes ⚠; `relay_inverted: true` på en last
der tilbakemeldingen er på når lasten er koblet ut, f.eks. en kontaktor foran en varmtvannsbereder); `temp` (romtemperaturen) og `heat`
(termostatens varmebehov) gir «Rom» og «Varmebehov». En last som er koblet
ut mens rommet vil ha varme, viser «♨ ønskes» i varselfargen.

```yaml
      loads:
        - {}                                                  # 1: varmtvannsbereder – intet rom
        - {tags: {temp: {connection: knx, tag: "3/0/1"}, heat: {connection: knx, tag: "3/2/15"}}}
```

Fanen Innstillinger: en tagg havner i den første av `groups` den passer med. Tagger utenfor gruppene samles
automatisk etter TFM-kode (`JV401_OP` → `JV401`) eller første ledd i navnet (`Sequence_Kp1` → `Sequence`) når minst
to tagger har det samme; gruppen får navn etter ordene beskrivelsene begynner med. Resten havner under «Annet».
Gruppenavnet – eller tittelen uten TFM-koden til slutt – tas bort fra hver beskrivelse (`Tilluftsvifte pådrag` i
«Tilluftsvifte JV401» vises som `Pådrag`). Sett titler med komma i anførselstegn.

### Personlige innstillinger

Hver bruker har `~/.config/knxscada/ui.json` (eller `$XDG_CONFIG_HOME/knxscada/ui.json`) med sitt eget valg av
fargetema og språk. Fila kan trygt slettes.

<a id="plc"></a>

## `plc` – PLS-broen over OPC UA

Valgfritt. Utveksler KNX-verdier med en PLS (f.eks. CODESYS på en WAGO PFC). Veiledning: [PLS-broen](10-plc-opcua.md).

| Nøkkel | Standard | Beskrivelse |
|---|---|---|
| `enabled` | `false` | Start broen sammen med serveren. Med `controllers` (under) er hver PLS på, med mindre den selv eller `plc.enabled` sier `false`. |
| `name` | `plc` | Navnet på PLS-en i toppfeltet og hendelsesloggen |
| `controllers` | – | Flere PLS-er (se under) |
| `endpoint` | `opc.tcp://127.0.0.1:4840` | OPC UA-serveren i PLS-en |
| `username`, `password` | – | Bruker i PLS-runtimen. Uten `username` er tilkoblingen anonym. |
| `security` | `None` | `None`, eller `<policy>,Sign` / `<policy>,SignAndEncrypt` med policy `Basic256Sha256`, `Aes128Sha256RsaOaep` eller `Aes256Sha256RsaPss` |
| `certificate`, `private_key` | `var/opcua/client_cert.der`, `var/opcua/client_key.pem` | Klientsertifikat for `security`; lages automatisk (nøkkelen med modus 600). PLS-en må stole på det. |
| `server_certificate` | – | Sertifikatet til OPC UA-serveren i PLS-en (`.der`/`.pem`, f.eks. eksportert fra CODESYS). Med det snakker broen bare med den PLS-en; uten det godtas sertifikatet PLS-en tilbyr, så en enhet på nettet kan utgi seg for å være PLS-en. Anbefales sammen med `security`. |
| `map` | `plc_map.yaml` | Hvilke gruppeadresser som utveksles, retningen og navnene på PLS-variablene (`./scada plc-map`) |
| `gvl` | `GVL_KNX` | Navnet på GVL-en i PLS-en. Brukes bare når `plc_map.yaml` lages første gang; etter det gjelder `gvl:` øverst i `plc_map.yaml`. |
| `gvl_file` | `<gvl>.st` | Hvor `./scada plc-map` skriver GVL-en |
| `node_prefix`, `namespace` | finnes automatisk | Node-id-prefiks for GVL-variablene (f.eks. `\|var\|<device>.Application.GVL_KNX.`) og namespace-indeksen – trengs bare hvis det automatiske søket ikke finner GVL-en. Med `node_prefix` og uten `namespace` er namespace `4`. |
| `publish_interval_ms` | `200` | Hvor ofte PLS-en melder endringer |
| `min_interval_ms` | `200` | Høyst én KNX-skriving per adresse i denne tiden; siste verdi vinner |
| `timeout_s` | `10` | Tidsavbrudd for tilkoblingen |
| `missing_recheck_s` | `60` | Hvor ofte det sjekkes om variabler som mangler i PLS-en, har dukket opp (etter en online change) |
| `settle_ms` | `500` | Endringer fra PLS-en holdes så lenge før de sendes til KNX (for å kjenne igjen en PLS-omstart) |
| `burst_limit` | `8` | Endres flere variabler enn dette innen `settle_ms`, er det en PLS-omstart (nedlasting, reset): ingenting sendes. `0` = av |
| `restart_quiet_s` | `15` | Etter en PLS-omstart blir endringer bare husket så lenge; deretter får PLS-en KNX-verdiene igjen |
| `tag_prefix` | – | Gi variablene navn etter systemtaggen til taggede PLS-objekter i ETS (se [PLS-broen](10-plc-opcua.md)) |

### Flere PLS-er

List dem under `controllers`. Nøkler rett under `plc:` er da standard for alle PLS-er (bruker, sikkerhet,
tider …); `name`, `endpoint`, `map`, `gvl`, `gvl_file`, `node_prefix`, `namespace`, `server_certificate` og
`enabled` hører til hver enkelt PLS.

```yaml
plc:
  enabled: true
  username: knx                         # standard for alle PLS-er
  password: "secret"
  controllers:
    - name: pfc300                      # bokstaver, sifre, _ – vises i toppfeltet og hendelsesloggen
      endpoint: opc.tcp://192.168.1.100:4840
    - name: board2
      endpoint: opc.tcp://192.168.1.101:4840
      gvl: GVL_KNX_Board2
      # enabled: false                  # behold oppsettet, men ikke koble til
```

`enabled` på en PLS vinner; uten den gjelder `plc.enabled`, og uten den igjen er PLS-en **på**. Gi hver PLS sin
egen `gvl` – to PLS-er med standard `GVL_KNX` ville skrevet samme `GVL_KNX.st`.

Hver PLS har sin egen kobling, som standard `plc_map_<name>.yaml`, og sin egen GVL; `./scada plc-map` lager eller
oppdaterer alle (`--plc board2` for én), `./scada plc-browse --plc board2` sjekker én. Hver PLS leser og
skriver KNX uavhengig av de andre, med alle reglene til broen. To PLS-er kan skrive samme gruppeadresse – siste
skriving vinner, så gi en slik adresse `dir: both` i bare én PLS. Toppfeltet viser én prikk per PLS, og
skrivinger fra PLS-er vises i hendelsesloggen med brukeren `plc:<name>`.
