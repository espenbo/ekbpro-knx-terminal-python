# CLAUDE.md – EKBPRO KNX Terminal Python

Det du må vite for å endre og programmere i prosjektet. Status, beslutninger og hva som gjenstår står i
[TODO.md](TODO.md) (koden) og `sites/<anlegg>/TODO.md` (anlegget) – les dem først, og hold dem oppdatert underveis
(økter kan bli brutt). TODO.md i roten er offentlig: ingen IP-adresser eller anleggsdetaljer der.

## Offentlig repo – kundedata holdes utenfor

`origin` er `github.com/espenbo/ekbpro-knx-terminal-python` (offentlig). Ingenting som hører til et anlegg skal i git:

- `sites/` og `tmp/` er i `.gitignore`. Det er også `config.yaml`, `plan.yaml`, `plc_map.yaml`, `GVL_*.st`,
  `*.knxproj`, `*.knxkeys` og `var/` i roten.
- `Ignition_todo.md` står i `.git/info/exclude` (privat).
- Passord ligger i klartekst i `sites/<anlegg>/config.yaml` (`plc.password`) og `sites/<anlegg>/var/protocols.yaml`.
  Ikke kopier dem til filer i repoet, til eksporter eller ut i svar.
- Anleggsdetaljer (IP-adresser, utstyr, avklaringer, oppgaver) skrives i `sites/<anlegg>/CLAUDE.md` og
  `sites/<anlegg>/TODO.md`, ikke her og ikke i TODO.md i roten.
- Eksempler i kode, tester og dokumentasjon skal være generiske (192.168.1.x, «Stue», «meter1»).

## Kjøring og test

```bash
./scada                                   # TUI (standard config: ./config.yaml, eller KNXSCADA_CONFIG)
./scada -c sites/<anlegg>/config.yaml …   # mot et anlegg: server | tui | info | gateway | modules | bacnet-scan …
./scada server --demo                     # simulert buss, egen database (…-demo.db)
venv/bin/python -m pytest -q              # hele pakken, ~2 min; skal være grønn før commit og før omstart
venv/bin/ruff check knxscada/ tests/      # skal gi «All checks passed!» (null merknader siden 2026-11-01)
```

- `./scada` setter `PYTHONSAFEPATH=1` og bruker `venv/`. Protokollmodulene installeres med
  `./scada modules install modbus opcua mqtt bacnet`.
- Skript som leser utpakkede eller nedlastede filer kjøres med `python -I` fra en annen mappe.

## Kjørende tjenester – vær forsiktig

- Systemd-tjenestene for anleggene (`knxscada-<anlegg>`) kjører **koden i denne arbeidskatalogen**. En omstart
  laster det som ligger på disk. Kjør derfor hele testpakken før noen starter tjenestene på nytt.
- Claude kan ikke starte tjenestene på nytt selv (`systemctl restart` krever passord). Be brukeren kjøre
  `sudo systemctl restart knxscada-<anlegg>`, og spør alltid først.
- `protocols.yaml` leses bare ved oppstart eller ved «last inn på nytt» i F11. Endres kode som allerede er importert
  i serveren (f.eks. `opcua_bridge.py`), må tjenesten startes helt på nytt.
- Noen tjenester kjører som en annen bruker: da får du ikke lest loggen (`journalctl -u …`).
- Tilganger per anlegg: egen Unix-gruppe (samme navn som anlegget), mappen `sites/<anlegg>` har setgid, `var/` er
  2770 og `config.yaml` 640. Nye filer skal ha samme gruppe (`install -m 640 …`).

## Sikkerhetsregler for anlegg (aldri brytes)

1. **Ingenting sendes ved oppstart eller gjenoppkobling.** KNX får aldri startverdier (`initial_allowed = False`).
   Gamle verdier fra databasen vises bare (dempet/kursiv) og sendes aldri videre. Se «Oppstart» i TODO.md og
   docstringen i `knxscada/protocols/gateway.py`.
2. **Ikke skriv til KNX-bussen, en PLS eller annet utstyr for å teste** uten at brukeren har sagt ja.
   Lesing (OPC UA, Modbus FC1–4, BACnet ReadProperty, `bacnet-scan`) er greit.
3. Nye tagger mot utstyr settes `writable: false` til skriving er avtalt og testet.
4. Ved en nedlasting til en PLS ble alt lyset slått av av reset-verdier (2026-10-25). Derfor finnes vaktene i
   PLS-broen. Ikke svekk dem.
5. BACnet skrives med en prioritet (`priority` per tilkobling eller tagg). Bruk anleggets standard (står i
   `sites/<anlegg>/CLAUDE.md`), aldri en høyere prioritet uten avtale.

## Arkitektur (kort – utfyllende i `docs/en/08-architecture.md`)

- `knxscada/server.py`: `ScadaServer` eier KNX-tilkoblingene (xknx, én tunnel per grensesnitt), databasen
  (`store.py`, SQLite WAL, commit én gang i sekundet), alarmene og Unix-socketen. Klientene (TUI-ene) snakker JSON
  med serveren (`request` → `cmd`).
