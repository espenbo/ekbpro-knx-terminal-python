# TODO – EKBPRO KNX Terminal Python

Gren: `feature/modulaer-protokoller`. Filen ligger i et offentlig repo: ingen IP-adresser, passord eller anleggsdetaljer
her. Det som gjelder ett anlegg, står i `sites/<anlegg>/TODO.md` og `sites/<anlegg>/CLAUDE.md` (ikke i git).

## Beslutninger

- Protokollene kan ha begge roller: klient/master (vi henter fra utstyr) og server/slave (andre henter fra oss).
- Alle protokoller er likestilte. KNX er én modul blant flere, og programmet skal kunne kjøres uten KNX
  (for eksempel som ren Modbus → OPC UA-gateway). Hvert prosjekt velger de protokollene det trenger.
- Hver protokoll er en modul med egne pakker. Pakkene installeres bare når modulen brukes:
  `./scada modules install modbus mqtt`.
- Innstillinger og gatewayer ligger i `protocols.yaml` (på anleggene `server.protocols: var/protocols.yaml`) og kan
  redigeres både i filen og i TUI-en. Tagger kan få nytt navn i en gateway (`hr:100 -> Ute.Temperatur`).
- **Oppstart:** ingen protokoll sender endringer ved oppstart eller gjenoppkobling. Statusene leses først.
  Gamle verdier fra databasen vises bare (dempet/kursiv) og sendes aldri videre. Startverdier går bare til egne
  servere, aldri til KNX. `read_on_start: status` er standard. Se docstringen i `knxscada/protocols/gateway.py`.
- Den gamle PLS-broen (`plc:` / `opcua_bridge.py`) kan erstattes av gatewayen, men først når det finnes en annen
  måte å flytte KNX-variabler til OPC UA-tagger på (et av anleggene bruker broen).

## Status

- 2026-11-01 kveld: 409 tester grønne (1 hoppet over), ruff 55. Ingenting fra Runde 2 er committet ennå.

## Neste (ønsker fra brukeren 2026-11-01 kveld)

- [x] Skjermsparer: flere og morsommere sider (2026-11-01, se «Ferdig»).
- [ ] Oversikt: flisen UTE viser utetemperaturen også når den kommer fra en protokolltagg (ikke bare en
      gruppeadresse). Flisen EFFEKT roterer gjennom alle energimålerne i `ui.systems` og viser navnet på måleren.
- [ ] Oversikt: romkort og «Lys på» skal også ta med rommene fra `project.point_rooms` (temperatur og lys).
      Kontroller at alle rom og lys i et bygg bak en gateway kommer med.
- [ ] Ny hovedmeny «Monitor» med undermenyer KNX, Modbus, OPC UA og BACnet (bare de protokollene prosjektet
      bruker). Under hver ligger monitoren for den protokollen (trafikk/verdier per tilkobling). Dagens
      busmonitor (F4) blir Monitor › KNX.
- [ ] BACnet-skriving: velge prioritet ved skriving. `priority` finnes allerede per tilkobling og per tagg
      (tom = enhetens standard). Mangler: valg i skrivedialogen (Anlegg › Innstillinger, romkort), med
      tilkoblingens prioritet som forvalg, og at prioriteten logges sammen med skrivingen.
- [ ] Skrivbare punkter på romkort fra `point_rooms` (f.eks. et varsellys som må kunne slås av).
- [ ] MQTT Sparkplug B (ny modul).
- [ ] Matter (ny modul).
- [ ] Modulært per prosjekt: gjøre xknx valgfri (`./scada modules install knx`), slik at et prosjekt med bare
      Modbus, eller bare OPC UA og Modbus, ikke trenger KNX-pakkene. TUI og server må tåle at modulen mangler.
- [ ] KNX → OPC UA via gatewayen (erstatning for `plc_map.yaml` + GVL): først da kan PLS-broen fases ut.

## Runde 2 – åpent

- [ ] BACnet mot ekte utstyr: skriving med prioritet, og at et ekte SD-anlegg leser vår BACnet-enhet
      (Who-Is, objektliste, COV, skriving tilbake). Lesing, COV og `bacnet-scan` er prøvd.
- [ ] Skriving av tabeller (ukeprogram fra CODESYS-kalendere) er ikke prøvd.
- [ ] Anlegg › Innstillinger: forbruk «i går» / per måned i Energimålere.
- [ ] Modbus-identifisering: baud vises «?» for noen WAGO-målere (kosmetisk).

## Enklere installasjon

- [ ] `./scada setup`: en veiviser i Python som erstatter `install_knx_terminal.sh`. Spør etter ETS-prosjektet
      (fil, scp/SFTP eller URL), tilkoblingstype og IP-grensesnitt, prøver passordet på nytt ved feil og spør hvilke
      protokollmoduler som skal installeres. Skal også fungere uten KNX.
- [ ] Veiviseren spør om Modbus-, BACnet- og OPC UA-utstyr og kan søke etter det.
- [ ] `./scada init --keyring FIL --keyring-password …` i stedet for at skriptet erstatter tekst i `config.yaml`.
- [ ] `./scada service install|remove|status`: systemd-tjenesten fra `deploy/knxscada.service`.
- [ ] `./scada plan-wizard`: plantegningsveiviseren som egen kommando. Pillow blir en valgfri modul.
- [ ] `install.sh` i repoet: én kommando på en ny maskin (apt-pakker, git clone, venv, `./scada setup`).

## Ferdig (kort – detaljer i git-loggen og i docs/)

- Runde 1 (committet 2026-10-31): modulregister, `protocols.yaml`, gateway-motor, hub, KNX, Modbus TCP/RTU,
  MQTT, OPC UA, server uten KNX, `./scada modules|gateway`, F11 Gatewayer, Docker/`setup.sh`.
- Oppstart: les først, send etterpå; gamle verdier vises dempet og sendes aldri (2026-10-31).
- Utstyrsmaler for Modbus (WAGO 879-30xx, Carlo Gavazzi, Solar-Log med effektstyring), `./scada simulate`.
- OPC UA i bolker etter `OperationLimits` (WAGO: 100 per kall), float32 rundet til 7 siffer, tabeller som lister.
- BACnet/IP klient og egen enhet, `./scada bacnet-scan`, `route_source()` (riktig kildeadresse over VPN).
- Logging og trender for protokolltagger (`log`, `point_values`, F7), alarmer på tagger (`match_point`, `delay_s`).
  `Hub.update` bruker `same_value(rel=1e-6)`, så store tellere logges ved små endringer.
- Anlegg (Ctrl+E): ett aggregat om gangen med tegning, grafer og Innstillinger (grupper, beskrivelser, skriving med
  bekreftelse, systemvender). `type: panel` for et bygg bak en gateway. Energimålere (Ctrl+G) med forbruk.
- `project.point_rooms`: rom med protokolltagger under «Etasjer og rom» (flere bygg).
- Skjermsparer (F9): nye sider – himmel med sol/måne og bygg med lys i vinduene, varmekart, telegramregn,
  ventilasjon med luftstrøm, energimålere på rad, sprettende klokke, dagens rekorder.
- Dokumentasjon på engelsk og norsk (`docs/en`, `docs/nb`, 12 dokumenter, `12-setup-reference.md` er fasit).

## Anlegg

Åpne punkter per anlegg står i `sites/<anlegg>/TODO.md` (privat). Les dem når du jobber med et anlegg.
