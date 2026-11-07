[English](../en/08-architecture.md) · **Norsk**

# Arkitektur (for utviklere)

## Moduler

```
knxscada/
  __main__.py     kommandolinje: server, tui, init, import, info, plan-dxf, plc-map, plc-browse, modules, gateway
  config.py       lasting av config.yaml, oppløsing av stier
  prefs.py        innstillinger per bruker (~/.config/knxscada/ui.json: fargetema, språk)
  i18n.py         oversettelsesstøtte: _(), N_(), set_language(), LANGUAGES
  locale/
    nb.py         katalog for norsk bokmål (CATALOGUE: engelsk → norsk)
  project.py      ETS-prosjekt(er) → modell (gruppeadresser, rom, funksjoner, datapunkter) + cache;
                  flere prosjekter (adresseprefiks S:1/2/3), rom fra gruppeadressenavn
  dpt.py          dekoding og koding av DPT-verdier, visningstekst, tekster for DPT 1
  alarms.py       alarmregler (glob, regex, betingelse, alvorlighet)
  store.py        SQLite: telegrammer, hendelser, alarmer, loggede taggverdier
  server.py       ScadaServer (xknx per grensesnitt, logging, alarmer, socket-API) + Simulator (--demo)
  knx_interfaces.py  flere KNX IP-grensesnitt: konfigurasjon, Router (utgående), Deduplicator (innkommende)
  knx_usb.py      KNX USB-grensesnitt: HID-rammer, cEMI, tar plassen til xknx sitt IP-grensesnitt (connection: usb)
  setup_wizard.py ./scada setup: spørsmålene, søk etter KNX/BACnet/Modbus/OPC UA, config.yaml
  service.py      ./scada service: systemd-enheten fra deploy/knxscada.service
  client.py       asynkron klient for socketen
  plan.py         plan.yaml og DXF-eksport
  export.py       eksport fra busmonitoren: ETS gruppemonitor-XML (cEMI-rammer), CSV, JSON-linjer
  plc.py          PLS-kobling (plc_map.yaml), IEC-typer, verdikonvertering, CODESYS GVL, tagnavn,
                  flere PLS-er (plc_configs)
  opcua_bridge.py OPC UA-klient mot PLS-en: synk, abonnementer, ekkodemping, vern mot PLS-omstart,
                  sikkerhet
  protocols/      protokollmoduler og gatewayer (docs/nb/11-protocols-gateways.md)
    __init__.py   register: protokollene, innstillingene og tagfeltene deres, installering av moduler
    base.py       Tag og Driver (grunnklassen for alle protokoller), verdikonvertering
    config.py     protocols.yaml: lesing, kontroll (også utstyrsmaler), lagring
    gateway.py    gatewaymotoren: nytt navn, skalering, retning, ekkodemping, dødbånd, regler ved oppstart
    hub.py        kjører driverne i serveren, holder verdiene, snakker med TUI-en
    knx.py, modbus.py, mqtt.py, opcua.py, bacnet.py   driverne
    sparkplug.py  MQTT Sparkplug B: edge node / host-applikasjon, egen protobuf-koding
    devices/      utstyrsmaler (model: WAGO 879-3000 …): én YAML-fil per utstyrsfamilie
  tui/
    app.py        Textual-appen: meny, topplinje, tastelinje, navigering, meldingshåndtering
    views.py      Oversikt, Rom, Gruppeadresser, Busmonitor, Alarmer, Hendelser, Trender
    plan_view.py  plantegning (bufret strektegning)
    widgets.py    betjening (bryter, dimmer, settpunkt, modus …), FastTable, LineLog, LiveStatic
    dialogs.py    adressedialog, verdi, bekreftelse, fargetema, språk
    screensaver.py, splash.py (oppstartslogo og Om-dialogen)
    charts.py     hjelpere for plotext (palett fra temaet, tidsakse, diagramtyper)
    graphics.py   sparklines, søyler, store sifre
    gateways.py   F11 Gatewayer: tilkoblinger, tagger, gatewayer og skjemaene for dem
    protomon.py   Monitor › Modbus/OPC UA/BACnet/MQTT: verdiendringer og skrivinger per protokoll (app.point_log)
    systems.py, energy.py   Anlegg (Ctrl+E) og Energimålere (Ctrl+G) fra ui.systems
    diagram.py    rørskjema i Anlegg (type: diagram): teksttegning med levende felt
    home.py       sammendrag per rom (temperatur, lys, varme, alarm)
    themes.py     fargetemaer
    app.tcss      stilark
Dockerfile        containerbilde (koden i /app, din installasjon i volumet /data)
docker/           knx (kommando inne i containeren), knx-host.sh (åpner TUI-en fra verten)
tests/            pytest-tester
tools/
  check_translations.py   lister uoversatte og ubrukte tekster
```