- `knxscada/opcua_bridge.py` + `plc.py`: den gamle PLS-broen (`plc:` i config.yaml, `plc_map.yaml`, GVL).
  `./scada plc-gateway` (`plc.gateway_config`) gjør den samme koblingen om til en OPC UA-tilkobling og gatewayer.
- `knxscada/protocols/`: modulære protokoller.
  - `__init__.py`: registeret `PROTOCOLS` med `Field`-er for innstillinger og taggfelt. Skjemaet i F11 og
    valideringen bygges fra dem, så nye innstillinger legges inn her.
  - `base.py`: `Tag` og `Driver` (`session`, `write`, `publish`, `set_ready`, `reads_first`, `echoes`,
    `initial_allowed`, `first_value_is_status`).
  - `hub.py`: én per server; starter driverne, holder `values["conn/tag"]`, logger (`log`), sender verdier til
    gateway-motoren og til `ScadaServer.point_value` (alarmer).
  - `gateway.py`: lenker mellom tilkoblinger (nytt navn, skala, retning, ekko-sperre, dødbånd, rate).
    Mot KNX har den samme vakt som PLS-broen (`settle_ms`, `burst_limit`, `restart_quiet_s`, dårlig status =
    ukjent). Ikke svekk den.
  - `config.py`: lesing, validering (ukjente nøkler er lov), `settings_of` (standardverdier + utstyrsmal), lagring.
  - `devices/*.yaml`: utstyrsmaler (`model:`). De gjelder Modbus: WAGO 879-30xx, Carlo Gavazzi, Solar-Log.
    `./scada simulate MODELL` lager en testslave.
- `knxscada/tui/`: Textual. `app.py` fordeler servermeldinger (`telegram`, `points`, `protocols`, `alarms` …) til
  visningene. F7 Trender (`views.TrendView`) viser både gruppeadresser og loggede tagger (`pt:conn/tag`).
  Anlegg (Ctrl+E, `systems.py`) viser ett anlegg om gangen (menyen: Anlegg → anlegg, data `("system", i)`), med
  tegning, grafer og fanen Innstillinger. Typer: `ventilation` (fast luftveitegning, `render_vent`), `diagram`
  (rørskjema skrevet som tekst i config.yaml med `{felt}`-plassholdere, `diagram.py`/`render_diagram`; ━ tur, ═ retur;
  felt kan lese andre tilkoblinger), `panel` (bare Innstillinger), `power` (effektstyring/maksvokter: forbruk mot grense og én linje per last,
  `power.py`/`render_power`; tagger kan ligge i andre tilkoblinger) og `energy`. Energimålere (Ctrl+G, `energy.py`). Begge leser
  `ui.systems` (roller → tagger). Tegningen og forbruksberegningen er rene funksjoner (`render_vent`, `meter_cells`,
  `consumption`), så de testes uten terminal. Nye visninger legges i
  `MENU`, `KEYBAR_LABELS`, `VIEW_HINTS` og `compose()` i `app.py`, og får `points` via `_handle`.
  Ledige taster: F1–F12 er brukt; Ctrl+T/L/E/G er brukt (Ctrl-taster har `priority`, så de virker også i Input).
  Monitor (F4) har undermenyer per protokoll (`("monitor", proto)`): KNX er `MonitorView`, de andre deler
  `ProtocolMonitorView` (`protomon.py`), som filtrerer appens buffer `point_log` (fra `points` og
  skrivehendelsene `conn/tag ← verdi`) etter `conn_protocol`. Protokollene kommer fra hubens status.
  Skjermspareren (F9, `screensaver.py`) har sider som rene funksjoner av app-tilstanden; sider uten data hoppes over.
  Rom med protokolltagger (`project.point_rooms`) får pseudoadresser `pt:conn/tag` i `model["point_gas"]`.
  Huben sender `points` bare når det finnes klienter (`server.clients`); i TUI-tester: `hub.server.clients = {object()}`.
- Alarmer (`alarms.py`): regler i `config.yaml` `alarms:`. Uten `match_point` gjelder regelen gruppeadresser, med
  `match_point` (glob på `conn/tag`) gjelder den protokolltagger. `delay_s` = innkoblingsforsinkelse.
  Alarmens `ga` er gruppeadressen eller `conn/tag`.

## Konvensjoner

- **Kode, kommentarer, docstrings og commit-meldinger på engelsk. TODO.md, anleggsfiler og svar til brukeren på norsk.**
  Linjelengde opptil ~120.
- **Ruff:** null merknader. Retter du ikke en merknad, skal `# noqa: REGEL` ha en begrunnelse på samme linje
  (eksempel: `raise ValueError(...)  # noqa: TRY004` med kommentar om at det er en konfigurasjonsfeil). Textual-
  klassenes `BINDINGS` skrives `BINDINGS: ClassVar[list[BindingType]] = [...]`.
