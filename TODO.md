# TODO – EKBPRO KNX Terminal Python

Gren: `main` (feature/modulaer-protokoller ble slått inn 2026-11-02). Filen ligger i et offentlig repo: ingen IP-adresser, passord eller anleggsdetaljer
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
- Den gamle PLS-broen (`plc:` / `opcua_bridge.py`) erstattes av gatewayen (`./scada plc-gateway`, 2026-11-02).
  Broen blir værende til anlegget som bruker den, er flyttet over og har gått en stund uten feil.

## Status

- 2026-11-02: 445 tester grønne (1 hoppet over), ruff 0, oversettelser 0 mangler. Runde 2 er slått inn i `main`;
  KNX valgfri og KNX → OPC UA via gatewayen er ikke committet ennå.
- [x] Kommentarer (brukerens krav 2026-11-01, ferdig ~23:45): docstring på alle moduler, klasser og funksjoner i
      `knxscada/` (1073 definisjoner, 0 uten) og alle tester (352 nye), pluss `#`/`#:`-kommentarer om sikkerhet,
      utstyrssærheter og ytelse. Kontrollert: AST uten docstrings lik før og etter (ingen kodeendring), ruff 0,
      413 grønne. Fire tomme `pass` fjernet der docstringen nå er kroppen (PIE790).

## Neste (ønsker fra brukeren 2026-11-01 kveld)

- [x] Skjermsparer: flere og morsommere sider (2026-11-01, se «Ferdig»).
- [x] Oversikt (2026-11-01): uten KNX-adresse for ute/effekt registrerer appen `outdoor`-taggen til første aggregat
      og effekten til hver måler i `ui.systems` som `pt:`-punkter (`ScadaApp._add_system_points`, `HomeModel.meters`).
      EFFEKT roterer hvert 5. s (`OverviewView.METER_ROTATE_S`) med målernavnet i tittelen («EFFEKT · Felles», «6/6»).
      Skjermspareren bruker det samme. Soltider: standard Fredrikstad (`ui.latitude`/`longitude`). Test, dok 03/04/12.
- [x] Oversikt: rom fra `project.point_rooms` var allerede med. Lys teller bare kort med `usage: light` og et
      skrivbart på/av-punkt – det som manglet, var anleggsoppsett (se `sites/<anlegg>/TODO.md`).
- [x] Hovedmenyen «Monitor» (2026-11-01): undermenyer KNX + én per protokoll i bruk med antall tilkoblinger
      (fra hubens status). F4 åpner den første og går videre til neste ved nytt trykk. `ProtocolMonitorView`
      (`tui/protomon.py`): tilkoblingene øverst, under hver verdiendring (←) og skriving (→ med bruker), filter,
      pause, Enter/`w` = skriv med bekreftelse (bare `writable`). Buffer `app.point_log` (10 000). Test
      `tests/test_protomon.py`, dok 03 og 08 (en+nb). Prøvd uten skjerm mot et anlegg: OPC UA, Modbus, BACnet.
  - [ ] Mulig senere: vise lesinger/pollinger og feil per tilkobling (krever at huben sender mer enn endringer),
        og skjule tagger som endrer seg hvert sekund (PLS-klokke `Act_Time`) med et eget filter.
- [x] BACnet-skriving mot ekte utstyr (2026-11-01): brukeren slo av to lys via en KNX→BACnet-gateway med
      prioritet 16 – status bekreftet.
- [x] BACnet-skriving med valgt prioritet og «Slipp» (2026-11-01): `Driver.priorities` (BACnet), `Hub.write(...,
      priority, release)`, `point_write` tar `priority`/`release: true`; 1, 2 og 5 avvises (`RESERVED_PRIORITIES`).
      `points` gir taggens standardprioritet. `PointDialog` viser prioritet 3–16 med standarden forvalgt, advarer
      under standarden og har «Slipp» (Null i plassen). Felles bekreftelse `confirm_point_write` i F11, Anlegg og
      Monitor (F11 skrev før uten bekreftelse). Hendelsesloggen: «verdi (prioritet 8)» / «sluppet (prioritet 8)».
      Tester i test_protomon og test_bacnet, dok 03 og 11 (en+nb).
  - [ ] Romkortene (`point_rooms`) skriver fortsatt med standardprioriteten, uten valg.
  - [ ] Mulig senere: vise enhetens prioritetstabell (`priority-array`) i dialogen – hvem som styrer nå.
- [x] Skrivbare punkter på romkort fra `point_rooms`: virket allerede (skrivbar bool → bryter); kortet må ha
      `usage: light` for å telle som lys og komme i «Lys på».
