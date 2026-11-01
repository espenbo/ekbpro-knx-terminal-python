[English](../en/11-protocols-gateways.md) · **Norsk**

# Protokoller og gatewayer (Modbus, MQTT, OPC UA, BACnet)

I tillegg til KNX kan serveren snakke Modbus, MQTT, OPC UA og BACnet/IP, og flytte verdier mellom dem. Alle protokollene er likestilte:
KNX er én modul blant de andre, og serveren kjører også **uten KNX**, f.eks. som en ren Modbus → OPC UA-gateway
på en WAGO Edge-datamaskin.

```
WAGO energimåler ─RS485──┐                                ┌─ OPC UA-server ── Ignition / WinCC
Varmepumpe ─Modbus TCP───┼── server: tilkoblinger ────────┼─ MQTT-broker ──── Home Assistant / Node-RED
KNX-buss ─KNX IP─────────┘   gatewayer (nytt navn, skala) └─ Modbus slave ──── en annen PLS
```

- En **tilkobling** er ett utstyr eller system: en Modbus master eller slave, en MQTT-broker, en OPC UA-klient eller -server.
  **Taggene** er verdiene den har (et register, et topic, en node, en gruppeadresse).
- En **gateway** kopierer tagger fra én tilkobling til en annen, om du vil under et nytt navn, skalert, i én eller
  begge retninger.

Alt står i `var/protocols.yaml` ved siden av `config.yaml` (for et anlegg: `sites/<site>/var/protocols.yaml`; satt med
`server.protocols: var/protocols.yaml` – den ligger i `var/` fordi systemd-tjenesten bare får skrive der), og kan
redigeres i fila eller i TUI-en (**F11 Gatewayer**). Serveren leser fila på nytt når den lagres fra TUI-en;
har du redigert fila for hånd, trykker du **r** i F11 (eller starter serveren på nytt).

## 1. Installer modulene du trenger

Hver protokoll er en modul med sine egne Python-pakker. De installeres bare når du ber om dem:

```bash
./scada modules                       # hvilke moduler som finnes, er installert og er i bruk
./scada modules install modbus mqtt   # modbus: pymodbus + pyserial · mqtt: aiomqtt · opcua: asyncua · bacnet: bacpypes3
```

Start serveren på nytt etterpå. En tilkobling der modulen mangler, vises i F11 og i `./scada gateway`
med kommandoen som installerer den; de andre tilkoblingene kjører likevel.

| Modul    | Klient (vi henter fra utstyr)          | Server (andre henter fra oss)               |
|----------|----------------------------------------|---------------------------------------------|
| `knx`    | alltid på når KNX er satt opp i `config.yaml` | –                                    |
| `modbus` | Modbus TCP / RTU (RS485) master        | Modbus TCP / RTU slave                      |
| `mqtt`   | abonnere og publisere mot en broker    | –                                           |
| `opcua`  | klient mot en PLS eller en annen server | vår egen OPC UA-server for SCADA-systemer  |
| `bacnet` | BACnet/IP-klient mot en enhet (lese, skrive, COV) | vår egen BACnet-enhet for SD-anlegg og SCADA |

MQTT Sparkplug B og Matter er planlagt.

## 2. Tilkoblinger

```yaml
connections:
  meter1:                       # navn: bokstaver, sifre, - og _
    protocol: modbus
    device: /dev/ttyUSB0
    model: WAGO 879-3000        # kjent utstyr: tagger og serieinnstillinger kommer fra malen

  plc1:
    protocol: modbus
    transport: tcp              # tcp · rtu (RS485) · rtu_over_tcp (gjennom en TCP-omformer)
    host: 192.168.1.20
    tags:
      Temp_ute: {address: 'ir:0', type: int16, scale: 0.1, unit: °C}
```

