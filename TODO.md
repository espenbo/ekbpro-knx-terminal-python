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
- [ ] Kjøre `sudo bash sites/glommensgate1/migrate.sh` (krever sudo; laster den nye koden ved omstart)
- [ ] Etterpå: sjekke at `knx-glommensgate1` virker, og at TUI-en på tty1 startes med `-c sites/glommensgate1/config.yaml`

## WAGO Edge (github.com/espenbo/wagoEdgeStart)

- [x] Lest repoet. `setupEdgeComputer/install_knx_terminal.sh` og `knx_plan_wizard.py` er de samme som i
      `tmp/eksempelPython` (se «Enklere installasjon» under). Brukbart for Modbus:
  - `modbus_slaves/wago meter/wago_modbus_registers.csv`: WAGO energimåler 879-30xx, RS485 9600 8E1, adresse 1,
    holding registers (FC3), Float ABCD (= `float32`, big). Måleverdier 0x5000–0x5036, energi 0x6000–0x608F.
  - `modbus_slaves/SolarLog/…/README.md`: SolarLog Modbus TCP PM 2.17 – live data 3500–3529 (uint32),
    effektstyring 10200–10231.
  - To slavesimulatorer (pymodbus, port 5020) – nyttige som testutstyr.
- [ ] Enhetsmaler: `knxscada/protocols/devices/*.yaml` med ferdige tagger (wago-879-30xx, solarlog), slik at en
      tilkobling kan skrive `model: wago-879-30xx` (ikke `device:` – det er serieporten) i stedet for å liste
      alle registrene. Valg av mal i F11-skjemaet.
- [ ] `./scada modules simulate wago-879-30xx` (eller tilsvarende): Modbus-slave fra en mal, til test uten utstyr

## Oppstart: les først, send etterpå (ny regel 2026-10-31)

Ved oppstart og ved gjenoppkobling skal **ingen** protokoll sende endringer. Statusene skal leses av først.

I dag gjelder sperren bare for KNX (`initial_writes = False` i `knx.py`). De andre driverne har
`initial_writes = True` (`base.py`), og gatewayen sender startverdiene videre til Modbus, MQTT og OPC UA
(`gateway.py`, `initial: true` som standard).

- [ ] Endre standarden: `initial_writes = False` for alle drivere, og `initial: false` som standard i gatewayen
- [ ] Hver tilkobling leser alle taggene sine (Modbus-polling, OPC UA-lesing, MQTT retained, KNX fra databasen
      eller GroupValueRead) før gatewayen slipper gjennom noe. Verdiene som leses, lagres som «siste verdi» på
      begge sider av lenken, slik at de ikke sendes senere som en endring.
- [ ] En tilkobling er «klar» først når første lesing er ferdig. Status i F11: `leser` → `klar`.
- [ ] Bare endringer som kommer *etter* at begge sider er klare, sendes videre
- [ ] `initial: true` skal fortsatt kunne slås på per gateway/linje (for eksempel en tom MQTT-broker som skal
      fylles) – men aldri mot KNX (besluttet 2026-10-31)
- [ ] Tester: oppstart og gjenoppkobling sender ingenting; første endring etter oppstart sendes
- [ ] Oppdatere dokumentasjonen (`docs/11-protocols-gateways.md`, docstringen i `gateway.py`)

## Dokumentasjon

- [ ] Skrives slik at en AI enkelt kan brukes til å sette opp programmet: fullstendige eksempler på
      `config.yaml` og `protocols.yaml`, alle nøkler med type, standardverdi og gyldige verdier, oppgaver steg
      for steg, og hvilke kommandoer som kontrollerer at oppsettet virker
- [ ] All dokumentasjon på både engelsk og norsk bokmål, i hver sine filer
      i `docs/en/` og `docs/nb/` (besluttet 2026-10-31). README får lenke til begge.
- [ ] I dag er `docs/01`–`11` og README bare på engelsk – alle må oversettes

## Neste steg (venter på avklaring)

- Enhetsmaler (WAGO-måler, SolarLog) – format og navn
- Migrere Glommensgate 1 (sudo)

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
