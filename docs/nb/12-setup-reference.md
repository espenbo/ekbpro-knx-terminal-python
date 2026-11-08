[English](../en/12-setup-reference.md) · **Norsk**

# Oppsettsreferanse (for folk og AI-assistenter)

Denne siden har alt som trengs for å sette opp programmet fra bunnen: hvilke filer som finnes, komplette eksempler,
alle nøkler med type, standardverdi og gyldige verdier, oppgaver steg for steg og kommandoene som sjekker resultatet.
Den er skrevet slik at en AI-assistent kan gjøre oppsettet ved å følge den. De andre sidene forklarer *hvorfor*; denne
siden er sjekklisten.

## Regler for oppsett

1. **Bruk bare nøkler som står på denne siden.** Ukjente nøkler ignoreres uten varsel – en feilstavet nøkkel får
   stille sin standardverdi.
2. **Stier er relative til mappen `config.yaml` ligger i**, ikke til gjeldende mappe (unntatt utfilen til
   `plan-dxf`). `~` virker.
3. **Sett anførselstegn rundt tekst som inneholder `*`, `:`, `{`, `#`, `?` eller starter med et siffer og en skråstrek**: `"0/6/*"`,
   `"3/1/0"`, `"(?i)alarm$"`. I regulære uttrykk i doble anførselstegn skriver du `\\b` for `\b`.
4. **YAML gjør noen ord om til boolske verdier**: `on`, `off`, `yes`, `no`, `true`, `false`. Sett anførselstegn rundt dem når du mener
   tekst (`condition: "true"` går fint begge veier, men et rom som heter `No`, må skrives `"No"`).
5. **Tall må være tall**: `dpt_main: 1`, ikke `"1"`.
6. **Hemmeligheter** (passord til KNX-keyring, PLS-passord, MQTT-/OPC UA-passord) ligger i `config.yaml` og
   `protocols.yaml`. Begge skrives med modus 600 av programmet; la det være slik.
7. **Start på nytt etter endringer**: `knx`, `project`, `server`, `alarms`, `plc` → start serveren på nytt. `ui` → start
   TUI-en på nytt. `protocols.yaml` → lagret i F11 gjelder den med en gang; redigert for hånd trykker du `r` i F11 eller starter
   serveren på nytt. `plan.yaml` → leses på nytt hver gang F8 åpnes.
