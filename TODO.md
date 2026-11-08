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
- Den gamle PLS-broen (`plc:` / `PlcBridge` i `opcua_bridge.py`) er erstattet av gatewayen (`./scada plc-gateway`).
  Begge anleggene som brukte den, er flyttet over (2026-11-02 og 2026-11-07). Broen blir værende som reserve til
  begge har gått noen dager uten feil, og fases så ut (se «KNX → OPC UA via gatewayen» under).

## Status

- 2026-11-08: tredje gjennomgang (av 818561c) – seks feil rettet, se «Stabilisering 2026-11-08 (2)». CI på GitHub
  var grønn for 818561c (alle fire jobbene).
- 2026-11-08: `.claude/settings.json` er fjernet fra hele git-historikken (force-push, 818561c); den står nå i
  `.gitignore`.
- 2026-11-07 (kveld): stabiliseringen under (punkt 1–8) er gjort og pushet (fa16913, nå 8f336eb etter
  omskrivingen); CI grønn.
- 2026-11-07: arbeidsplanen (punkt 1–9) er gjort og pushet (8b0bd89).
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
  - [x] Det andre anlegget byttet over 2026-11-07 (346 variabler). Ingen anlegg bruker `plc:` lenger.
  - [ ] Deretter: fase ut `PlcBridge`/`plc:` i `opcua_bridge.py` når begge har gått noen dager uten feil (broen er
        reserven til da). `make_client`, `operation_limits` m.fl. brukes av OPC UA-driveren og blir værende.

## Stabilisering 2026-11-07 (ekstern gjennomgang av 8b0bd89)

Rekkefølge etter gjennomgangen – kryss av underveis. Hver feil får en regresjonstest.

1. [x] Ventende sending til KNX etter at kilden mistet forbindelsen: `disconnected` fjerner nå også vaktens `_batch`
       og `pending`, og `_write` sjekker kildens forbindelse og kvalitet rett før skrivingen (`_source_ok`). Tester i
       `test_gateway_guard.py` (feiler med den gamle koden).
2. [x] Kvalitet gjennom gatewayen: `Driver.set_quality`, kalt av motoren ved dårlig kilde («bad») og tapt kilde
       («old»); OPC UA-serveren gir BadNoCommunication / UncertainLastUsableValue, og BadWaitingForInitialData før
       første verdi. Neste gode verdi (også lik) gir Good. Test i `test_protocols.py`, dok 11 en+nb. Modbus-, BACnet-,
       MQTT- og Sparkplug-server viser ikke kvalitet ennå (BACnet: statusFlags/reliability er en mulig neste).
3. [x] Avhengigheter: `UsbInterface.stop_reconnect()` (xknx 4); hele pakken grønn med xknx 3.10.0 og 4.1.1.
       Øvre grenser i `requirements*.txt` og modulregisteret, og `constraints.txt` med de testede versjonene (brukes av
       `setup.sh`, `modules install` og Docker). Dok 01 en+nb.
4. [x] Sparkplug: identitet `(node, device, metric)` gjennom hele klienten (automatiske tagger `<node>/…` ved `+`,
       taggfeltet `node`), død bare for riktig node/enhet, skriving til riktig node. Test med to noder, dok 11/12.
5. [x] Tellere: gatewayen sammenligner nå normaliserte verdier (`Driver.normalise`; KNX koder/dekoder med DPT) med
       rel 1e-6 (`SAME_REL`). 147912 → 147913 går gjennom; et grovt DPT 9-ekko gjenkjennes fortsatt. Test i
       `test_gateway_guard.py`. Den gamle broen (`opcua_bridge.py`) bruker fortsatt 1e-4 – fases ut.
6. [x] Store 64-bits heltall: `base.as_int` (heltall forblir heltall) i Modbus `to_registers` og OPC UA-skriving.
       Test i `test_protocols.py`.
7. [x] `NO_COLOR`: plantegningens «ingen plantegning»-linje hadde tekstbiter uten stil, som Textuals
       monokromfilter ikke tåler – `apply_style(base)` som i LineLog. Hele pakken grønn med `NO_COLOR=1`.
8. [x] CI: `.github/workflows/tests.yml` – ruff og tester (oversettelser inkludert) på Python 3.12/3.13 med
       `constraints.txt`, en jobb med `NO_COLOR` og en med xknx 4. Grønn på GitHub.
9. [ ] Senere (beslutninger): tilgangsstyring (lese/operatør/konfigurasjon på socketen), OPC UA-server-standard
       (binding, anonym, kryptering), versjonerte releaser, Sparkplug mot Ignition MQTT Engine.