Den gamle prototypen i én fil ligger i `legacy/80_KNX_Scada.py` og er ikke en del av programmet.

## Modellen (`var/project_cache.json`)

```json
{
  "cache_version": 7,
  "info":      {"name", "tool_version", …},
  "gas":       {"1/1/0": {"address", "name", "description", "dpt": "1.001", "writable", "readable",
                          "used", "placeholder", "range", "objects": ["texts of the linked objects"]}},
  "ranges":    [{"address": "1", "name": "Lighting", "gas": […], "children": […]}],
  "floors":    [{"name": "Ground floor", "rooms": [{"id", "name", "functions": ["F-1", …]}]}],
  "functions": {"F-1": {"name", "usage", "room", "datapoints": [
                  {"label": "On/Off", "kind": "switch", "ga": "1/1/0", "status": "1/4/0", "dpt": "1.001", "role": "SwitchOnOff"}]}},
  "source":    "ets-functions" | "group-addresses" | "room-names",
  "sources":   [["name", "file", "prefix", "label", rooms], …]
}
```

Med flere prosjekter (`project.files`) får adressene, rom-id-ene og funksjons-id-ene i alle prosjekter unntatt det
første prosjektets prefiks (`S:1/1/0`), etasjene heter «<label>: <floor>», `ranges` har én toppnode per prosjekt
(med et `project`-felt som holder prefikset), og modellen har en `projects`-liste (`name`, `label`, `prefix`,
`info`). Cachen bygges på nytt når en prosjektfil er nyere, eller når `sources` (prosjektlisten eller
`rooms`-innstillingen) er endret.

`kind` er en av `switch`, `percent`, `setpoint`, `mode`, `scene`, `value` og `bool`. `ga` er adressen det
skrives til, og `status` er adressen som vises. Navn fra ETS lagres som de er; genererte etiketter
(f.eks. «On/Off», «Setpoint») lagres på engelsk og oversettes når de vises. Øk `CACHE_VERSION` i
`project.py` når formatet endres.

## Socket-protokoll

Protokollen bruker JSON-linjer over en Unix-socket. Når en klient kobler til, sender serveren `hello` med hele tilstanden.

**Fra serveren:**

| `type` | Innhold |
|---|---|
| `hello` | `state` (siste verdi per GA), `status`, `stats`, `alarms`, `demo` |
| `telegram` | `t`: `{id, ts, dir, src, dst, via, type, raw, value, text}` (`via`: KNX-grensesnittet, når det er flere), pluss `state` for skriving og svar |
| `status` | `{connected, connection, gateway, started, interfaces: [{name, gateway, project, connected, connection}], plc?}`. `connected` er sann så lenge minst ett grensesnitt er tilkoblet; `connection` kan være `partly connected`. |
| `stats` | `{count, per_min, clients}` hvert sekund |
| `alarms` | Alle åpne alarmer (aktive eller ikke kvittert) |
| `event` | Ny hendelse |
| `reply` | Svar på en kommando: `{id, ok, data \| error}` |

**Fra klienten:** `{"id": n, "cmd": …, "user": "user@ip", …}`

Navnet i revisjonsloggen hentes fra kjernen (`SO_PEERCRED`: kontoen til prosessen som kobler til);
bare SSH-adressen etter `@` kommer fra klienten. Når klienten kjører under serverens egen konto
(f.eks. inne i Docker), brukes klientens navn, begrenset til bokstaver, sifre og `@ . : -`.

