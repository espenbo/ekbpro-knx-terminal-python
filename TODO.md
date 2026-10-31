# TODO – modulære protokoller og gatewayer

Gren: `feature/modulaer-protokoller`

## Beslutninger

- Protokollene kan ha begge roller: klient/master (vi henter fra utstyr) og server/slave (andre henter fra oss).
- Alle protokoller er likestilte. KNX er én modul blant flere, og programmet skal kunne kjøres uten KNX
  (for eksempel som ren Modbus → OPC UA-gateway).
- Hver protokoll er en modul med egne pakker. Pakkene installeres bare når modulen brukes:
  `./scada modules install modbus mqtt`.
- Innstillinger og gatewayer ligger i `protocols.yaml` og kan redigeres både i filen og i TUI-en.
  Tagger kan få nytt navn når de legges inn i en gateway (`hr:100 -> Ute.Temperatur`).

## Runde 1 (ferdig – committet 2026-10-31)

- [x] Modulregister med innstillinger og tagfelt per protokoll (`knxscada/protocols/__init__.py`)
- [x] Felles tag-modell og driverbase (`knxscada/protocols/base.py`)
- [x] `protocols.yaml`: lesing, validering, lagring
- [x] Gateway-motor: nytt navn, skalering, retning (én vei / begge), ekko-sperre, dødbånd, ratebegrensning,
      ingen telegrammer til KNX ved oppstart
- [x] Hub i serveren: drivere, verdier, status, kommandoer til TUI-en
- [x] KNX-modul
- [x] Modbus TCP og RTU (RS485), master og slave
- [x] MQTT
- [x] OPC UA klient og server
- [x] Serveren kan starte uten KNX
- [x] Kommandoer: `./scada modules`, `./scada modules install …`, `./scada gateway`
- [x] TUI: ny visning «Gatewayer» (F11) med tilkoblinger, tagger, gatewayer og skjemaer for innstillinger
- [x] Norske oversettelser
- [x] Tester (`tests/test_protocols.py`, 79 tester)
- [x] Dokumentasjon: `docs/11-protocols-gateways.md`, README, `protocols.example.yaml` (WAGO-måler: paritet E)
- [x] Dockerfile (`--build-arg MODULES="modbus mqtt"`) og `setup.sh` (`./setup.sh modbus mqtt`, eller spør): velg moduler
- [x] `./scada modules` virker uten `config.yaml` (ny installasjon)

## Status 2026-10-31

- 198 norske oversettelser lagt inn; `test_i18n` grønn.
- TUI-en starter på F11 når serveren kjører uten KNX.
- Rettet: F11 krasjet når status kom før tabellen hadde kolonner; `Select.BLANK` → `Select.NULL` (Textual 8).
- Hele testpakken grønn: 332 tester (2026-10-31).
- Alle tre site-konfigurasjonene lest med den nye koden: KNX på, ingen `protocols.yaml` → gatewaydelen står stille.
- OBS: de tre serverne (glommensgate1 via `knxscada.service`, lerkeveien2, tomteveien30) kjører koden fra
  denne arbeidskatalogen. Neste omstart laster den nye koden – kjør hele testpakken før omstart.

## Glommensgate 1 → `sites/glommensgate1`

- [x] Filene kopiert til `sites/glommensgate1` (config, knxproj, plan, plc_map, GVL), tjenestefil og `migrate.sh`
- [x] Kontrollert 2026-10-31: kopiene i `sites/glommensgate1` er identiske med filene i rotmappen
- [x] Kjørt `sudo bash sites/glommensgate1/migrate.sh` (2026-10-31 17:10). `knxscada-glommensgate1` kjører:
      KNX og PLS (435 variabler) tilkoblet, `knx-glommensgate1` virker.
- [ ] TUI-en på tty1 (startet manuelt 26.10 som `knxscada tui` uten `-c`) bruker fortsatt den gamle sokkelen
      `var/knxscada.sock`, som er borte. Avslutt den på tty1 og start `knx-glommensgate1`.