- [ ] MQTT Sparkplug B (ny modul).
- [ ] Matter (ny modul).
- [x] KNX som valgfri modul (2026-11-02): `knx` er en modul (`xknx`, `xknxproject`); grunnpakken er textual,
      textual-plotext og PyYAML (asyncua flyttet til `opcua`). `dpt.py`/`server.py`/`project.py` tåler at KNX mangler;
      et KNX-anlegg uten modulen stopper med `./scada modules install knx` (exit 2). `setup.sh` og Docker har `knx`
      som standard. Test `tests/test_optional_knx.py` (pakkene skjult i en egen prosess). Dok 01/09/11/12 (en+nb), README.
- [x] KNX → OPC UA via gatewayen (2026-11-02): `./scada plc-gateway [--write]` lager en OPC UA-tilkobling og
      gatewayene `knx-<pls>`/`<pls>-knx` fra `plc_map.yaml` (`plc.gateway_config`, prefikset fra `plc.node_prefix`
      eller ved bla). Gatewayen har fått PLS-broens vakter mot KNX: `settle_ms` (500), `burst_limit` (8),
      `restart_quiet_s` (15) – en bølge av endringer (nedlasting/omstart) sendes ikke, stillhet, deretter får
      PLS-en KNX-verdiene igjen; dårlig status gjør taggen ukjent. Tester `test_gateway_guard.py`, `test_plc.py`,
      dok 10/11/12 (en+nb), README. Prøvd lesende mot et anlegg: samme 435 variabler som broen.
  - [x] Byttet over på anlegget som brukte broen (2026-11-02): gatewayen kjører; følges med noen dager.
  - [ ] Deretter: fase ut `opcua_bridge.py` når ingen anlegg bruker `plc:`.

## Runde 2 – åpent

- [x] Effektstyring i Anlegg (2026-11-02): `type: power` (`tui/power.py`, `render_power`) – forbruk som stolpe mot
      grensen (gul fra 80 %, rød over), ledig/over, laster koblet ut, månedens høyeste, én linje per last (koblet
      ut/inne, drift, ganger utkoblet, min./maks. tider). Tagger kan ligge i andre tilkoblinger
      (`{connection: knx, tag: …}`); `history` med `point: knx/<ga>` leser telegrammene. `extra_rooms` med navnet til
      et ETS-rom legger kortene til det rommet. Tester `test_power.py`, `test_project.py`, `test_store.py`,
      dok 03/04/12 (en+nb). «Ledig» = grense − forbruk; grafkurvene trekkes til nå (verdier logges ved endring).
- [x] Effektstyring for elektrisk varme (2026-11-02): `loads` som liste med `tags` per last; rollene `temp`
      (romtemperatur) og `heat` (termostatens varmebehov) gir kolonnene Rom og Varmebehov; utkoblet last med
      varmebehov = «♨ ønskes» (varselfarge). `relay` (aktuatorens status) gir kolonnen Relé; utkoblet last med relé
      på = «⚠ på» (feil). Kolonnen Drift (`status`) vises bare når en last har `status`. `relay_inverted` (tilbakemelding på =
      last ute). Anlegg/Energimålere tar `knx/…`-verdier fra telegrammene (huben sender ikke KNX i `points`);
      grafene tegnes på nytt når en nåverdi endres. Tester i `test_power.py`, dok 04/12 (en+nb).
- [x] Flere temperaturfølere per rom (2026-11-02): `Room.temps` (`home.extra_temps`, `short_label`) – alle
      DPT 9.001-verdier i rommets kort unntatt duggpunkt/settpunkt; Klima-tabellen får en linje `└ navn` per føler med
      minigraf, romkortene viser verdiene ved lysene. Test `test_home.py`, dok 03 (en+nb).
- [x] `project.units` (2026-11-02): enhet per gruppeadresse (volt sendt som DPT 9.020 mV) – `dpt.fmt(…, unit)`,
      KNX-taggene, trender og hendelsesloggen. Dok 12 (en+nb), test i `test_project.py`.
- [x] BACnet over VPN (2026-11-02): `bacnet-scan ADRESSE` leser jokerinstansen 4194303 når Who-Is ikke får svar
      (Climatix/Belimo svarer med kringkastet I-Am). `ui.systems`: `status_texts` (multi-state-status som tekst) og
      `alarm_normal` (alarmklasse 1 = Normal / 2 = Alarm). Tester, dok 04/11/12 (en+nb).
- [x] Rørskjema i Anlegg (2026-11-02): `type: diagram` – tegningen skrives som tekst i `ui.systems` med
      `{felt}`-plassholdere (bredden følger plassholderen), ━ = tur (rød), ═ = retur (blå), felttyper value/pump/
      state/text, alarmtagger per felt, felt fra andre tilkoblinger, `charts` for grafene. `tui/diagram.py`,
      `tests/test_diagram.py`, dok 03/04/08/12 (en+nb). Første bruk: varmeanleggene i et anlegg, tegnet etter HMI-en.

- [ ] BACnet mot ekte utstyr: at et ekte SD-anlegg leser vår BACnet-enhet
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