| `cmd` | Parametere | Svar |
|---|---|---|
| `hello` / `bye` | – | logger tilkobling og frakobling |
| `write` | `ga`, `value`, `dpt?` | `value` tolkes etter DPT-en i prosjektet (`dpt` bare når ETS ikke har noen). `{"raw": "0C 33"}` gir rå bytes. Bare adresser i ETS-prosjektet. |
| `read` | `ga` | sender GroupValueRead (bare adresser i ETS-prosjektet) |
| `ack` | `alarm_id` eller `all: true` | antall kvitterte |
| `telegrams` | `limit` (≤ 5000), `ga?`, `before_id?` | liste |
| `events` | `limit` (≤ 5000) | liste |
| `alarm_history` | `limit` (≤ 5000) | liste |
| `history` | `ga` eller `point` (`tilkobling/tagg`), `hours`, `max_points?` (4000) | `[[ts, number], …]`. Over `max_points` laveste og høyeste verdi per tidsintervall. Kjører i en egen arbeidstråd. |
| `logged_points` | – | de loggede taggene i `protocols.yaml` (`key`, `conn`, navn, enhet, beskrivelse, verdi) |
| `traffic` | `minutes` (≤ 1440) | telegrammer per minutt |

Eksempel fra skallet:

```bash
echo '{"id":1,"cmd":"history","ga":"3/0/0","hours":1}' | socat - UNIX-CONNECT:var/knxscada.sock | tail -1
```

Klienter som ikke henger med (for stor skrivebuffer), kobles fra av serveren.

## Database (SQLite, WAL)

- `telegrams(id, ts, dir, src, dst, type, raw, value JSON, num REAL, via)`
  - `via`: KNX-grensesnittet telegrammet kom gjennom (bare med flere grensesnitt). Eldre databaser får
    kolonnen automatisk.
  - `dir`: `in` | `out`; `type`: `write` | `response` | `read`
  - `num` brukes til trender (boolske verdier som 0/1).
  - Indekser på `(dst, ts)` (trender), `(ts)` (trafikk, sletting) og en delvis indeks på `(dst, id)` for telegrammer
    som ikke er lesinger, brukt til å hente siste verdi per adresse ved oppstart.
- `events(id, ts, level, kind, user, text)`
  - `level`: `info` | `error` | `alarm`
  - `kind`: `system` | `knx` | `user` | `operation` | `alarm` | `plc`
  - `text` lagres på serverens språk på tidspunktet for hendelsen.
- `alarms(id, ga, text, severity, raised_ts, cleared_ts, acked_ts, acked_by, value)`
  - `severity`: `critical` | `warning` | fritekst. Gamle rader med `kritisk` / `advarsel` gjøres om ved lasting.
  - `ga`: en gruppeadresse, eller `tilkobling/tagg` for en tagg i `protocols.yaml` (regler med `match_point`).
- `point_values(id, ts, point, value JSON, num REAL)`: verdiene til taggene som er valgt med `log` i `protocols.yaml`
  (`point` = `tilkobling/tagg`). Indekser på `(point, ts)` og `(ts)`; slettes sammen med telegrammene.

Telegrammer og hendelser skrives (commit) én gang i sekundet; alarmendringer skrives straks. Én gang i timen
sletter serveren telegrammer, hendelser og loggede taggverdier eldre enn `retention_days` i porsjoner (5000 rader om gangen), og gir
event-loopen tid mellom hver porsjon, så en stor sletting ikke blokkerer serveren.

## Ytelse i TUI-en

Erfaringer fra utviklingen, gjelder for Textual 8:

- `Static.update()` ber alltid om ny layout av hele skjermen. Bruk `LiveStatic.set()`, som bare tegner på nytt
  ved en faktisk endring og uten layout. Gi slike widgets fast høyde i CSS.
- `DataTable` måler hver celle når den settes inn, og `remove_row` er O(n). `FastTable` hopper over målingen når alle
  kolonnene har fast bredde. Til logger bruker du `LineLog`, som bare formaterer synlige linjer og bufrer dem.
- Store tegneflater (plantegningen) bygges som `Strip`-er med sammenslåtte segmenter og bufres. Det holder
  SSH-trafikken lav.
- Hvert telegram sendes til alle visninger (`on_telegram`), så hold det billig: appen har en bufret liste over
  visninger, og skjulte visninger (gruppeadresser, trender) husker bare siste verdi per rad og bruker den når
  de vises igjen. Søkefelt som bygger store tabeller på nytt, venter på en kort pause i skrivingen (0,2 s).
- Oversikten beregner romstatusen én gang per endring og bruker den om igjen i klimatabellen, romkortene og
  statusflisene.
- `App.query_one()` søker i den *aktive* skjermen. Når dialoger eller skjermspareren ligger øverst, bruker du `app.main.query_one()`.
- Ikke bruk attributtnavn som Textual bruker internt, for eksempel `_closing` og `_tooltip`.

## Oversettelser