## WAGO Edge (github.com/espenbo/wagoEdgeStart)

- [x] Lest repoet. `setupEdgeComputer/install_knx_terminal.sh` og `knx_plan_wizard.py` er de samme som i
      `tmp/eksempelPython` (se «Enklere installasjon» under). Brukbart for Modbus:
  - `modbus_slaves/wago meter/wago_modbus_registers.csv`: WAGO energimåler 879-30xx, RS485 9600 8E1, adresse 1,
    holding registers (FC3), Float ABCD (= `float32`, big). Måleverdier 0x5000–0x5036, energi 0x6000–0x608F.
  - `modbus_slaves/SolarLog/…/README.md`: SolarLog Modbus TCP PM 2.17 – live data 3500–3529 (uint32),
    effektstyring 10200–10231.
  - To slavesimulatorer (pymodbus, port 5020) – nyttige som testutstyr.
- [x] Enhetsmaler (2026-10-31): `knxscada/protocols/devices/*.yaml`, `model: WAGO 879-3000` (også 879-3020,
      879-3040; produktnummeret alene går også). 35 måleverdier med engelske navn som i håndboken (V1.8, A3.2),
      får nytt navn etter merkesystemet i gatewayen. Paritet Even (håndboken sier None, WAGO-biblioteket Even).
      Identifisering etter tilkobling: meter code 0x4002, adresse, baud, paritet 0x4011, serienr. – vises i
      hendelsesloggen og F11, med feil når modell eller innstillinger ikke stemmer. Valg i F11-skjemaet (Utstyr).
- [x] Carlo Gavazzi-maler (2026-10-31, fra protokolldokumentene i `tmp/CarloGavazzie`): EM111, EM112, ET112
      (`carlo-gavazzi-em100.yaml`), EM270, EM280 (`…-em270.yaml`, to sensorsett TCDA1/TCDA2), EM580 (elbillading),
      WM15. 8N1 (fabrikk), heltall med vekt og laveste ord først, skalert i malen. Nye Modbus-innstillinger
      `max_registers` (EM100: 50, EM270: 18) og `max_gap` (EM580: 0), `invalid` (EEE-koder → dårlig kvalitet),
      `only:` for tagger som bare finnes på én modell (timeteller ET112).
- [ ] EM271 mangler protokolldokument (ikke med i EM270/EM280-dokumentet)
- [ ] Flere maler: SolarLog (venter på dokumentasjon/modellnavn)
- [ ] `./scada modules simulate wago-879-30xx` (eller tilsvarende): Modbus-slave fra en mal, til test uten utstyr

## Oppstart: les først, send etterpå (ferdig 2026-10-31)

Ved oppstart og ved gjenoppkobling skal **ingen** protokoll sende endringer. Statusene skal leses av først.

- [x] `Driver.ready`: en tilkobling er klar først når første lesing er ferdig (Modbus: første polling,
      OPC UA-klient: alle noder lest, MQTT: 1 s etter abonnement, KNX: etter `read_on_start` + 2 s).
      Verdier før det er statuser, ikke endringer. F11 viser `leser …`.
- [x] Statuser lagres som «siste verdi» på begge sider av lenken. Målets egne verdier følges også for
      enveislenker, slik at en endring ikke sendes når målet allerede har verdien.
- [x] En endring til et mål som fortsatt leser, venter til det er klart (og droppes hvis målet har verdien)
- [x] Startverdier går bare til egne servere (OPC UA-server, Modbus-slave, MQTT-tilstander laget av en
      gateway) – aldri til utstyr. `initial: true` sender også til utstyr (aldri KNX), `initial: false` til ingenting.
      Egne servere fylles på nytt når de kommer tilbake (for eksempel en MQTT-broker som har startet på nytt).