## Stabilisering 2026-11-08 (ny gjennomgang av fa16913)

Fire feil som gjensto etter runden over. Alle de nye testene feiler med fa16913 og består nå.

1. [x] `link.held` (endring som venter på at målet skal bli ferdig med å lese) ble bare tømt når **målet** falt ut.
       Nå tømmes den når en av endene faller ut eller kilden blir dårlig (`on_bad`), og `on_ready` sjekker
       `_source_ok` før den sender. Test: kilde False → True (venter), brudd, tilbake med False, mål klart → ingen
       True.
2. [x] Kvalitet gjennom flere gatewayledd: `_mark_target` setter også kvaliteten i hubens verdi (`Hub.mark_quality`),
       som kaller `on_bad` for taggen, slik at kvaliteten går videre til neste ledd. En løkke av `direction: both`
       stopper fordi samme kvalitet ikke settes to ganger. OPC UA: Good → Bad → frakoblet gir nå
       UncertainLastUsableValue med verdien (asyncua leser en Bad-variabel som Null, så serveren husker siste gode
       verdi selv i `good`). Tester i `test_gateway_guard.py` og `test_protocols.py`, dok 11 en+nb.
3. [x] `coerce(…, "int")` gikk via float: bruker nå `as_int` (Decimal for desimaltekst). Test gjennom `Hub.write` og
       en gatewaylinje, også negative tall og lister.
4. [x] Tellere: huben sammenligner nå **eksakt** (støy fra float32 rundes allerede bort i driverne), og
       gatewayens `SAME_REL` er 1e-12. Ekkosperren følger oppløsningen til hver ende (`Driver.normalise`):
       float32 i OPC UA (Float), BACnet (REAL), Sparkplug-klient (Float) og Modbus float32, og skalerte
       heltallsregistre i Modbus (`normalise_raw`). 1 000 000 → 1 000 001 Wh og 1000,000 → 1000,001 kWh er
       endringer. `same_value` ser NaN = NaN (ingen endring ved hver lesing). Dok 11 en+nb.
       (Avrundingen til 7 siffer er erstattet i runden under.)

## Stabilisering 2026-11-08 (2) (tredje gjennomgang, av 818561c)

1. [x] Ventende skriving i hastighetsbegrensningen (`link.pending`) ble skrevet etter dårlig → god kilde (også i
       stilleperioden mot KNX). Nå: `on_bad` kansellerer den, hver skriving bærer et gyldighetsmerke for kilden
       (`epoch` per tagg ved dårlig status, `conn_epoch` per tilkobling ved brudd – `_token`) og hoppes over når det
       er endret, og `_write` skriver ikke mot KNX i kildens stilleperiode (`_quiet_towards_knx`).
2. [x] Sparkplug: -1 til en UInt64 hengte serveren (`_varint` løp evig). Nå sjekkes verdiområdet per heltallstype
       (`INT_RANGES`) før koding, og `_varint` avviser negative tall. Grensene testes for alle typene.
3. [x] Sparkplug: en sen NBIRTH/DBIRTH (etter `BIRTH_WAIT_S`, eller ny fødsel mens forbindelsen er oppe) ble behandlet
       som endringer. Nå er fødselsverdier alltid status (`Driver.publish(status=True)` → `Hub.update(status=True)`).
4. [x] En fjernet gateway kunne skrive: oppgaver som alt var laget, sjekker nå `engine.stopped` før skriving.
5. [x] YAML-feil eller ugyldig innstilling ved «last inn på nytt» stoppet alle tilkoblinger. Nå leses og valideres
       fila før den tas i bruk; ved feil beholdes alt som kjører, feilen står i hendelsesloggen, og kommandoen feiler.
6. [x] `protocols.yaml.bak` (og init/setup sin `config.yaml.bak`) fikk umask-rettigheter (0644). Ny `fileutil.py`:
       filen, backupen og den midlertidige filen lages med endelige rettigheter fra start (0600 for nye, eksisterende
       beholder gruppebitene, aldri «andre»), atomisk med `os.replace`.
7. [x] float32: korteste desimal som gir samme float32 (`base.float32`) i stedet for 7 siffer / 6 desimaler – ingen
       tellertrinn går tapt (10 000 001 forblir 10 000 001).
8. [x] `.gitignore`: `protocols.yaml`, `*.bak`, `.*.tmp`, `.claude/settings.json` (og brukerens tillegg: `.env`,
       nøkler, `*.db`, `*.log`).