All tekst brukeren ser, skrives på engelsk i kildekoden. Tilgjengelige språk står i
`LANGUAGES` i `knxscada/i18n.py` (`en`, `nb`). Norsk bokmål ligger i `knxscada/locale/nb.py`, en vanlig
dict `CATALOGUE` som kobler den engelske teksten til oversettelsen. Det brukes ingen gettext-verktøykjede (`.po`/`.mo`).

- Pakk inn tekst i `_()` der den **vises**: `_("Bus monitor")`, `_("{n} telegrams").format(n=n)`.
  Oversett malen først og kall `.format()` etterpå, så plassholderne blir med i katalognøkkelen.
- Merk konstanter på modulnivå med `N_()`. Den returnerer teksten uendret, men lar kontrollskriptet finne den; koden
  som viser konstanten, pakker den inn i `_()`. Unngå `_()` ved import i kjernemodulene – serveren og
  kommandoene velger språk etter at de er importert.
- Unntak: beskrivelsene av tastebindinger i Textual (`BINDINGS`) lages når TUI-klassene defineres. Derfor
  setter `__main__.py` språket for brukergrensesnittet *før* `knxscada.tui` importeres, og derfor starter et språkbytte fra
  menyen TUI-prosessen på nytt (`os.execv`, se `run()` i `tui/app.py`) i stedet for å bytte underveis.
- Mangler en oversettelse, brukes den engelske teksten.
- Navn fra ETS (gruppeadresser, rom, funksjoner) og tekster fra `config.yaml`/`plan.yaml` er brukerdata og
  oversettes aldri.

Språket velges i denne rekkefølgen: `./scada tui --language` → brukerens valg (Ctrl+L, lagret i
`~/.config/knxscada/ui.json`) → `ui.language` i `config.yaml` → engelsk. Serveren og de
andre kommandoene bruker `ui.language`. `set_language()` godtar `no` som alias for `nb` og faller tilbake til engelsk for
ukjente koder.

Sjekk katalogen etter at du har endret tekster:

```bash
venv/bin/python tools/check_translations.py
```

Den lister tekster som brukes i koden uten å ha oversettelse, og katalogoppføringer som ikke brukes lenger.

**Legge til et språk:**

1. Lag `knxscada/locale/<code>.py` med en `CATALOGUE`-dict (kopier `nb.py` og oversett verdiene).
2. Legg koden og språkets eget navn til i `LANGUAGES` i `knxscada/i18n.py`.
3. Kjør `tools/check_translations.py` mot den nye katalogen til ingenting mangler.

## Tester

Testene ligger i `tests/` og dekker DPT-konvertering, alarmregler, tolkingen av ETS-prosjekter (også
flere prosjekter og rom fra navn), flere KNX-grensesnitt (ruting, fjerning av duplikater), lageret, reglene
ved oppstart (ingen telegrammer), TUI-widgetene, PLS-koblingen, OPC UA-broen mot en ekte asyncua-server
(inkludert vern mot PLS-omstart), protokollmodulene og gatewayene mot ekte Modbus, OPC UA og MQTT
(`test_protocols.py`), utstyrsmalene (`test_devices.py`) og Docker-filene.
pytest står i `requirements-dev.txt`:

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m pytest
```

## Utvide

**Ny betjeningstype:**

1. Gi datapunktet en ny `kind` i `project.build_datapoints`.
2. Lag en underklasse av `DatapointRow` i `tui/widgets.py`.
3. Koble den inn i `make_row`.

**Ny visning:**

1. Arv fra `View` i `tui/views.py`, og implementer `on_show_view`, `on_telegram` og `on_theme_changed`.
2. Legg den til i `ScadaApp.compose` og i `MENU`, og gi den en kort etikett i `KEYBAR_LABELS` og tastehint i
   `VIEW_HINTS` (alt i `tui/app.py`).
3. Legg de nye tekstene inn i katalogene.

**Nytt tema:** legg til et `Theme` i `tui/themes.py`, i `THEMES` og i `THEME_CHOICES`.

**Ny alarmbetingelse:** se `Rule.active` i `alarms.py`.

## Teste uten installasjon

```bash
./scada server --demo         # simulerer følere, aktuatorer med statusekko og feilmeldinger
```

Simulatoren bygger på prosjektmodellen. Den svarer på leseforespørsler og sender styretelegrammer i ekko til
statusadressen, også sammenhengen mellom en bryter og dimmeverdien. Demomodus bruker sin egen database
(`knxscada-demo.db`).