- [x] Rettet: svarene på `read_on_start` ble sendt videre som endringer fra KNX
- [x] TUI: «Send verdier ved oppstart» er nå et valg (bare til egne servere / ja / nei)
- [x] Tester (4 nye), `docs/11-protocols-gateways.md`, docstringen i `gateway.py`
- [x] Gamle verdier sendes aldri videre (besluttet 2026-10-31): KNX-verdiene i databasen fra forrige kjøring
      vises bare i TUI-en, dempet og i kursiv («Gammel verdi – ikke lest ennå»), til bussen har sendt en ny.
      OPC UA, MQTT og Modbus får en KNX-verdi først når den er lest eller mottatt etter oppstart – til da er
      taggen tom. Ferske statuser som leses ved oppstart, fyller egne servere.
- [x] `read_on_start: status` er standard (KNX-statusene leses alltid ved oppstart, aldri styreadresser)
- [x] Rettet: første skriving fra en ekstern klient til egen server (Modbus-slave, OPC UA-server) ble tolket som
      status og ikke sendt videre. Etter at en tilkobling er klar, er alle verdier endringer (unntak: Modbus-master,
      der en tagg som ikke kunne leses ved første polling, gir status når den leses).
- [x] De tre site-konfigurasjonene har fått `read_on_start: status` (2026-10-31; `config.yaml` i rotmappen er
      flyttet til `sites/glommensgate1`). Gjelder fra neste omstart av hver tjeneste.
- [x] Oversiktsskjermen viser gamle verdier dempet og i kursiv: romkortene (temperatur, settpunkt, varme, lys),
      klimatabellen og flisene ute / inne snitt / effekt. Forklaring i rammen «Romstatus».

## Dokumentasjon

- [x] Flyttet til `docs/en/`, oversatt til `docs/nb/` (alle 12 dokumenter, 2026-10-31). README lenker til begge.
- [x] `docs/*/12-setup-reference.md`: oppsett for folk og AI – fullstendige eksempler på `config.yaml` og
      `protocols.yaml`, alle nøkler med type, standardverdi og gyldige verdier, oppgaver steg for steg, sjekker.
- [x] Avvik mellom koden og dokumentasjonen rettet (04, 06, 08, 10, 11; MQTT `set_topic`-hjelpeteksten var feil).
- [ ] Når en engelsk side endres, må den norske endres likt.
- [x] `server.protocols: var/protocols.yaml` (besluttet 2026-10-31): satt på de tre anleggene og i
      `config.example.yaml`, standard i dokumentasjonen – systemd-tjenesten får bare skrive i `var/`.

## Neste steg (venter på avklaring)

- SolarLog-mal: hvilken modell, og registerkart

## Runde 2

- [ ] BACnet/IP (klient og enhet)
- [ ] MQTT Sparkplug B
- [ ] Matter
- [ ] Trender og logging for tagger fra andre protokoller enn KNX
- [ ] Vurdere å la gatewayen erstatte den gamle PLS-broen (`plc:` / `opcua_bridge.py`)
- [ ] Gjøre xknx valgfri, slik at en installasjon uten KNX blir enda mindre

## Enklere installasjon (fra `tmp/eksempelPython`)

`install_knx_terminal.sh` og `knx_plan_wizard.py` kan tas inn i programmet:

- [ ] `./scada setup`: en veiviser i Python som erstatter skallskriptet. Den spør etter ETS-prosjektet
      (fil på maskinen, opplasting med scp/SFTP eller URL), tilkoblingstype og IP-grensesnitt, prøver
      passordet på nytt ved feil, og spør hvilke protokollmoduler som skal installeres. Den skal også fungere
      uten KNX.
- [ ] `./scada init --keyring FIL --keyring-password …` i stedet for at skriptet erstatter tekst i `config.yaml`
- [ ] `./scada service install|remove|status`: lager systemd-tjenesten fra `deploy/knxscada.service`
      (bruker, gruppe og katalog fylles inn), starter den og viser loggen
- [ ] `./scada plan-wizard`: plantegningsveiviseren som egen kommando. Pillow blir en valgfri modul
      (`./scada modules install plan-image`).
- [ ] `install.sh` i repoet: én kommando på en ny maskin (apt-pakker, git clone, venv, `./scada setup`)