- **All kode kommenteres grundig, på engelsk** (brukerens krav 2026-11-01). Det gjelder ny kode og kode du endrer:
  - Hver modul har en docstring øverst: hva den gjør, hvordan den henger sammen med resten (server, hub, TUI) og
    hvilke filer eller nøkler den leser.
  - Hver klasse og hver funksjon eller metode (også private) har en docstring: hva den gjør, hva argumentene og
    returverdien betyr, og når den kalles (for eksempel «once per second», «after connecting»).
  - Kommentarer inne i koden forklarer **hvorfor**: valg og avveininger, sikkerhetsregler (ingenting sendes ved
    oppstart, `writable`, PLS-vaktene), særheter ved utstyr og protokoller (grenser, ordrekkefølge, 0- eller 1-baserte
    adresser), ytelse (hvorfor noe hurtigbufres eller kjøres i en tråd) og grensetilfeller (None, tapt forbindelse,
    gamle verdier).
  - Konstanter og attributter får en kommentar med enhet og betydning (`#: Seconds between …`).
  - Testene har en docstring som sier hva de sjekker.
  - Ikke gjenta koden med ord. Skriv det en ny utvikler trenger for å forstå og trygt endre koden.
- **Brukertekster oversettes:** marker dem med `_()` (eller `N_()` for tekst som oversettes ved visning) og legg den
  norske teksten i `knxscada/locale/nb.py`. `tests/test_i18n.py` feiler ved manglende og ubrukte oppføringer og ved
  ulike `{plassholdere}`. Den fanger ikke doble nøkler (den siste vinner stille): sjekk med grep før du legger inn.
- **Dokumentasjonen finnes på to språk:** `docs/en/` og `docs/nb/` har de samme 12 dokumentene. Endres en engelsk
  side, endres den norske likt. `docs/*/12-setup-reference.md` er fasit for alle nøkler (type, standard, gyldige
  verdier). Når nye nøkler legges inn, oppdateres 11 og 12 på begge språk.
- Tester ligger i `tests/` og bruker `FakeServer`/`Mem`/`make_hub` i `tests/test_protocols.py` for huben.
  Ekte Modbus-, OPC UA- og BACnet-tester går mot lokale servere på ledige porter (`free_port()`).
- Nye databasetabeller legges i `SCHEMA` i `store.py` (`CREATE … IF NOT EXISTS`), nye kolonner i `_migrate`,
  og tabellen tas med i `purge_batch`.
- Commit og push bare når brukeren ber om det.
- **Versjoner:** skriv endringer i `CHANGELOG.md` under «Unreleased» (engelsk). En release: `tools/release.py prepare
  X.Y.Z` → commit → `tools/release.py check` → `git tag -a vX.Y.Z` og push av taggen (bare når brukeren ber om det);
  `.github/workflows/release.yml` lager releasen etter grønn CI. Tjenestene kjører fortsatt arbeidskatalogen.

## Erfaringer med utstyr

- **WAGO PFC (CODESYS OPC UA):** høyst 100 noder per Read og per CreateMonitoredItems (`BadTooManyOperations`).
  Klienten og PLS-broen deler i bolker etter `OperationLimits` (`opcua_bridge.operation_limits`).
  Node-ID: `ns=4;s=|var|<PLS-type>.Application.<GVL>.<variabel>`, f.eks. `WAGO 750-8212 PFC200 G2 2ETH RS`.
  Navnerommet heter `CODESYSSPV3/3S/IecVarAccess`. float32 rundes til 7 siffer.
  Kalendere i CODESYS er tabeller (ARRAY, én verdi per dag, mandag først); de kommer som lister
  (`coerce` per element) og vises i Anlegg-kortet med rollene `week_*`. DateTime-verdier støttes ikke (hoppes over).
- **Ignition Modbus-adresser** er 1-baserte (`IR20` = `ir:19`, `HRF20482` = float på `hr:20481`) med mindre
  `zeroBasedAddressing` er på. Skala står i taggen (`scaleMode: Linear`, raw/scaled). Ignition-backup (`.gwbk`) er
  en zip. Tagger ligger i `config/resources/core/ignition/tag-definition/<provider>/`, UDT-er i
  `tag-type-definition/`, OPC-tilkoblinger i `ignition/opc-connection/`, Modbus-enheter i
  `com.inductiveautomation.opcua/device/`. Passordene der er kryptert.
- **WAGO 879-30xx bak Modbus TCP-gateway:** malen passer (Float ABCD, 0x5002 …). Identifiseringen rapporterer
  RS485-pariteten (vanligvis N), og settes `parity` feil, kommer det en feil i hendelsesloggen.
- **IT-nett:** 230 V mellom fasene, så fase–N viser ~133–145 V. Det er riktig, ikke en feil.
- **BACnet over Tailscale:** direkte adresse fungerer. Who-Is (kringkasting) går ikke over VPN.
- **MBS UBR1 (KNX→BACnet):** BBMD-modus, objektene leses med ReadPropertyMultiple; vanlige nåverdier er
  kommanderbare (skriv med prioritet).

## Nettverk

- Utviklingsmaskinen står på et lokalt nett med ett av anleggene. Andre anlegg nås over Tailscale på egne subnett,
  se `sites/<anlegg>/CLAUDE.md`. En adresse fra en IP-liste må pinges på riktig subnett.
- BACnet over VPN: klienten velger kildeadressen etter ruten til enheten (`bacnet.route_source()`).