Innstillinger alle tagger kan ha: `type`, `scale` og `offset` (verdi = rå × scale + offset), `unit`, `writable`,
`description`, `log` (lagre verdiene for trender, se [Logging og trender](#logging-og-trender)).

### Modbus

| Innstilling | Standard | |
|---|---|---|
| `role` | `client` | `client` = master, `server` = slave |
| `transport` | `tcp` | `tcp`, `rtu`, `rtu_over_tcp` |
| `host`, `port` | master `127.0.0.1`, slave `0.0.0.0`; `502` | Master: utstyret. Slave: adressen det lyttes på (`0.0.0.0` = alle) |
| `device`, `baudrate`, `parity`, `stopbits` | `/dev/ttyUSB0`, `9600`, `N`, `1` | Bare RTU |
| `unit_id` | `1` | Master: standardadressen til utstyret. Slave: vår adresse, `0` svarer på alle adresser |
| `poll_ms`, `timeout_s` | `1000`, `3` | Bare master (`poll_ms` minst 50) |
| `max_registers` | `100` | Master: høyst så mange registre i én lesing (noe utstyr tar 50 eller færre) |
| `max_gap` | `8` | Master: ubrukte registre som leses over for å slå sammen to lesinger; `0` = bare registre som ligger inntil hverandre (for utstyr som svarer med feil på ukjente adresser) |
| `word_order` | `big` | `big`: høyeste ord først (de fleste enheter) · `little`: laveste ord først |

Tagadresser: `hr:100` holding register, `ir:5` input register, `co:3` coil, `di:7` discrete input,
`hr:100.3` bit 3 i et register. Heks går fint (`hr:0x5002`), og den klassiske nummereringen virker også (`40101` = `hr:100`).
Datatyper: `bool`, `int16`, `uint16` (standard), `int32`, `uint32`, `float32`, `int64`, `uint64`, `float64`.
En tag kan overstyre `unit_id` for tilkoblingen (flere enheter på én RS485-linje).

Noe utstyr leses i én tabell og skrives i en annen. `write_address: hr` skriver taggen med funksjon 6/16 til samme
nummer i holding-registrene – så `ir:10201` med `write_address: hr` kan skrives – og `write_address: hr:200` skriver
et annet sted. `heartbeat_s: 10` får masteren til å skrive en teller (1, 2, 3 …) til taggen hvert 10. sekund mens den
er tilkoblet – for en watchdog i utstyret som går til sikre verdier når styringen slutter å skrive. Et livstegn til en
tagg som ikke kan skrives, meldes én gang i hendelsesloggen.

Som **slave** er gateway-målene registeradresser (`to: hr:100`).

#### Kjent utstyr (`model:`)

For utstyr programmet kjenner, skriver du `model:` i stedet for å liste registrene. Malen gir taggene
og serieinnstillingene; innstillinger du skriver selv, går foran, og dine egne `tags:` legges til (en tag med samme
navn som en tag i malen endrer den, f.eks. en `description`). I F11 velger du utstyret under *Utstyr*.

| `model` | Utstyr | Mal |
|---|---|---|
| `WAGO 879-3000` | MID-energimåler 4PU, direkte, 65 A | `knxscada/protocols/devices/wago-879-30xx.yaml` |
| `WAGO 879-3020` | MID-energimåler 4PS, direkte, 40 A | samme |
| `WAGO 879-3040` | MID-energimåler 2PU CT, strømtransformator | samme |
| `Carlo Gavazzi EM111` | 1-fase energimåler, direkte eller via 5 A strømtrafo | `carlo-gavazzi-em100.yaml` |
| `Carlo Gavazzi EM112` | 1-fase energimåler, direkte, 100 A | samme |
| `Carlo Gavazzi ET112` | 1-fase energimåler med timeteller, direkte, 100 A | samme |
| `Carlo Gavazzi EM270` | dobbel 3-fase energimåler, TCD-strømsensorer opptil 630 A | `carlo-gavazzi-em270.yaml` |
| `Carlo Gavazzi EM280` | dobbel 3-fase energimåler, TCD06-strømsensorer, 32 A | samme |
| `Carlo Gavazzi EM271` | flerkanals nettanalysator, TCD_M-sensorblokker opptil 400 A | `carlo-gavazzi-em271.yaml` |
| `Carlo Gavazzi EM580` | 3-fase MID-energimåler for elbillading, direkte, 65 A | `carlo-gavazzi-em580.yaml` |
| `Carlo Gavazzi WM15` | 3-fase nettanalysator, 5 A strømtrafo | `carlo-gavazzi-wm15.yaml` |
| `Solar-Log Modbus TCP` | Solar-Log datalogger, gratis Modbus TCP-grensesnitt (sanntidsdata for anlegget) | `solar-log.yaml` |
| `Solar-Log Modbus TCP PM` | det samme med PM-lisens: også verdier fra innmatingspunktet og vekselretterne | samme |

Produktnummeret alene virker også (`model: 879-3000`, `model: EM111`).

**WAGO 879-30xx:** RS485, Modbus RTU, 9600 baud, **even paritet** (8E1), unit id 1, lest hvert 5. s. De 35 taggene
er hovedverdiene med navnene fra manualen (vedlegg A3.2): `Voltage`, `L1 voltage` … `L2-L3 voltage`,
`Frequency`, `Current`, `L1 current` …, `Total active power`, `L1 active power` …, reaktiv og tilsynelatende effekt,
`Power factor` …, og energitellerne `Total active energy`, `L1`–`L3 Total active energy`,
`Active energy import`, `Active energy export` og `Total reactive energy`. Alle er `float32` (Float ABCD) og
bare for lesing. Merk: WAGO-manualen sier at pariteten fra fabrikk er *none*, WAGOs CODESYS-bibliotek sier *even* –
malen bruker even, slik våre målere gjør. Sett `parity: N` på tilkoblingen hvis en måler bare svarer da.

Etter tilkobling leser masteren identifikasjonen til måleren én gang – målerkode (`0x4002`: hvilken av de
tre det er), unit id, baudrate, paritet (`0x4011`) og serienummer – og melder den i hendelsesloggen og i
F11, f.eks. `meter1: fant WAGO 879-3000 (4PU – direct connection, 65 A)  adresse 1  9600 E  serienr. 0012AB34`.
Er måleren en annen modell, eller avviker innstillingene fra tilkoblingens, gir en feilmelding beskjed om det.

**Carlo Gavazzi:** RS485, Modbus RTU, 9600 baud, **ingen paritet** (8N1, fabrikkinnstillingen), adresse 1, lest hvert
5. s. Verdiene er heltall med vekt (V × 10, A × 1000, W × 10, kWh × 10) og laveste ord først – malen skalerer
dem, så taggene har den riktige enheten (V, A, W, kWh). Taggnavnene er de fra Carlo Gavazzis manualer: `V L1-N`,
`A L1`, `W sys`, `PF sys`, `Hz`, `kWh (+) TOT`, `kWh (-) TOT` …; på EM270/EM280/EM271 har hvert av de to
sensorsettene egne tagger (`W TOT TCDA1`, `kWh (+) TOT TCDA2` …). Målerne svarer på høyst 50 (EM100-serien) eller 18
(EM270/EM280/EM271) registre per forespørsel; malen setter `max_registers`. «Utenfor område» (EEE) og «ikke målt»
vises som dårlig kvalitet, ikke som et tall. Identifikasjonskoden (`0x000B`) forteller modellen, `0x2000`–`0x2002`
adresse, baudrate og paritet. EM271 har eget protokolldokument (kode 274–277): ingen PF, men effektbehov
(`W TOT TCDA1 DMD`, `… MAXDMD`) og primærstrømmen som måleren har funnet for hver sensorblokk (`TCDA1 CT primary`,
dårlig kvalitet når ingen TCD er tilkoblet).

**Solar-Log:** Modbus TCP, port 502, unit id 1, lest hvert 5. s; slå på grensesnittet i Solar-Log
(*Konfigurasjon → Digitale grensesnitt → Modbus TCP*). Solar-Log leser bare med funksjonskode 4, så alle taggene
er input-registre (`ir:`); 32-bits verdier har laveste ord først. `Solar-Log Modbus TCP` gir sanntidsdata for hele
anlegget (3500–3531): `Pac`, `Pdc`, `Uac`, `Udc`, `Daily yield` … `Total yield` (Wh), `Pac consumption`,
`Daily yield cons.` … `Total yield cons.` og `TotalPower` (installert Wp). `Solar-Log Modbus TCP PM` (lisens 255511)
legger til verdiene fra innmatingspunktet og vekselretterne (10500–10607, `PM Pac`, `PM Iac L1`, `PM CosPhi`,
`PM Status` …; noen krever fastvare 6.0.1). Sensorer som ikke er tilkoblet (innstråling 65535, temperatur −273),
vises som dårlig kvalitet.

PM-modellen har også registrene for effektstyring (10200–10248), med navn `PC …` som i håndboken: `PC PLimit_Type` og
`PC PLimitPerc` (begrensning av aktiv effekt), `PC Reactive_Type`, `PC CosPhi_Fix`, `PC QPerc` (reaktiv effekt),
`PC WatchDog_Tag`, `PC WatchDog_Time`, `PC WatchDog_Limit`, verdiene fra en ekstern avregningsmåler
(`PC Utility_connect_good`, `PC Utility_fUacRS` … `PC Utility_fQacT`) og parameterne for Q(U)-kurvene i fastvare 6
(`PC Uq0_Uc`, `PC Qref_Pbinst`, `PC Qref_total`, `PC Qref_type`). De leses med funksjon 4 og skrives med 6/16
(`write_address: hr`). Med en watchdog-tid (`PC WatchDog_Time` > 0 s) må `PC WatchDog_Tag` skrives innen den tiden,
ellers går Solar-Log til reserveverdiene («Failure of the remote control»). La masteren gjøre det:

```yaml
  solarlog:
    protocol: modbus
    host: 192.168.1.50
    model: Solar-Log Modbus TCP PM
    tags:
      PC WatchDog_Tag: {heartbeat_s: 10}   # kortere enn PC WatchDog_Time
```

En tagg kan ha sin egen `invalid: [65535]` – råverdier som betyr «ingen verdi» bare for den taggen – i tillegg til
malens `invalid`-liste.

Gi taggene navn fra merkesystemet ditt i gatewayen når de skal videre til OPC UA eller BACnet:

```yaml
  - name: Energimåler til SCADA
    from: meter1
    to: scada
    map:
      - Total active power -> 320.001-OE001.P
      - Total active energy -> 320.001-OE001.E
```

#### Test uten utstyret (`./scada simulate`)

```bash
./scada simulate                                  # lister utstyrsmalene
./scada simulate "Carlo Gavazzi EM111"            # Modbus TCP-slave på 127.0.0.1:5020, adresse 1
./scada simulate EM270 --port 5021 --unit 3
./scada simulate "WAGO 879-3000" --device /dev/ttyUSB1   # på en serieport, med malens serieinnstillinger
```

Simulatoren svarer på registrene i malen med troverdige verdier som endrer seg litt (rundt 230 V, 50 Hz,
energitellere som teller opp) og på identifikasjonsregistrene, så en tilkobling med samme `model:` kjenner den igjen
og leser verdier som ser ekte ut. Det en master skriver til taggene med `write_address` (effektgrensene i Solar-Log),
blir stående og leses tilbake. Den skriver ut linjene du setter inn i `protocols.yaml` (`transport: tcp`, `host`,
`port`, `model`). Ctrl+C stopper den.

En ny mal er en YAML-fil i `knxscada/protocols/devices/` – se WAGO-fila og docstringen i
`knxscada/protocols/devices/__init__.py`.

### MQTT

| Innstilling | Standard | |
|---|---|---|
| `host`, `port` | `127.0.0.1`, `1883` | Brokeren |
| `username`, `password`, `tls`, `client_id` | | `tls: true` betyr som regel port 8883 |
| `topic_prefix` | | Settes foran hvert topic, f.eks. `bygg/` |
| `subscribe` | | Topic-filtre (`sensors/#`): hvert topic som kommer inn, blir en tag av seg selv |
| `payload` | `plain` | `plain`: `21.5` · `json`: `{"value": 21.5, "ts": …}` |
| `qos`, `retain` | `0`, `true` | |

Tagger: `topic` (standard er prefiks + tagnavn), `json_key` (plukk verdien ut av et større JSON-objekt) og `set_topic`
(dit skrivinger går; tomt = taggens eget topic, og `<topic>/set` for tagger funnet via `subscribe`). Et gateway-mål publiseres til prefiks + navn; med `direction: both` lytter gatewayen også
på `<topic>/set`.

### OPC UA

| Innstilling | Standard | |
|---|---|---|
| `role` | `client` | `client` mot en PLS/server, eller `server` for SCADA-systemer |
| `endpoint` | `opc.tcp://127.0.0.1:4840` | Server: skriv `opc.tcp://0.0.0.0:4840` for å lytte på alle adresser – uten den kan serveren bare nås fra denne maskinen |
| `username`, `password` | | Tomt = anonym |
| `security` | `None` | Som for [PLS-broen](10-plc-opcua.md) |
| `namespace` | `urn:ekbpro:gateway` | Bare server |
| `publish_interval_ms` | `500` | Bare klient |
| `timeout_s` | `10` | Bare klient |
| `certificate`, `private_key` | klient `var/opcua/client_cert.der`, `…/client_key.pem`; server `var/opcua/server_cert.der`, `…/server_key.pem` | Lages automatisk; brukes med `security` |
| `server_certificate` | – | Klient: lås serverens sertifikat (anbefalt med `security`) |

Klienttagger angir en node-id (`node: "ns=4;s=|var|PLC.Application.GVL.rTemp"`); `./scada plc-browse` lister dem.
Som server blir hvert gateway-mål en variabel, og punktum eller skråstrek i navnet lager mapper:
`Building.Room101.Temperature` → `Objects/Gateway/Building/Room101/Temperature`. Node-id-en er navnet, så den blir
den samme når serveren starter på nytt. Mål med `direction: both` er skrivbare.

Klienten leser høyst så mange noder per forespørsel som serveren tillater (`OperationLimits`; WAGO PFC: 100), så
lange tagglister virker. En REAL (float32) rundes til 7 siffer (16.6, ikke 16.600000381469727). En tabell (f.eks.
et ukeprogram med én verdi per dag) blir en liste; den kan vises og logges, men ikke sendes gjennom en gateway
til én enkelt verdi.

### BACnet/IP

| Innstilling | Standard | |
|---|---|---|
| `role` | `client` | `client` mot én BACnet-enhet, eller `server`: vår egen BACnet-enhet for SD-anlegg og SCADA |
| `address` | | Klient: enheten – `192.168.1.20`, `192.168.1.20:47809`, eller `2:5` (MS/TP-enhet 5 bak en ruter på nett 2) |
| `device_id` | | Klient: enhetens instans; med tom `address` blir enheten funnet med Who-Is |
| `device_instance` | påkrevd | Server: instansen til vår enhet, 0–4194302, unik på BACnet-nettet |
| `device_name` | `knxscada <tilkobling>` | Server |
| `local_address` | denne maskinen | Vår IP-adresse med prefiks, f.eks. `192.168.1.10/24` (prefikset gir kringkastingsadressen). Tom på en klient med `address`: adressen systemet ruter til enheten fra – over VPN (Tailscale) VPN-adressen, så svaret kommer samme vei tilbake |
| `local_port` | server `47808`, klient en ledig port | Vår UDP-port. To BACnet-tilkoblinger kan ikke dele én |
| `poll_ms` | `5000` | Klient |
| `timeout_s` | `3` | Klient: ett nytt forsøk, så en enhet som er av, merkes etter rundt 6 s |
| `cov` | `false` | Klient: abonner også på endringer i nåverdiene (COV); avlesningen fortsetter som reserve |
| `cov_lifetime_s` | `300` | Klient: fornyes av seg selv |
| `max_per_request` | `20` | Klient: egenskaper i én ReadPropertyMultiple (én og én når enheten ikke støtter det) |
| `priority` | enhetens standard (16) | Klient: skriveprioritet 1–16 |
| `bbmd`, `bbmd_ttl` | –, `300` | Registrer som «foreign device» hos en BBMD (`ip:port`) for å nå et annet subnett |

Taggene peker på et objekt: `object: av:3` (kortnavn `ai ao av bi bo bv msi mso msv csv iv piv lav acc`; den lange
formen `analog-value,3` virker også) og, når det ikke er nåverdien, en `property:` (`status-flags`,
`out-of-service` …). Av/på-objekter gir `true`/`false`, multi-state-objekter 1, 2, 3 …. En tagg kan ha sin egen
skrive`priority`. Innganger (`ai`, `bi`, `msi`) er skrivebeskyttet hvis ikke taggen sier `writable: true`. En
operatør kan velge en annen prioritet for én skriving, og slippe en prioritet igjen, i skrivedialogen i TUI-en
(brukerveiledningen, «Skriving til BACnet»); `point_write` tar `priority` og `release: true` til dette.
Prioritet 1, 2 og 5 avvises for operatører.

```yaml
  aggregat1:
    protocol: bacnet
    address: 192.168.1.40
    cov: true
    priority: 8
    tags:
      Tilluft: {object: ai:1, unit: °C}
      Settpunkt: {object: av:10, unit: °C}
      Vifte på: {object: bo:1}
      Modus: {object: msv:2}
```

#### Finn enheter og objekter (`./scada bacnet-scan`)

```sh
./scada bacnet-scan                          # Who-Is: instans, adresse, navn og leverandør for hver enhet
./scada bacnet-scan 192.168.1.40             # objektene i én enhet: navn, nåverdi, enhet, beskrivelse
./scada bacnet-scan --device 40              # det samme, funnet med instansnummeret
./scada bacnet-scan 192.168.1.40 --yaml      # en tilkobling for protocols.yaml med hvert objekt som tagg
```

Who-Is bruker UDP-port 47808, fordi enhetene svarer med en kringkasting til den porten. Porten kan deles med vår
egen BACnet-enhet når serveren kjører som samme bruker; ellers må serveren stoppes, eller skanningen finner ingenting.
Lesing av én enhet bruker en ledig port. `--local 192.168.1.10/24` velger nettverkskort, `--bbmd` når et annet
subnett. Taggene fra `--yaml` får objektnavnene – gi dem nye navn i gatewayen.

Som **server** blir hvert gatewaymål et objekt med samme navn som målet: tall → `analog-value`,
av/på → `binary-value`, tekst → `characterstring-value`, med enheten fra kilden (°C, %, W, kWh …).
Instansnummeret lagres i `var/bacnet/<tilkobling>.json` og beholdes når serveren starter på nytt eller gatewayene
endres; et nummer gis aldri til et annet mål. Gi det selv i gatewaylinjen når SD-anlegget har en liste:
`{from: 1/1/1, to: Rom101.Lys, object: bv:101}` – også `ai`/`bi` for verdier SD-anlegget ikke skal skrive, og `msv`
med `states: 4`. Mål med `direction: both` er skrivbare (bare nåverdien); verdien fra SD-anlegget går tilbake til
kilden. COV-abonnenter får beskjed om endringer (`cov_increment`, standard 0,1, for analoge objekter).

```yaml
  sd:
    protocol: bacnet
    role: server
    device_instance: 1001
    local_address: 192.168.1.10/24
```

### KNX

Tilkoblingen `knx` finnes av seg selv når KNX er satt opp i `config.yaml` (`knx:` og `project:`). Taggene er
gruppeadressene (`1/2/3`, eller `S:1/2/3` med flere prosjekter) eller ETS-navnene deres når de er unike. Verdiene
kodes med DPT-en til adressen; skrivinger logges i hendelsesloggen med gatewayen som bruker.

## 3. Gatewayer

```yaml
gateways:
  - name: Energimåler til SCADA
    from: meter1
    to: scada
    map:
      - Total active power -> Energi.Hovedtavle.Effekt      # et nytt navn for hver tag
      - Total active energy -> Energi.Hovedtavle.Energi

  - name: KNX til MQTT
    from: knx
    to: broker
    deadband: 0.2
    min_interval_ms: 1000
    map:
      - 3/0/1 -> stue/temperatur
      - {from: 1/1/1, to: stue/lys, direction: both}
      - {from: hr:100, to: Ute.Temp, scale: 0.1}
```

En koblingslinje er en av disse:

- `kilde` – samme navn på begge sider;
- `kilde -> nytt navn` – `*` og `?` treffer flere tagger, `{name}` er navnet på kildetaggen;
- `{from: …, to: …, …}` med `direction` (`forward` eller `both`), `scale`, `offset`, `invert`, `deadband`,
  `min_interval_ms`, `initial`, og for en ny måltag `type`, `unit` og `description`.

Innstillinger for hele gatewayen: `direction`, `deadband`, `min_interval_ms`, `initial` og `enabled`.

Regler som holder anlegget trygt og rolig:

- **Ingen ekko.** En verdi som nettopp ble skrevet og leses tilbake, går ikke tilbake til kilden.
- **Les først, send etterpå.** Etter oppstart eller ny tilkobling leser hver tilkobling først statusen til
  taggene sine; F11-visningen viser `leser …` til den er ferdig. Disse oppstartsverdiene huskes på begge sider av
  hver linje, men **sendes ikke til utstyr**: utstyret beholder sin egen verdi til noe faktisk endrer seg, og bare
  endringer som skjer *etter* at begge sider er lest, sendes videre.
  - Modbus master: klar etter første polling av alle registre.
  - OPC UA-klient: klar når hver node er lest én gang.
  - MQTT: klar 1 s etter abonnering (de lagrede meldingene (retained) er statusene).
  - KNX: klar når tilbakemeldings-, føler- og alarmadressene er lest (`server.read_on_start: status`,
    standard, +2 s for de siste svarene); med `read_on_start: false` med en gang.
  - **Gamle verdier sendes aldri videre.** KNX-verdiene i databasen fra forrige kjøring vises bare i
    TUI-en (nedtonet) – OPC UA, MQTT og Modbus får en KNX-verdi først når bussen har sendt den siden oppstart. Til
    da er taggen tom.
  - Våre egne servere (OPC UA-server, Modbus slave) er klare så snart de kjører.
- **Oppstartsverdier fyller bare vårt eget adresserom.** OPC UA-serveren, Modbus-slaven og MQTT-statustopicene
  en gateway lager, får alle verdiene med en gang (og igjen når de kommer tilbake, f.eks. etter omstart av brokeren),
  så klienter som leser dem, ikke ser tomme verdier. `initial` på en gateway eller en linje endrer dette:
  - ikke satt (standard): bare til våre egne servere;
  - `initial: true`: også til utstyr (Modbus master, OPC UA-klient, MQTT-topics for utstyr) – **aldri til KNX**,
    oppstart eller ny tilkobling legger aldri telegrammer på bussen;
  - `initial: false`: til ingenting.
- **Målet leses før det skrives.** En endring til et mål som fortsatt leser, venter til det er
  ferdig, og sendes ikke når målet allerede har den verdien. Er målet ikke tilkoblet, meldes endringen
  som en feil og forkastes.
- **Dødbånd**: tall som har endret seg mindre enn dette, sendes ikke.
- **Hastighetsgrense** (`min_interval_ms`): høyst én skriving per linje og retning i løpet av den tiden; siste verdi
  sendes når tiden er ute.
- KNX HVAC-moduser (DPT 20.102) blir tall (0 auto, 1 komfort, 2 standby, 3 natt/eco, 4 frostsikring) og tilbake.

Måltaggen lages av seg selv når målet er en server (OPC UA-server, Modbus slave, MQTT). Et klientmål
(Modbus master, OPC UA-klient) må ha taggen under `tags:`.

## 4. Sjekk og følg med

```bash
./scada gateway
```

sjekker `protocols.yaml` og lister tilkoblinger og gatewayer – med status, antall tagger og feil i sanntid når serveren
kjører. I TUI-en viser **F11 Gatewayer** tilkoblingene (øverst), taggene til den valgte tilkoblingen med sanntidsverdier
(til venstre) og gateway-linjene med siste verdi og feil (til høyre).

| Tast | |
|---|---|
| `a` | Ny tilkobling (velg protokoll, så et skjema med bare de innstillingene som gjelder) |
| `e` eller Enter | Endre den valgte tilkoblingen |
| `g` / `G` eller Enter | Ny gateway / endre den valgte gatewayen |
| `w` eller Enter | Skriv en verdi til den valgte taggen |
| `r` | Les `protocols.yaml` på nytt |

### Logging og trender

Verdiene til Modbus-, OPC UA-, MQTT- og BACnet-tagger lagres i databasen (tabellen `point_values`, slettes etter
`server.retention_days` som telegrammene) når taggen er valgt med `log`:

```yaml
connections:
  aggregat1:
    protocol: opcua
    endpoint: opc.tcp://192.168.1.100:4840
    log: ["*_PV", "*_SP", "*_AL"]   # navn eller mønstre (* ?) på taggene som skal lagres, "*" = alle
    log_deadband: 0.1               # lagre et tall bare når det har endret seg minst så mye (standard 0)
    log_min_s: 60                   # høyst én lagret verdi per tagg i denne tiden (standard 10 s)
    tags:
      Tilluft: {node: "ns=4;s=|var|PLC.Application.GVL.Supply_PV", unit: °C}
      Settpunkt: {node: "ns=4;s=|var|PLC.Application.GVL.Supply_SP", log: false}   # taggens egen log gjelder først
```

Bare verdier med kvalitet Good lagres. Av/på-endringer lagres alltid. Et tall som endrer seg igjen innenfor
`log_min_s`, holdes tilbake og lagres når intervallet er over, så den siste verdien går aldri tapt. `log_deadband`
og `log_min_s` kan også settes på én tagg. **F7 Trender** viser de loggede taggene sammen med gruppeadressene
(tilkoblingen vises som adresse) – søk, perioder og opptil seks kurver virker på samme måte.

### Alarmer på tagger

Alarmregler i `config.yaml` med `match_point` (et mønster på `tilkobling/tagg`) gjelder taggene i `protocols.yaml`.
`match_name` og `exclude_name` testes mot taggnavnet og mot `description`. Alarmene vises, kvitteres og logges som
KNX-alarmene (F5); `{name}` i teksten er beskrivelsen (eller taggnavnet).

```yaml
alarms:
  - match_point: "aggregat1/*"
    match_name: "_AL$"
    exclude_name: "Reset"
    condition: "true"
    severity: warning
    delay_s: 60                     # tilstanden må vare i 60 s (et kortere blink gir ingen alarm)
    text: "Aggregat 1: {name}"
```

Regler med `match_point` gjelder aldri gruppeadresser, og regler uten gjelder aldri tagger.

## 5. Uten KNX

Utelat `knx:` og `project:` i `config.yaml` (eller sett `knx: {enabled: false}`). Serveren kjører da bare
tilkoblingene og gatewayene; KNX-visningene er tomme, og TUI-en starter på F11.

```yaml
# config.yaml – en ren Modbus → OPC UA-gateway
server:
  socket: var/knxscada.sock
  database: var/knxscada.db
```