8. **Sjekk etter hver endring** med kommandoene i [Sjekk oppsettet](#sjekk-oppsettet). Meld aldri et oppsett som
   ferdig før `./scada info` og serverloggen er uten feil.
9. **Endre aldri disse uten at du blir bedt om det:** KNX-bussen (ingen testskrivinger), PLS-programmet, systemd-enheter
   (krever `sudo`) og `read_on_start: all` (kan koble aktuatorer).

## Filer

| Fil | Hvor | Skrevet av | Hva den inneholder |
|---|---|---|---|
| `config.yaml` | programmappen, eller `sites/<site>/` | `./scada init`, for hånd | KNX-forbindelse, ETS-prosjekt, server, alarmer, UI, PLS-broen |
| `*.knxproj` / `*.knxprojarchive` | ved siden av `config.yaml` | ETS-eksport | ETS-prosjektet |
| `protocols.yaml` | `var/` (`server.protocols: var/protocols.yaml`) | F11, for hånd | Modbus-, MQTT- og OPC UA-tilkoblinger og gatewayer |
| `plan.yaml` | ved siden av `config.yaml` (`ui.plan`) | for hånd | Plantegning for F8 |
| `plc_map.yaml`, `GVL_KNX.st` | ved siden av `config.yaml` | `./scada plc-map`, så for hånd (`use:`) | PLS-broen: hvilke adresser, retning, variabelnavn |
| `var/` | ved siden av `config.yaml` | serveren | `knxscada.sock`, `knxscada.db`, `project_cache.json`, `opcua/`-sertifikater |
| `~/.config/knxscada/ui.json` | hjemmemappen til hver bruker | TUI-en | Fargetema og språk for den brukeren |

Maler i programmappen: `config.example.yaml`, `protocols.example.yaml`, `plan.example.yaml`.

**Ett anlegg eller flere.** En maskin med én installasjon har `config.yaml` i programmappen (tjenesten
`knxscada`). En maskin med flere har hvert anlegg i `sites/<site>/` med egen `var/` (tjenesten
`knxscada@<site>` eller `knxscada-<site>`), og alle kommandoer tar `-c sites/<site>/config.yaml`. Et skript
`/usr/local/bin/knx-<site>` med innholdet `exec <program folder>/scada -c <program folder>/sites/<site>/config.yaml "$@"`
gjør at `knx-<site>` åpner TUI-en for det anlegget og `knx-<site> info` kjører kommandoene dets.

## Kommandoer

Alle kommandoer: `./scada [-c CONFIG] <command>`. Uten `-c` er filen `$KNXSCADA_CONFIG`, ellers `config.yaml` i
programmappen. Uten kommando starter `tui`.

| Kommando | Valg | Gjør |
|---|---|---|
| `tui` | `--language en\|nb` | Terminalbrukergrensesnittet (krever en server som kjører) |
| `server` | `--demo` (simulert buss, egen database), `-v` (debuglogg) | KNX, logging, alarmer, PLS-broen, protokoller, socket |
| `setup` | – | Spørsmål i terminalen: ETS-prosjekt, KNX-tilkobling (søk, USB), moduler, utstyrssøk, config.yaml, tjeneste |
| `init PROJECT` | `--gateway IP`, `--connection TYPE`, `--password PW`, `--keyring FIL`, `--keyring-password PW`, `--user-id N`, `--usb-device ENHET`, `--language en\|nb`, `--force` | Skriver `config.yaml` fra malen og importerer prosjektet |
| `import` | | Leser ETS-prosjektet/-prosjektene på nytt og bygger cachen på nytt |
| `info` | | Sammendrag av prosjektet: adresser, kort, etasjer og rom |
| `plan-dxf [FILE]` | | Plantegningen til DXF (standard `plan.dxf` i gjeldende mappe) |
| `plc-map` | `--all`, `--marked`, `--rename`, `--plc NAME` | Skriver `plc_map.yaml` og GVL-en |
| `plc-browse [FILTER]` | `--endpoints`, `--plc NAME` | Lister PLS-ens OPC UA-variabler / endpoints |
| `plc-gateway` | `--plc NAME`, `--prefix`, `--namespace`, `--write` | PLS-broen (`plc:` + `plc_map.yaml`) som OPC UA-tilkobling og gatewayer; skrives ut, eller legges inn i protocols.yaml med `--write` |
| `modules` | `install NAME …` | Lister protokollmodulene, eller installerer dem |
| `gateway` | | Sjekker `protocols.yaml`, med direktestatus når serveren kjører |
| `simulate [MODELL]` | `--host`, `--port` (5020), `--unit` (1), `--device` | Spiller en utstyrsmal som Modbus-slave, for test uten utstyr; uten modell lister den malene |
| `service install\|remove\|status` | `--name NAVN` | Serveren som systemd-tjeneste fra `deploy/knxscada.service` for denne konfigurasjonen; uten root skrives `sudo`-kommandoene ut |
| `knx-usb [ENHET]` | `--listen SEKUNDER` | KNX USB-grensesnittene (sti, USB-ID, serienummer, tilgang); med `--listen` telegrammene gjennom ett av dem, ingenting sendes |
| `bacnet-scan [ADRESSE]` | `--device N`, `--local`, `--port`, `--bbmd`, `--timeout` (3), `--yaml` | Uten adresse: BACnet-enhetene på nettet (Who-Is); med adresse: objektene i enheten, `--yaml` som en tilkobling for `protocols.yaml` |

`./setup.sh [knx modbus mqtt opcua bacnet]` lager `venv/`, installerer grunnpakkene og protokollmodulene du nevner
(ingen nevnt: den spør, standard `knx`), og kjører `./scada import` når `config.yaml` finnes og KNX er installert.
Grunnpakker: `textual`, `textual-plotext`, `PyYAML`. Moduler: `knx` (`xknx`, `xknxproject`), `modbus`, `mqtt`,
`sparkplug`, `opcua` (`asyncua`, trengs også av PLS-broen `plc:`), `bacnet`.

## Oppgaver

### A. Ny installasjon med KNX

```bash
git clone <repository> ~/knx_scada && cd ~/knx_scada
./setup.sh knx                               # venv, grunnpakker og KNX-modulen; legg til modbus mqtt opcua ved behov
./scada init ~/MyHouse.knxproj --gateway 192.168.1.50 --language nb
./scada info                                 # sjekk: adresser, kort og rom er der
./scada server                               # første test i en terminal; Ctrl+C stopper den
./scada                                      # TUI i en annen terminal
```

`--gateway` tom = automatisk søk. `--connection`: `tunneling` (standard med gateway), `tunneling_tcp`,
`routing`, `automatic`, `tunneling_secure`, `routing_secure`, `usb` – den sjekkes ikke av `init`, en skrivefeil viser seg først når
serveren starter. Installer så tjenesten: [Drift og SSH](05-operation-and-ssh.md).

### B. Et anlegg til på samme maskin

```bash
mkdir -p sites/cabin && cp ~/Cabin.knxproj sites/cabin/
./scada -c sites/cabin/config.yaml init sites/cabin/Cabin.knxproj --gateway 10.0.0.20
./scada -c sites/cabin/config.yaml info
sed -e "s#YOUR_USER#$USER#g" -e "s#INSTALL_DIR#$PWD#g" deploy/knxscada@.service \
    | sudo tee /etc/systemd/system/knxscada@.service
sudo systemctl daemon-reload && sudo systemctl enable --now knxscada@cabin
journalctl -u knxscada@cabin -n 30          # sjekk: "Server startet (KNX)", KNX tilkoblet
```

### C. KNX Secure

Eksporter keyringen fra ETS (*Project → Security → Export keyring*) og legg den ved siden av `config.yaml`:

```yaml
knx:
  connection: tunneling_secure     # eller routing_secure
  gateway_ip: 192.168.1.50
  keyring_file: MyHouse.knxkeys
  keyring_password: "secret"
  # user_id: 2                     # tunnelbruker, valgfri
```

Når `keyring_file` er satt, er forbindelsen sikker uansett hva `connection` sier.

### D. Kjøre uten KNX (bare Modbus/MQTT/OPC UA/BACnet)

Installer uten KNX-modulen – `./setup.sh modbus opcua` (eller modulene du trenger) – så blir `xknx` og `xknxproject`
ikke installert i det hele tatt.

```yaml
# config.yaml
knx: {enabled: false}
server:
  socket: var/knxscada.sock
  database: var/knxscada.db
  protocols: var/protocols.yaml
ui:
  title: Energigateway
  language: en
```

Så oppgave E, og start med `./scada server`. TUI-en åpner på F11.

### E. Energimåler på RS485 til en OPC UA-server

```bash
./scada modules install modbus opcua
```

```yaml
# var/protocols.yaml
connections:
  meter1:
    protocol: modbus
    device: /dev/ttyUSB0                 # RS485-adapteren
    model: WAGO 879-3000                 # tagger og serieinnstillinger fra utstyrsmalen
    # unit_id: 2                         # bare hvis Modbus-adressen til måleren ikke er 1
  scada:
    protocol: opcua
    role: server
    endpoint: opc.tcp://0.0.0.0:4841     # 0.0.0.0 = tilgjengelig fra nettet

gateways:
  - name: Meter to SCADA
    from: meter1
    to: scada
    map:                                 # tagnavn i malen -> navn i merkesystemet ditt
      - Total active power -> 320.001-OE001.P
      - Total active energy -> 320.001-OE001.E
      - L1 current -> 320.001-OE001.I1
```

Sjekk: `./scada gateway` (ingen linjer med `!`), start serveren på nytt, så viser hendelsesloggen
`meter1: fant WAGO 879-3000 …`; F11 viser verdiene. Brukeren som kjører serveren, må ha lov til å åpne
serieporten (gruppen `dialout`).

### F. PLS-broen (KNX ↔ CODESYS over OPC UA)

```bash
./scada plc-map                          # skriver plc_map.yaml og GVL_KNX.st
# rediger plc_map.yaml: use: true på adressene PLS-en trenger, og så:
./scada plc-map                          # oppdaterer GVL-en
./scada plc-browse --endpoints           # svarer PLS-en? hvilken sikkerhet?
```

Importer `GVL_KNX.st` i PLS-prosjektet, legg den til i Symbol Configuration, last ned, og så:

```yaml
plc:
  enabled: true
  endpoint: opc.tcp://192.168.1.100:4840
  username: knx
  password: "secret"
```

Start serveren på nytt. Sjekk: `./scada plc-browse GVL_KNX` lister variablene; topplinjen viser `PLC` i grønt.
Veiledning: [PLS-broen](10-plc-opcua.md).

### G. Alarmer

Legg til regler under `alarms:` (første treff vinner, spesifikke regler først), start serveren på nytt, og list hvilke
adresser hver regel fanger med enlinjeren i [Konfigurasjon → Alarmer](04-configuration.md#alarmer).

## Komplett `config.yaml`

Alle nøkler, med standardverdi der den finnes. Nøkler merket *valgfri* kan utelates.

```yaml
knx:
  enabled: true                       # valgfri; false = ingen KNX (bare protocols.yaml)
  connection: automatic               # tunneling | tunneling_tcp | routing | automatic | tunneling_secure | routing_secure | usb
  gateway_ip: 192.168.1.50            # trengs ikke for routing/automatic
  gateway_port: 3671
  local_ip: 192.168.1.10              # valgfri; maskiner med flere nett
  route_back: false                   # true bak NAT/VPN (bare tunneling)
  individual_address: 1.1.250         # valgfri; routing
  keyring_file: MyHouse.knxkeys       # valgfri; KNX Secure
  keyring_password: "secret"
  user_id: 2                          # valgfri; sikker tunnelbruker
  name: knx                           # valgfri; vises i TUI-en
  dedup_ms: 1000                      # flere grensesnitt: forkast kopier innenfor denne tiden; 0 = av
  # interfaces:                       # i stedet for tilkoblingsnøklene over, for flere IP-grensesnitt
  #   - name: house                   # ^[A-Za-z0-9_.-]{1,16}$, unik
  #     gateway_ip: 192.168.1.50      # gateway_ip, gateway_port, individual_address: bare per grensesnitt
  #     default: true                 # grensesnittet for adresser som ikke passer noe mønster
  #   - name: garage
  #     gateway_ip: 192.168.1.51
  #     addresses: ["4/*", "5/1/*"]   # sendes gjennom dette grensesnittet
  #     project: annex                # med project.files

project:
  file: MyHouse.knxproj               # eller .knxprojarchive
  password: ""                        # valgfri
  cache: var/project_cache.json
  # files:                            # flere ETS-prosjekter, i stedet for file:
  #   - {name: main, label: Main building, file: Main.knxproj}
  #   - {name: annex, label: Annex, prefix: S, file: Annex.knxprojarchive, password: ""}
  # rooms:                            # rom fra navnene på gruppeadressene (prosjekter uten ETS-funksjoner)
  #   pattern: "(?i)\\b(office\\s*\\d+|kitchen)\\b"
  #   floors: {0: "1st floor", 1: "2nd floor"}
  #   names: {"1st floor/Office 1": "Office 1 · RT101"}
  #   setpoint: "(?i)set temp"
  #   read_only: "(?i)desired temp|valve"
  #   exclude: ["2/*/46"]
  # extra_rooms:                      # rom for adresser som ikke ligger i noe ETS-rom
  #   - floor: Attic
  #     name: Attic
  #     cards:
  #       - {name: Temperature, addresses: ["3/0/35"]}
  #       - {name: Light, addresses: ["1/0/5", "1/0/7"], status: {"1/0/5": "1/4/81"}, usage: light}

server:
  socket: var/knxscada.sock
  database: var/knxscada.db
  retention_days: 90
  read_on_start: status               # status | false | all
  read_interval_ms: 80
  protocols: var/protocols.yaml     # i var/: tjenesten får bare skrive der

alarms:
  - match_address: "0/6/*"            # valgfri glob (alias: address)
    match_name: "(?i)smoke.*alarm"    # valgfritt regex på GA-navnet
    exclude_name: "(?i)acknowledg"    # valgfritt regex
    dpt_main: 1                       # valgfritt tall
    condition: "true"                 # true | false | > x | < x | >= x | <= x | == x | != x
    severity: critical                # critical | warning | hvilken som helst tekst
    text: "Smoke: {name}"             # {name} {address} {value}
  - match_point: "aggregat1/*_AL"     # en tagg i protocols.yaml (mønster på tilkobling/tagg) i stedet for en gruppeadresse
    severity: warning
    delay_s: 60                       # valgfri: så lenge må tilstanden vare

ui:
  title: Mitt hus                     # standard: "EKBPRO KNX Terminal Python – <navnet på ETS-prosjektet>"
  language: en                        # en | nb
  theme: retro                        # retro | retro-classic | amber | modern | solarized | storm
  logo_file: my-logo.txt              # valgfri
  splash: true
  big_digits: false
  ascii: false
  screensaver_minutes: 5              # 0 = av
  screensaver_rotate_s: 20
  screensaver_pages: [clock, sky, home, heatmap, ventilation, meters, records]   # standard: alle sidene
  latitude: 59.22                     # sol og måne på siden «sky» (standard Fredrikstad)
  longitude: 10.93
  plan: plan.yaml
  export_dir: "~"
  outdoor_ga: 0/1/0                   # valgfri; finnes ut fra navnet når den utelates
  power_ga: 0/0/23                    # valgfri
  energy_ga: 0/1/20                   # valgfri
  setpoint_step: 0.5
  dim_step: 10
  confirm_raw_write: true

plc:
  enabled: false
  name: plc
  endpoint: opc.tcp://127.0.0.1:4840
  username: knx                       # valgfri; tom = anonym
  password: "secret"
  security: None                      # None | <policy>,Sign | <policy>,SignAndEncrypt
  certificate: var/opcua/client_cert.der
  private_key: var/opcua/client_key.pem
  server_certificate: var/opcua/plc_cert.der   # valgfri; anbefalt med security
  map: plc_map.yaml
  gvl: GVL_KNX
  gvl_file: GVL_KNX.st
  tag_prefix: BY1234                  # valgfri
  node_prefix: "|var|WAGO 750-8302 PFC300 2ETH RS.Application.GVL_KNX."   # valgfri; finnes automatisk
  namespace: 4                        # valgfri; med node_prefix
  publish_interval_ms: 200
  min_interval_ms: 200
  timeout_s: 10
  missing_recheck_s: 60
  settle_ms: 500
  burst_limit: 8
  restart_quiet_s: 15
  # controllers:                      # flere PLS-er; nøklene over er standardverdier, unntatt de som gjelder per PLS
  #   - {name: pfc300, endpoint: "opc.tcp://192.168.1.100:4840"}
  #   - {name: board2, endpoint: "opc.tcp://192.168.1.101:4840", gvl: GVL_KNX_Board2}
```

## Nøkler i `config.yaml`

### `knx`

| Nøkkel | Type | Standard | Gyldige verdier |
|---|---|---|---|
| `enabled` | bool | på når `knx:` eller `project:` er fylt ut | bare `false` slår av KNX |
| `connection` | tekst | `automatic` | `tunneling`, `tunneling_tcp`, `routing`, `automatic`, `tunneling_secure`, `routing_secure`, `usb`. Alt annet stopper serveren. |
| `gateway_ip` | IP | – | |
| `gateway_port` | int | `3671` | |
| `local_ip` | IP | automatisk | |
| `route_back` | bool | `false` | |
| `individual_address` | tekst | – | `1.1.250` |
| `usb_device` | tekst | det første som finnes | bare `usb`: serienummer, `/dev/hidrawN` eller `vvvv:pppp` |
| `keyring_file` | sti | – | `.knxkeys`; slår på KNX Secure |
| `keyring_password` | tekst | – | |
| `user_id` | int | – | |
| `name` | tekst | `knx` | `^[A-Za-z0-9_.-]{1,16}$` |
| `dedup_ms` | int | `1000` | `0` = av |
| `interfaces` | liste | – | se under |

`interfaces[]`: `name` (standard `gateway_ip`, ellers `knx1` …; unik), `gateway_ip`, `gateway_port`,
`individual_address`, `usb_device` (arves aldri), `connection`, `local_ip`, `route_back`, `keyring_file`, `keyring_password`,
`user_id`, `dedup_ms` (arves fra `knx:`), `addresses` (glob eller liste med glob-er, standard ingen), `default` (bool,
høyst én per prosjekt), `project` (et `project.files[].name`, standard det første prosjektet).

### `project`

| Nøkkel | Type | Standard | Gyldige verdier |
|---|---|---|---|
| `file` | sti | `project.knxproj` | ignoreres når `files` er satt |
| `password` | tekst | – | arves ikke av `files[]` |
| `cache` | sti | `var/project_cache.json` | |
| `files` | liste | – | elementer: `{file (påkrevd), name (p1, p2 …), label (navnet), prefix (navnet; bokstaver/sifre; aldri på det første), password, rooms}` eller bare stien |
| `rooms` | map | – | `pattern` (påkrevd), `floors`, `names`, `setpoint`, `read_only`, `exclude`; brukes bare når prosjektet ikke har ETS-funksjoner |
| `extra_rooms` | liste | – | elementer: `floor` (`Other`), `name` (`Room N`), `cards[]`: `name`, `addresses`, `status`, `usage` (`light`, `dimmer`, `heating`, `other`; gjettes). Et `name` som allerede er et rom i den etasjen, får kortene lagt til (ikke et rom til). En adresse som ikke finnes i prosjektet, stopper programmet. |
| `units` | kart | – | gruppeadresse → enhet som vises i stedet for DPT-ens, f.eks. `{"0/1/17": V}` for volt sendt som DPT 9.020 (mV); ukjente adresser logges og hoppes over |
| `point_rooms` | liste | – | som `extra_rooms` med `building` (grupperer menyen), og `cards[].rows[]`: `point` (`tilk/tagg`, påkrevd), `status`, `label` (taggens beskrivelse), `role` (`temp`, `setpoint`, `heat`), `kind` (fra taggen). Ukjente tagger meldes, stopper ikke. Se [04](04-configuration.md#point-rooms). |

### `server`

| Nøkkel | Type | Standard | Gyldige verdier |
|---|---|---|---|
| `socket` | sti | `var/knxscada.sock` | |
| `database` | sti | `var/knxscada.db` | |
| `retention_days` | tall | `90` | |
| `read_on_start` | tekst/bool | `status` | `status` (også `true`), `false` (også `off`, `no`, `0`), `all`. Alt annet = `status`. |
| `read_interval_ms` | int | `80` | |
| `protocols` | sti | `protocols.yaml` | bruk `var/protocols.yaml` – systemd-tjenesten får bare skrive i `var/` |
| `access` | tabell | – (alle med tilgang til socketen: alt) | `read`, `operate`, `configure` → lister over kontoer, `@gruppe` eller `*`; høyeste nivå som passer vinner, en konto som ikke står noe sted, avvises |

### `alarms[]`

| Nøkkel | Type | Standard | Gyldige verdier |
|---|---|---|---|
| `match_address` (`address`) | glob | alle | `"0/6/*"`, `"S:3/*"` |
| `match_name` | regex | alle | søkes i GA-navnet; et ugyldig regex stopper serveren |
| `exclude_name` | regex | – | |
| `dpt_main` | int | alle | `1`, `9`, `14` … |
| `condition` | tekst | `"true"` | `true`/`on`/`1`, `false`/`off`/`0`, `> x`, `< x`, `>= x`, `<= x`, `== x`, `!= x`. Alt annet utløses aldri. |
| `severity` | tekst | `warning` | `critical`, `warning` (`kritisk`, `advarsel`), hvilken som helst tekst |
| `text` | tekst | `"{name}"` | `{name}`, `{address}`, `{value}` |
| `match_point` | mønster | – | `"aggregat1/*_AL"`: regelen gjelder tagger i `protocols.yaml` (`tilkobling/tagg`), ikke gruppeadresser; `match_name`/`exclude_name` testes da mot taggnavnet og beskrivelsen |
| `delay_s` | desimaltall | `0` | sekunder tilstanden må vare før alarmen kommer |

### `ui`

| Nøkkel | Type | Standard | Gyldige verdier |
|---|---|---|---|
| `title` | tekst | programnavnet – navnet på ETS-prosjektet | |
| `language` | tekst | `en` | `en`, `nb` (`no` = `nb`); ukjent = `en` |
| `theme` | tekst | `retro` | `retro`, `retro-classic`, `amber`, `modern`, `solarized`, `storm` |
| `logo_file` | sti | innebygd | tekstfil med `█ ▀ ▄ ▒` |
| `splash`, `big_digits`, `ascii`, `confirm_raw_write` | bool | `true`, `false`, `false`, `true` | |
| `building` | tekst | `Hovedbygg` | navnet på bygget med ETS-etasjene i menyen når `point_rooms` legger til et annet |
| `systems` | liste | – | ventilasjonsaggregater for Anlegg-visningen (Ctrl+E) og energimålere for visningen Energimålere (Ctrl+G), se [04](04-configuration.md#anleggsvisning-ctrle). Per aggregat: `name`, `short` (oppføring i menyen; standard `=NNN.NNN` fra navnet), `type` (`ventilation` / `diagram` / `panel` / `power` / `energy`), `connection`, `prefix`, `meter`, `tags` (roller), `mode_texts`, `status_texts` (verdi → tekst), `alarm_normal` (tall), `type: diagram`: `diagram` (tekst med `{felt}`), `fields` (`kind` value/pump/state/text, `tag`, `connection`, `prefix`, `decimals`, `unit`, `alarm`, `run`, `safety`, `on`, `off`, `texts`, `align`), `charts` (`temperatures`/`outputs`: forklaring → felt), `settings` (glob-liste for fanen Innstillinger; standard alle tagger), `hide` (glob-liste uten prefiks), `groups` (liste med `{title, tags}`; glob uten prefiks); `type: power`: `tags` (`consumption`, `setpoint`, `overuse`, `shed_count`, `peak`; et navn eller `{connection, tag}`), `loads` (tall, eller liste med navn eller `{name, tags, relay_inverted}`), `load_tags` (`name`, `shed`, `status`, `count`, `min_off`, `max_off`, `min_on`, `relay`, `temp`, `heat`; `{n}` = lastens nummer), `time_unit` (standard `min`); `type: energy`: `meters` (`name`, `connection`, `tags`) |
| `screensaver_minutes`, `screensaver_rotate_s` | tall | `5`, `20` | `0` = ingen skjermsparer |
| `screensaver_pages` | liste | alle | `clock`, `home`, `sky`, `temps`, `ventilation`, `heatmap`, `energy`, `meters`, `rain`, `air`, `records`, `knx`, `bounce`; `ventilation`, `rain` og `bounce` beveger seg (mer trafikk over SSH) |
| `latitude`, `longitude` | tall | `59.22`, `10.93` | desimalgrader, for siden `sky` |
| `plan` | sti | `plan.yaml` | |
| `export_dir` | sti | `~` | |
| `outdoor_ga`, `power_ga`, `energy_ga` | gruppeadresse | finnes ut fra navn og DPT | uten dem: aggregatets `outdoor`-tagg og målerne i `ui.systems` |
| `setpoint_step`, `dim_step` | tall | `0.5`, `10` | |

### `plc`

| Nøkkel | Type | Standard | Gyldige verdier |
|---|---|---|---|
| `enabled` | bool | `false`; med `controllers` er hver PLS på, med mindre den selv eller `plc.enabled` sier `false` | |
| `name` | tekst | `plc` | `^[A-Za-z_][A-Za-z0-9_]*$`; påkrevd og unik i `controllers` |
| `controllers` | liste | – | bare per PLS: `name`, `endpoint`, `map`, `gvl`, `gvl_file`, `node_prefix`, `namespace`, `server_certificate`, `enabled`; andre nøkler hentes fra `plc:` |
| `endpoint` | tekst | `opc.tcp://127.0.0.1:4840` | |
| `username`, `password` | tekst | anonym | |
| `security` | tekst | `None` | `None`, `<policy>,Sign`, `<policy>,SignAndEncrypt`; policy `Basic256Sha256`, `Aes128Sha256RsaOaep`, `Aes256Sha256RsaPss` |
| `certificate`, `private_key` | sti | `var/opcua/client_cert.der`, `var/opcua/client_key.pem` | lages automatisk |
| `server_certificate` | sti | – | |
| `map` | sti | `plc_map.yaml` (`plc_map_<name>.yaml` med `controllers`) | |
| `gvl` | tekst | `GVL_KNX` | brukes bare når koblingsfilen lages første gang |
| `gvl_file` | sti | `<gvl>.st` | |
| `tag_prefix` | tekst | – | |
| `node_prefix`, `namespace` | tekst, int | finnes automatisk (`namespace` `4` med `node_prefix`) | |
| `publish_interval_ms`, `min_interval_ms` | int | `200`, `200` | |
| `timeout_s`, `missing_recheck_s` | tall | `10`, `60` | |
| `settle_ms`, `burst_limit`, `restart_quiet_s` | tall | `500`, `8`, `15` | `burst_limit: 0` = av |

## Nøkler i `plc_map.yaml`

| Nøkkel | Type | Standard | Gyldige verdier |
|---|---|---|---|
| `select` | tekst | `marked` | `marked` (bare `use: true`), `all` |
| `gvl` | tekst | `GVL_KNX` | IEC-navn |
| `addresses[].ga` | gruppeadresse | – | må finnes i prosjektet |
| `addresses[].use` | bool | `false` | |
| `addresses[].dir` | tekst | `to_plc` | `to_plc`, `to_knx`, `both` |
| `addresses[].type` | tekst | ut fra DPT-en | `BOOL`, `REAL`, `SINT`, `USINT`, `UINT`, `INT`, `UDINT`, `DINT`, `STRING(14)` |
| `addresses[].var` | tekst | `GA_<address>_<name>` | `^[A-Za-z_][A-Za-z0-9_]*$`, ikke `__`, unik |

## Nøkler i `plan.yaml`

| Nøkkel | Type | Standard | Gyldige verdier |
|---|---|---|---|
| `floors[].name` | tekst | `""` | |
| `floors[].width`, `.depth` | mm | `8000` | |
| `floors[].rooms[].x`, `.y`, `.w`, `.h` | mm | **påkrevd** | |
| `floors[].rooms[].label` | tekst | `""` | |
| `floors[].rooms[].room` | tekst | – | et romnavn fra ETS (`./scada info`), store eller små bokstaver |

## Komplett `protocols.yaml`

```yaml
connections:
  meter1:                             # navn: ^[A-Za-z][A-Za-z0-9_-]{0,31}$; "knx" er reservert
    protocol: modbus                  # modbus | mqtt | sparkplug | opcua | bacnet
    role: client                      # client (master) | server (slave)
    model: WAGO 879-3000              # valgfri utstyrsmal (liste: 11-protocols-gateways.md)
    transport: rtu                    # tcp | rtu | rtu_over_tcp
    host: 192.168.1.20                # tcp: utstyret (client) / lytteadresse (server)
    port: 502
    device: /dev/ttyUSB0              # rtu
    baudrate: '9600'                  # 1200 2400 4800 9600 19200 38400 57600 115200
    parity: E                         # N | E | O
    stopbits: '1'                     # 1 | 2
    unit_id: 1                        # server: 0 = svar på alle adresser
    poll_ms: 5000                     # client; minst 50
    timeout_s: 3                      # client
    word_order: big                   # big | little
    max_registers: 100                # master; høyst så mange registre per lesing
    max_gap: 8                        # master; 0 = bare registre inntil hverandre
    log: ["Total active power", "Total active energy"]   # tagger som lagres for trender (navn/mønstre, "*" = alle)
    log_deadband: 0.01                # valgfri, standard 0
    log_min_s: 60                     # valgfri, standard 10 s
    tags:                             # kommer i tillegg til taggene i malen
      Day register: {address: 'hr:0x6049', type: float32, unit: kWh, writable: false}

  broker:
    protocol: mqtt
    host: 127.0.0.1
    port: 1883
    username: ""
    password: ""
    tls: false
    client_id: ""                     # tom = automatisk
    topic_prefix: bygg/
    subscribe: ["sensors/#"]          # topics som blir tagger av seg selv
    payload: plain                    # plain | json
    qos: '0'                          # 0 | 1 | 2
    retain: true
    quality: true                     # mål: «quality» i JSON, ellers <topic>/quality
    tags:
      Outdoor: {topic: weather/temp, type: float, json_key: value, unit: °C}

  sparkplug:
    protocol: sparkplug
    role: server                      # server = vår edge node | client = host-applikasjon for en
    host: 127.0.0.1
    port: 1883
    group_id: knxscada
    edge_node: knx                    # server: vår node-id · client: noden som følges (+ = alle)
    rebirth_request: true             # client: be om fødselen etter tilkobling
    qos: '0'                          # 0 | 1
    tags:                             # client: navn, skala, enhet eller writable for valgte metrikker
      Ute: {metric: Outdoor temp, device: "", node: "", type: float, unit: °C, writable: false}

  plc1:
    protocol: opcua
    role: client
    endpoint: opc.tcp://192.168.1.100:4840
    username: ""
    password: ""
    security: None
    publish_interval_ms: 500
    timeout_s: 10
    tags:
      Temp: {node: "ns=4;s=|var|PLC.Application.GVL.rTemp", type: float, unit: °C}

  scada:
    protocol: opcua
    role: server
    endpoint: opc.tcp://0.0.0.0:4841  # utelatt = bare denne maskinen (127.0.0.1)
    namespace: urn:ekbpro:gateway
    username: scada                   # tomt = anonyme klienter, bare lesing
    password: "…"
    security: Basic256Sha256,SignAndEncrypt   # None = ukryptert (meldes ved start når den er på nettet)
    anonymous_write: false            # true = anonyme klienter kan også skrive

  aggregat1:
    protocol: bacnet
    role: client
    address: 192.168.1.40             # eller device_id: 40 (Who-Is); 2:5 = MS/TP 5 bak en ruter på nett 2
    local_address: 192.168.1.10/24    # tom = denne maskinen (klient med address: ruten til enheten)
    poll_ms: 5000
    timeout_s: 3
    cov: true                         # også COV-abonnement
    status_flags: true                # fault/out-of-service = dårlig kvalitet
    priority: 8                       # tom = enhetens standard (16)
    tags:
      Tilluft: {object: ai:1, unit: °C}
      Vifte på: {object: bo:1, priority: 12}

  sd:
    protocol: bacnet
    role: server
    device_instance: 1001             # påkrevd, unik på nettet
    device_name: Gateway bygg A
    local_address: 192.168.1.10/24    # port 47808
    # bbmd: 10.0.0.1:47808             # registrer som «foreign device»

gateways:
  - name: Meter to SCADA              # unik
    from: meter1                      # tilkoblingsnavn (knx finnes når KNX er på)
    to: scada
    direction: forward                # forward | both (standard for linjene)
    # initial: true                   # utelatt = startverdier bare til egne servere; true = også utstyr (aldri KNX); false = ingen
    deadband: 0                       # standard for linjene
    min_interval_ms: 0                # standard for linjene
    # settle_ms: 500                  # omstartsvern mot KNX: holdetid; 0 = av
    # burst_limit: 8                  # flere endringer enn dette fra én tilkobling innen settle_ms = en omstart
    # restart_quiet_s: 15             # etter omstart eller dårlig status: endringer bare husket så lenge
    enabled: true                     # false = av
    map:
      - Total active power -> 320.001-OE001.P                        # kilde -> mål
      - Frequency                                                     # samme navn i målet
      - {from: L1 current, to: 320.001-OE001.I1, scale: 1, deadband: 0.1}
      - {from: Power factor, to: 320.001-OE001.PF, type: float, description: cos phi}
      - "L? voltage -> Voltage.{name}"                               # jokertegn * ? [ ], {name} {address} {description}
```

### Nøkler i `protocols.yaml`

Tilkoblingsinnstillinger for **Modbus**, **MQTT**, **OPC UA** og **BACnet**: se tabellene i
[Protokoller og gatewayer](11-protocols-gateways.md#2-tilkoblinger) – der er de komplette. Kort fortalt:

| Taggnøkkel | Protokoller | Standard | Gyldige verdier |
|---|---|---|---|
| `address` | Modbus | taggnavnet | `hr:N`, `ir:N`, `co:N`, `di:N`, `hr:N.bit`, heks `0x…`, klassisk `40001` |
| `type` | Modbus | `uint16` | `bool`, `int16`, `uint16`, `int32`, `uint32`, `float32`, `int64`, `uint64`, `float64` |
| `type` | MQTT, OPC UA | automatisk | `bool`, `int`, `float`, `str` |
| `unit_id`, `word_order` | Modbus | tilkoblingens | |
| `write_address` | Modbus | – (skrives der den leses) | `hr` / `co` (samme nummer), eller en adresse `hr:N`, `co:N` |
| `log`, `log_deadband`, `log_min_s` | alle | tilkoblingens | lagre verdiene for trender (`log: true`/`false` gjelder foran mønstrene i tilkoblingens `log`) |
| `heartbeat_s` | Modbus-master | – (av) | sekunder; skriver 1, 2, 3 … til taggen (watchdog i utstyret) |
| `topic`, `json_key`, `set_topic` | MQTT | prefiks + navn, –, selve topicen | |
| `node` | OPC UA | taggnavnet | node id |
| `object`, `property` | BACnet | klient: påkrevd, `present-value`; server: neste ledige nummer | `av:3`, `bi:1`, `analog-value,3`; et egenskapsnavn |
| `priority` | BACnet-klient | tilkoblingens | 1–16 |
| `states`, `cov_increment` | BACnet-server | `16`, `0.1` | for `msv`/`msi` og analoge objekter |
| `status_flags` | BACnet-klient | tilkoblingens (`true`) | `true`, `false` |
| `scale`, `offset` | alle | – | verdi = råverdi × scale + offset |
| `unit`, `description` | alle | – | |
| `writable` | alle | `true` (Modbus `ir`/`di` skrivebeskyttet uten `write_address`) | |

| Koblingsnøkkel | Nivå | Standard | Gyldige verdier |
|---|---|---|---|
| `from`, `to` | gateway | påkrevd | tilkoblingsnavn, forskjellige |
| `from` | linje | påkrevd | taggnavn, adresse eller (KNX) unikt ETS-navn; `* ? [` = jokertegn |
| `to` | linje | samme som `from` | med jokertegn en mal med `{name}`, `{address}`, `{description}` |
| `direction` | gateway, linje | `forward` | `forward`, `both` |
| `initial` | gateway, linje | bare egne servere | `true`, `false` |
| `deadband`, `min_interval_ms` | gateway, linje | `0` | tall |
| `settle_ms`, `burst_limit`, `restart_quiet_s` | gateway | `500`, `8`, `15` | omstartsvern for endringer mot KNX; `settle_ms: 0` = av |
| `scale`, `offset`, `invert`, `type`, `unit`, `description` | bare linje | – | `type`/`unit`/`description` teller bare når måltaggen opprettes |
| `enabled` | gateway | `true` | `false` |

Tilkoblinger har ikke `enabled` – fjern eller kommenter ut en tilkobling for å slå den av. Når F11 lagrer filen,
fjernes kommentarer, og nøkler som ikke står her, forsvinner.

## Sjekk oppsettet

| Hva | Kommando | Godt resultat |
|---|---|---|
| ETS-prosjektet | `./scada info` | `Gruppeadresser: N (M i bruk)`, `Kort: …`, én linje per etasje med rommene |
| Serveren starter | `./scada server` (eller `journalctl -u <service> -n 50`) | `Server startet (KNX)`, `KNX-forbindelse: CONNECTED`, ingen traceback |
| Bussen | `./scada` → F4 Busmonitor | telegrammer kommer inn |
| Alarmregler | enlinjeren i [Konfigurasjon → Alarmer](04-configuration.md#alarmer) | hver forventet adresse med sin regel |
| Protokollmoduler | `./scada modules` | hver modul som en tilkobling bruker, sier `installert` |
| `protocols.yaml` | `./scada gateway` | avslutningskode 0, ingen linjer som starter med `!`; når serveren kjører, er hver tilkobling `tilkoblet` |
| En utstyrsmal | hendelsesloggen (F6) | `<connection>: fant <model> …`, ingen feil etter den |
| PLS-en | `./scada plc-browse --endpoints`, så `./scada plc-browse GVL_KNX` | endpointene, så variablene i GVL-en |
| PLS-broen | hendelsesloggen og topplinjen | `PLC` grønn, `PLS (OPC UA) tilkoblet: N variabler` |
| Plantegningen | `./scada plan-dxf /tmp/plan.dxf` | `Skrev … (N etasjer, M rom)` |

Det finnes ingen enkeltkommando som sjekker hele `config.yaml`: `info` sjekker prosjektdelen, `gateway` sjekker
`protocols.yaml`, og serveren sjekker resten når den starter (KNX-grensesnitt, PLS-innstillinger, `plc_map.yaml`) – les
loggen dens.

## Vanlige feil

| Symptom | Årsak |
|---|---|
| En innstilling har ingen virkning | Feilstavet eller feil innrykket nøkkel – ukjente nøkler ignoreres |
| En alarmregel utløses aldri | `dpt_main: "1"` (tekst) eller en `condition` som ikke har en av de gyldige formene |
| SCADA-systemet når ikke OPC UA-serveren vår | `endpoint` utelatt eller `127.0.0.1` – bruk `opc.tcp://0.0.0.0:<port>` |
| En Modbus-måler svarer ikke | Feil `parity`/`baudrate`/`unit_id`, A/B byttet om, ingen terminering; brukeren er ikke i `dialout` |
| Flere PLS-er, én skal være av | Med `controllers` er en PLS på, med mindre `enabled: false` er satt på den eller på `plc:` |
| To PLS-er overskriver hverandres GVL-fil | Begge bruker standarden `gvl: GVL_KNX` – gi hver sin egen |
| Serveren stopper ved oppstart etter en endring i ETS | `plc_map.yaml` eller `extra_rooms` nevner en adresse som er borte – kjør `./scada plc-map`, rett `extra_rooms` |
