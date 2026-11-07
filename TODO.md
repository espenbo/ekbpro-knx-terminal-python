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

- 2026-11-07: 496 tester grønne (1 hoppet over), ruff 0, oversettelser 0 mangler. Arbeidsplanen under er gjort
  (punkt 1–9); ikke committet.
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
  - [x] Lesinger/feil per tilkobling og `f` = skjul raske tagger (2026-11-07, se arbeidsplanen).
- [x] BACnet-skriving mot ekte utstyr (2026-11-01): brukeren slo av to lys via en KNX→BACnet-gateway med
      prioritet 16 – status bekreftet.
- [x] BACnet-skriving med valgt prioritet og «Slipp» (2026-11-01): `Driver.priorities` (BACnet), `Hub.write(...,
      priority, release)`, `point_write` tar `priority`/`release: true`; 1, 2 og 5 avvises (`RESERVED_PRIORITIES`).
      `points` gir taggens standardprioritet. `PointDialog` viser prioritet 3–16 med standarden forvalgt, advarer
      under standarden og har «Slipp» (Null i plassen). Felles bekreftelse `confirm_point_write` i F11, Anlegg og
      Monitor (F11 skrev før uten bekreftelse). Hendelsesloggen: «verdi (prioritet 8)» / «sluppet (prioritet 8)».
      Tester i test_protomon og test_bacnet, dok 03 og 11 (en+nb).
  - [x] Romkortene (`point_rooms`): `p` på raden gir valg av prioritet og «Slipp» (2026-11-07).
  - [x] Enhetens prioritetstabell (`priority-array`) vises i dialogen (2026-11-07); ikke prøvd mot ekte utstyr.
- [x] Skrivbare punkter på romkort fra `point_rooms`: virket allerede (skrivbar bool → bryter); kortet må ha
      `usage: light` for å telle som lys og komme i «Lys på».
- [x] MQTT Sparkplug B (ny modul, 2026-11-07) – se arbeidsplanen.
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

## Arbeidsplan 2026-11-07 (brukeren: «gjør ferdig alle punkter som kan gjøres»)

Rekkefølge – kryss av underveis (økten kan bli brutt). Punkter som trenger utstyr, beslutninger fra brukeren eller
skriving på et anlegg, tas ikke (se «Venter på brukeren» nederst).

1. [x] `./scada init --keyring FIL --keyring-password … --user-id N --usb-device …` (`render_config`, `tests/test_init.py`,
   dok 02/12 en+nb)
2. [x] `./scada service install|remove|status` (`service.py`, `tests/test_service.py`, dok 05/12 en+nb). Uten root:
   enheten i `var/systemd/` og sudo-kommandoene skrives ut. Gir samme enhet som den som er installert på anleggene.
3. [x] Energimålere: kolonnene «I går» og «Denne måneden», og i går/denne/forrige måned i kortet (`calendar_use`,
   lokal tid med sommertid, test i `test_systems.py`, dok 03 en+nb)
4. [x] Romkort (`point_rooms`): `p` på raden til en BACnet-tagg gir skrivedialogen med prioritet og «Slipp»
   (`DatapointRow.action_priority_write`, prioriteten fra `points` i `point_gas`); kontrollene skriver fortsatt med
   standardprioriteten. Test i `test_protomon.py`, dok 03 en+nb.
5. [x] BACnet: `priority-array` og `relinquish-default` i skrivedialogen (`Driver.priority_array`, hub-kommandoen
   `point_priorities`, `priority_text`). Tester i `test_bacnet.py`, dok 03 en+nb. Ikke prøvd mot ekte utstyr.
6. [x] Monitor: tellere per tilkobling (Lest, /min, Endret, Feil, Brudd – `Hub.stats`, status hvert 10. s) og `f`
   skjuler tagger som endrer seg > 20 ganger/min. Tester i `test_protomon.py`, dok 03 en+nb.