9. [ ] Gjenstår fra gjennomgangen – tas ett og ett (brukeren 2026-11-08):
   1. [x] BACnet statusFlags og kvalitet i MQTT/Sparkplug. BACnet-klient: `status-flags` leses med nåverdien (RPM
          med halve bolker, enkeltlesing, COV); fault eller out-of-service = «bad» (`status_flags: false` slår av).
          BACnet-server: `reliability` (og dermed fault) når kilden er dårlig/borte/uten verdi. MQTT: `quality` i
          JSON eller `<topic>/quality`, og innkommende JSON-`quality` leses. Sparkplug: egenskapen `Quality`
          (192/0/500) begge veier. Nytt `Driver.owns()`. **Funnet underveis:** et enveis gateway-mål på MQTT ble
          avvist som skrivebeskyttet (MQTT har ingen `role`) – rettet. Lest på anleggene før endringen (bare
          lesing): tomteveien30 2 fault + 14 out-of-service, lerkeveien2 12 fault i UBR1; ingen av dem går i en
          gateway, så endringen gjelder bare visningen. Tester i `test_bacnet.py`, `test_sparkplug.py`,
          `test_protocols.py`, dok 11/12 en+nb.
   1b. [x] Monitor (brukeren 2026-11-08): «Feil» viste antall dårlige avlesninger siden start (vokser hele tiden).
          Nå: antall tagger som er dårlige **nå** (`status` → `bad`). `e` lister dem med årsak (`Driver.why`:
          feilsvar, BACnet `fault`/`out-of-service`, `points` → `why`). Raske tagger (PLS-klokker som `Act_Time`)
          er skjult fra start (`f` viser dem). Markøren i tilkoblingstabellen vises bare med fokus, så det røde
          tallet ikke skjules. Verdikolonnen 24 tegn (dato og tid). Dok 03 en+nb.
   2. [x] Tilgangsstyring på socketen: `knxscada/access.py`, `server.access` med nivåene read / operate / configure
          (kontonavn, `@gruppe`, `*`; høyeste nivå vinner; ikke oppført = avvist og logget). Uten nøkkelen: alle
          med socket-tilgang kan alt (som før). Serverens konto og root har alltid alt. Hver kommando sjekkes
          (`COMMAND_LEVEL`); klienten får `PermissionError` ved avvisning, TUI viser nivået i toppfeltet. Ugyldig
          `server.access` = bare serverkontoen og root. Tester `test_access.py`, dok 04/05/08/12 en+nb.
          Anleggene har ikke `server.access` ennå (brukeren bestemmer nivåene).
   3. [x] OPC UA-serverens standard: endepunkt uten verdi = 127.0.0.1 (koden falt tilbake til 0.0.0.0). Egen
          brukerbehandler og regelsett alltid (asyncuas standard lot alle skrive og «admin» administrere):
          anonyme klienter bare lesing (`anonymous_write: true` for skriving), med `username` bare den brukeren,
          aldri administrator. Server på nettet med anonym tilgang eller uten `security` meldes i hendelsesloggen
          ved hver start (`exposure`). Ingen anlegg har OPC UA-server. Test `test_opcua_server_access`, dok 11/12.
   4. [x] Overvåking av interne oppgaver: `knxscada/watchdog.py`. `supervised()` rundt husholdningen (commit),
          hubens flush-løkke, hver tilkobling, PLS-broene og KNX: uventet feil → hendelseslogg + ny start med
          back-off; oppstartslesingen av KNX gjentas aldri. `Driver.run` fanger nå også BaseException (bacpypes3).
          `Watchdog`: hjerteslag (husholdning, flush-løkke), forsinkelse i hendelsesløkken, `sd_notify`
          (READY/WATCHDOG/STOPPING). `deploy/*.service`: `Type=notify`, `WatchdogSec=60`, `TimeoutStartSec=300`.
          Anleggenes enheter er fortsatt `Type=simple` (virker, men uten systemd-vakthund) til brukeren installerer
          dem på nytt. Tester `test_watchdog.py`, dok 05/08 en+nb.
   5. [ ] Versjonerte releaser med tilbakeføring.
   6. [ ] Beskyttet `main` med krav om grønn CI (brukeren gjør det i GitHub).

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
ekte SD-anlegg, skriving av tabeller mot en ekte PLS, utfasing av `opcua_bridge.py` (venter noen dager etter
overgangen 2026-11-07), skrivetest gjennom KNX-USB, og alle anleggspunktene i `sites/*/TODO.md` (beslutninger, fysisk tilgang
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