7. [x] Modbus-identifisering: «?» kom av enkeltforespørsler uten svar gjennom RS485-gatewayen (baud, paritet eller
   adresse vekslet mellom oppstarter). Hvert identify-register prøves nå 3 ganger (`IDENTIFY_TRIES`). Test i
   `test_devices.py`.
8. [x] `./scada setup` (`setup_wizard.py`: språk, ETS-prosjekt fra fil/scp/URL med passord på nytt, KNX IP-søk/USB/
   adresse/routing, keyring, moduler, søk etter BACnet/Modbus/OPC UA, config.yaml, tjeneste) og `install.sh` (apt,
   git clone/pull, setup.sh, setup). Tester `test_setup_wizard.py`, dok 01/12 en+nb. `install.sh` ikke prøvd på en
   ny maskin.
9. [x] MQTT Sparkplug B (`protocols/sparkplug.py`): server = vår edge node (NBIRTH/NDATA/NDEATH, NCMD tilbake til
   kilden, rebirth), klient = host-applikasjon (metrikker fra fødsel blir tagger, NCMD/DCMD ved skriving, død =
   «bad»). Egen protobuf-koder (ingen ny pakke). Tester `test_sparkplug.py` (amqtt-broker lokalt), dok 11/12 en+nb,
   README. Ikke prøvd mot Ignition MQTT Engine eller annen ekte host.

Venter på brukeren / utstyr: Matter (krever Matter-SDK, sertifisering og utstyr – ikke realistisk nå), BACnet mot et
ekte SD-anlegg, skriving av tabeller mot en ekte PLS, utfasing av `opcua_bridge.py` (ett anlegg bruker fortsatt
`plc:`), skrivetest gjennom KNX-USB, og alle anleggspunktene i `sites/*/TODO.md` (beslutninger, fysisk tilgang
eller skriving til utstyr). `plan-wizard` og `install_knx_terminal.sh` finnes ikke i dette repoet – punktet
om plantegningsveiviseren venter til brukeren sier hvor den ligger.

## Runde 2 – åpent

- [x] KNX USB (2026-11-07): `connection: usb` (`knx_usb.py`, `UsbInterface` erstatter `xknx.knxip_interface`, cEMI over
      `/dev/hidraw`), `./scada knx-usb [--listen S]`, `deploy/99-knx-usb.rules`, tester `test_knx_usb.py` (falsk enhet),
      dok 04/09/12 (en+nb). Ved oppstart: cEMI, så `PID_COMM_MODE` = data link layer (M_PropWrite, lokalt) – en
      Weinzierl 312 starter i «no layer» (0xFF) og sender ingenting opp før det. Lyttetest mot en ekte Weinzierl 312
      på et anlegg: samme 53 av 53 telegrammer som IP-grensesnittet på 40 s.
  - [ ] Skrivetest gjennom pinnen (etter avtale med brukeren).
  - Ikke støttet: EMI1/EMI2-grensesnitt og Powerline (PL110) – bruk knxd som KNXnet/IP-server der.

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
- [x] Anlegg › Innstillinger: forbruk «i går» / per måned i Energimålere (2026-11-07, se arbeidsplanen).
- [x] Modbus-identifisering: baud «?» – ny prøving av registre uten svar (2026-11-07).

## Enklere installasjon

- [x] `./scada setup` (2026-11-07, se arbeidsplanen): veiviser med prosjekt, tilkobling, passord, moduler, uten KNX.
- [x] Veiviseren søker etter BACnet (Who-Is), Modbus TCP (502) og OPC UA (4840) og lister det som svarer.
- [x] `./scada init --keyring FIL --keyring-password …` (2026-11-07, se arbeidsplanen).
- [x] `./scada service install|remove|status` (2026-11-07, se arbeidsplanen).
- [ ] `./scada plan-wizard`: plantegningsveiviseren som egen kommando. Pillow blir en valgfri modul.
- [x] `install.sh` i repoet (2026-11-07): apt-pakker, git clone/pull, `setup.sh`, `./scada setup`. Ikke prøvd på en ny maskin.

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
