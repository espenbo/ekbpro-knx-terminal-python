[English](../en/10-plc-opcua.md) · **Norsk**

# PLS-broen over OPC UA (f.eks. CODESYS på en WAGO PFC)

Serveren kan utveksle KNX-verdier med en PLS: PLS-en **leser** KNX-verdier (temperaturer, tilbakemeldinger, alarmer) og
**skriver** til KNX (settpunkter, lys av/på). Serveren er OPC UA-**klient** mot PLS-ens egen OPC UA-server,
så PLS-en trenger verken OPC UA-klientlisens eller en ekstra port – bare en global variabelliste (GVL) som er
publisert i Symbol Configuration.

```
KNX-bus ── KNX IP-grensesnitt ── server (én tunnel per grensesnitt, logging) ── OPC UA ── PLS (CODESYS)
                                                                          GVL_KNX:
                                                                            GA_0_1_0_Outdoor_temp : REAL;
                                                                            GA_1_1_5_Hall_light   : BOOL;
```

Alt går gjennom serveren du allerede kjører: én KNX-tunnel per grensesnitt, hver skriving fra PLS-en i hendelsesloggen (bruker
`plc`), synlig i busmonitoren, og alarmene virker som vanlig. Serveren kan kjøre på selve PLS-en (se
[Docker](09-docker.md)) eller på en annen maskin.

## 1. Velg gruppeadressene

```bash
./scada plc-map
```

Dette skriver to filer ved siden av `config.yaml`:

- `plc_map.yaml` – alle gruppeadresser som kan utveksles, én per linje:

  ```yaml
  select: marked
  gvl: GVL_KNX
  addresses:
    - {ga: "0/1/0",   use: false, dir: to_plc, type: REAL,       var: GA_0_1_0_Outdoor_temp}  # Outdoor temp · 9.001
    - {ga: "1/1/5",   use: false, dir: both,   type: BOOL,       var: GA_1_1_5_Hall_light_switching}  # … · 1.001
    - {ga: "3/1/0",   use: false, dir: both,   type: REAL,       var: GA_3_1_0_Living_setpoint}  # … · 9.001
  ```

- `GVL_KNX.st` – den globale variabellisten for CODESYS med de valgte adressene.

Merk adressene PLS-en trenger med `use: true` og kjør `./scada plc-map` på nytt – valgene dine blir tatt vare på,
nye adresser fra ETS-prosjektet kommer med, og adresser som er fjernet, faller ut. Vil du utveksle **alle** adressene,
setter du `select: all` (eller kjører `./scada plc-map --all`).

| Felt | Betydning |
|---|---|
| `use` | `true` = utveksles med PLS-en (ignoreres med `select: all`). Standard `false`. |
| `dir` | `to_plc`: KNX → PLS, PLS-en leser den. `to_knx`: PLS → KNX, PLS-en skriver den. `both`: begge veier. Utelatt = `to_plc`. |
| `type` | IEC-typen til PLS-variabelen, ut fra DPT-en (se under). |
| `var` | Navnet på PLS-variabelen. Du kan gi den nytt navn; det må være et gyldig IEC-navn (bokstaver, sifre, `_`, ikke `__`) og unikt. |
| `ga` | Gruppeadressen. En adresse som ikke lenger finnes i ETS-prosjektet, stopper serveren med en feil – kjør `./scada plc-map` etter at du har endret prosjektet. |

**Navn fra systemtaggene.** Har PLS-en allerede egne KNX-objekter i ETS, navngitt med systemtaggen
(slik ST_KNX-kodegeneratoren gjør: `KNX_Linje_M1.M1_000_564_002_RT6101._01_Rom_SP`), setter du `plc.tag_prefix`
(byggkoden, f.eks. `BY1234`). Adresser som er koblet til et slikt objekt, får da navn etter taggen –
`BY1234_564_002_RT6101_Rom_SP`, `BY1234_564_001_XG101_Un` – og får `dir: to_plc`: PLS-ens eget objekt skriver dem allerede
på bussen, så broen bare leser dem. Andre adresser beholder formen `GA_<adresse>_<navn>`. Setter du prefikset på en
koblingsfil som finnes fra før, kjører du `./scada plc-map --rename` for å gi de eksisterende linjene nytt navn også.
Sett `plc.gvl` for å gi GVL-en (og `.st`-filen) navn, f.eks. `GVL_KNX_Building`, før første `plc-map`; finnes
`plc_map.yaml` allerede, endrer du heller `gvl:` øverst i den.

Standard: adresser som *styrer* noe i visualiseringen (av/på, settpunkter, driftsmodus), får
`both`; alt annet (tilbakemeldinger, følere) får `to_plc`. Å la PLS-en skrive på bussen er et bevisst
valg.

Adresser med en DPT som ikke kan utveksles (relativ dimming, dato/tid, farger …), blir utelatt; `plc-map`
sier hvor mange.

### Typer

| DPT | PLS-type | Merknad |
|---|---|---|
| 1.x (bryter, alarm …) | `BOOL` | |
| 5.001 (prosent), 5.003 (vinkel) | `REAL` | 0–100 % / 0–360° |
| 5.x (teller) | `USINT` | 0–255 |
| 6.x / 7.x / 8.x | `SINT` / `UINT` / `INT` | |
| 9.x (temperatur, lux, ppm …), 14.x (effekt, energi …) | `REAL` | |
| 12.x / 13.x (tellere, energi) | `UDINT` / `DINT` | |
| 16.x (tekst) | `STRING(14)` | |
| 17.001 (scenenummer) | `USINT` | |
| 20.102 (HVAC-modus) | `USINT` | 0 auto, 1 komfort, 2 standby, 3 økonomi, 4 frost-/bygningsbeskyttelse |

## 2. Gjør klar PLS-en (CODESYS)

De nøyaktige menynavnene avhenger av CODESYS-versjonen.

1. Legg til en GVL i applikasjonen, med samme navn som `gvl` i `plc_map.yaml` (standard `GVL_KNX`), og lim inn innholdet
   i den genererte `.st`-filen.
2. Legg til en **Symbol Configuration** i applikasjonen (med OPC UA-støtte), trykk **Build** i verktøylinjen dens
   (listen viser bare det som fantes ved siste bygging), og kryss av for GVL-en med lese-/skrivetilgang.
   Den genererte GVL-en starter med tre attributter:
   - `{attribute 'linkalways'}` – uten den utelater CODESYS en GVL som ingen kjørende kode bruker, fra byggingen,
     og den dukker ikke opp i listen;
   - `{attribute 'qualified_only'}` – PLS-koden bruker variablene som `GVL_KNX.<var>`;
   - `{attribute 'symbol' := 'readwrite'}` – alle variabler publiseres med lese-/skrivetilgang.
3. Last ned applikasjonen til PLS-en og start den.
4. Sjekk at OPC UA-serveren er slått på. På en WAGO PFC: web-based management, *Fieldbus → OPC UA*.

Sjekk fra servermaskinen hva PLS-en tilbyr, og at variablene er synlige:

```bash
./scada plc-browse --endpoints     # sikkerhetsmoduser og innloggingstyper (krever ikke innlogging)
./scada plc-browse GVL_KNX         # variablene i GVL-en (logger inn med plc:-innstillingene)
```

## 3. Koble til

Legg til i `config.yaml` (alle nøkler: [Konfigurasjon](04-configuration.md#plc)):

```yaml
plc:
  enabled: true
  endpoint: opc.tcp://192.168.1.100:4840     # opc.tcp://127.0.0.1:4840 når serveren kjører på PLS-en
```

Start serveren på nytt. Hendelsesloggen viser `PLS (OPC UA) tilkoblet: N variabler`, og topplinjen viser
`● PLC` (grønn når den er tilkoblet).

## Flere PLS-er

Et anlegg kan ha flere PLS-er (f.eks. én per tavle). List dem under `plc.controllers`; hver får sin egen
koblingsfil (`plc_map_<name>.yaml`), sin egen GVL og sin egen OPC UA-forbindelse, og hver leser og skriver KNX
for seg. Se [Konfigurasjon → Flere PLS-er](04-configuration.md#flere-pls-er).

```bash
./scada plc-map --all              # koblingsfil og GVL for alle PLS-er
./scada plc-map --plc board2       # bare denne
./scada plc-browse --plc board2 GVL_KNX_Board2
```

## Sikkerhet

| Innstilling | `config.yaml` |
|---|---|
| Anonym | ingen `username`, `security: None` (standard) |
| Brukernavn og passord | `username:` og `password:` – en bruker i PLS-ens runtime |
| Signert / kryptert | `security: Aes128Sha256RsaOaep,SignAndEncrypt` (eller `Basic256Sha256`, `Aes256Sha256RsaPss`; modus `Sign` eller `SignAndEncrypt`) |

Brukernavn og passord kan kombineres med kryptering, og bør det: med `security: None` sendes passordet
i klartekst (PLS-en må tillate det – på en WAGO PFC: *Allow Password On Plaintext*).

**Lås PLS-ens sertifikat.** Uten `server_certificate` godtar broen det sertifikatet PLS-en viser fram,
så annet utstyr på nettet kan utgi seg for å være PLS-en – få passordet og mate inn verdier som broen
sender til KNX. Eksporter OPC UA-serversertifikatet fra CODESYS (sikkerhetsbildet for enheten) og sett
`server_certificate: var/opcua/plc_cert.der`.

Med kryptering lager serveren sitt eget klientsertifikat første gang (`var/opcua/client_cert.der`, den
private nøkkelen med modus 600).
PLS-en må **stole på** det: i CODESYS åpner du sikkerhetsbildet for enheten, finner det avviste (karantenesatte)
klientsertifikatet «EKBPRO KNX Terminal Python@<host>» og flytter det til de godkjente sertifikatene. Start så
serveren på nytt. På WAGO PFC stiller du inn hvilke policyer som tilbys under *OPC UA Server Security Settings*
(«Aes128Sha256RsaOaep and better» dekker alle tre policyene over).

## Slik utveksles verdiene

- **Ingen telegrammer ved oppstart.** Når broen kobler seg til, får PLS-variablene (`to_plc`, `both`) de KNX-verdiene
  serveren har mottatt siden den startet – det skriver til PLS-en, ikke til bussen. Gamle verdier fra
  databasen fra forrige kjøring sendes aldri; PLS-en får de adressene når bussen har sendt dem
  (`read_on_start: status` eller neste telegram). De gjeldende verdiene i PLS-variablene
  blir bare *notert*; ingenting sendes til KNX før en variabel **endres** etter det. Det samme gjelder
  etter en ny tilkobling: endringer PLS-en gjorde mens forbindelsen var nede, sendes ikke.
- **Ingen telegrammer når PLS-en starter på nytt.** En nedlasting, reset eller online change med reinitialisering setter
  GVL-variablene til startverdiene (`FALSE`, `0`) mens OPC UA-forbindelsen kan bli stående oppe. Uten
  beskyttelse ser det ut som at PLS-programmet slår av alt. Tre sperrer hindrer det:
  1. En melding uten verdi eller med en annen status enn *Good* (CODESYS sender slike mens den laster en
     applikasjon) gjør variabelen ukjent: neste verdi blir bare husket, som den første etter
     tilkobling. En manglende verdi sendes aldri som `FALSE` eller `0`.
  2. Endringer mot KNX holdes igjen i `settle_ms` (500 ms). Endres flere enn `burst_limit` (8) variabler
     i løpet av den tiden, regnes det som en omstart, og ingenting sendes.
  3. Etter en omstart blir endringer bare husket i `restart_quiet_s` (15 s). Så får PLS-en KNX-verdiene
     igjen, som ved oppstart. Hendelsesloggen viser «PLS-omstart oppdaget – ingenting sendt til KNX».

  Må PLS-programmet virkelig koble mange adresser samtidig, bruker du en sentral gruppeadresse («alt av»)
  eller øker `burst_limit`.
- **KNX → PLS:** hver skriving eller respons på bussen oppdaterer PLS-variabelen.
- **PLS → KNX:** en endring i en `to_knx`/`both`-variabel sendes som et skrivetelegram. Skrives *samme* verdi
  igjen, sendes ingenting. En `both`-variabel følger telegrammene på *sin egen* gruppeadresse, så en veggbryter på
  samme adresse holder den oppdatert. Kan lyset også kobles via en *annen* adresse (en sentral
  «alt av», en scene), kan variabelen fortsatt stå på `TRUE` mens lyset er av: les tilbakemeldingsadressen
  (`to_plc`) i PLS-en, og for å slå på igjen skriver du `FALSE`, venter en syklus og skriver så `TRUE`.
- **Ingen ekko:** en verdi som kom fra KNX, sendes ikke tilbake til KNX, og en verdi PLS-en sendte, skrives ikke
  tilbake til PLS-en.
- **Begrenset takt:** høyst én KNX-skriving per adresse hvert `min_interval_ms` (standard 200 ms); endrer PLS-en
  en verdi raskere, sendes den siste verdien. Ikke la en `to_knx` REAL følge en urolig måling
  hver syklus – avrund eller filtrer den i PLS-en.
- **`to_plc`-variabler eies av KNX:** skriver PLS-programmet til dem, overskriver neste telegram dem,
  og ingenting sendes til bussen.
- **Manglende variabler** (GVL-en i PLS-en er eldre enn `plc_map.yaml`) listes i hendelsesloggen; de andre
  variablene virker som før. Broen sjekker hvert minutt om de har dukket opp (etter en online change),
  og kobler seg da til på nytt av seg selv, med de samme oppstartsreglene – du trenger ikke starte serveren på nytt.

## Over til gatewayene (`plc-gateway`)

Den samme utvekslingen kan gå gjennom protokoll-gatewayene ([Protokoller og gatewayer](11-protocols-gateways.md)) i
stedet for broen – ett sted for alle tilkoblinger (F11, Monitor › OPC UA, logging og alarmer på PLS-taggene). GVL-en i
PLS-en blir som den er. `./scada plc-gateway` gjør `plc:` og `plc_map.yaml` om til:

- en OPC UA-klienttilkobling med én tagg per variabel som utveksles (node-ID-ene finnes ved å bla i PLS-en etter
  GVL-en, slik broen gjør; `--prefix` setter dem uten tilkobling). Bare variabler som skrives fra KNX, er skrivbare;
- gatewayen `knx-<pls>` for linjer med `to_plc` og `both`, med `initial: true` – PLS-en får KNX-statusene etter en
  oppstart, slik broen synkroniserte den;
- gatewayen `<pls>-knx` for linjer med `to_knx`.

Broens vakter blir med: gatewayens **omstartsvern** mot KNX (`settle_ms`, `burst_limit`, `restart_quiet_s`, samme
standardverdier og virkemåte) og `min_interval_ms` mot KNX. KNX får aldri startverdier.

```bash
./scada plc-gateway            # skriver ut resultatet (passordet maskert) – kontroller det
./scada plc-gateway --write    # legger det inn i protocols.yaml (en .bak-kopi beholdes)
```

Sett deretter `plc.enabled: false` i `config.yaml` (ellers skriver både broen og gatewayene) og start serveren på
nytt. Tilbake til broen: `plc.enabled: true` og fjern (eller `enabled: false`) de to gatewayene.

## Feilsøking

| Problem | Løsning |
|---|---|
| `GVL_KNX` står ikke i Symbol Configuration | GVL-en brukes ikke av noe kode som kjører, så CODESYS utelot den. Bruk den genererte GVL-en, som har `{attribute 'linkalways'}`, og trykk **Build** i Symbol Configuration. |
| `Fant ikke GVL GVL_KNX i PLS-en` | GVL-en er ikke med i Symbol Configuration, eller applikasjonen kjører ikke. Sjekk med `./scada plc-browse GVL_KNX`. Bruker PLS-en en annen navngiving, setter du `node_prefix` (og `namespace`) ut fra det `plc-browse` viser. |
| `variabler mangler i PLS-en` | Importer den nye `GVL_KNX.st` etter at du har kjørt `plc-map`, og last ned applikasjonen. |
| Tilkobling avvist / tidsavbrudd | Sjekk `endpoint`, at OPC UA-serveren er slått på, og brannmuren. Fra Docker-containeren på PFC-en bruker du `127.0.0.1`. |
| `BadIdentityTokenInvalid` | PLS-en tilbyr ikke anonym tilgang (i CODESYS: brukerautentisering for runtime er slått på). Sett `username` og `password`. |
| `BadIdentityTokenRejected` / `BadUserAccessDenied` | Feil brukernavn/passord, eller brukeren har ikke tilgang til variablene. |
| Kryptering er satt, men PLS-en tilbyr bare `None` | OPC UA-serveren i PLS-en har ikke noe sertifikat ennå. I CODESYS lager du et for OPC UA-serveren i sikkerhetsbildet for enheten og starter runtime på nytt. Sjekk med `plc-browse`: når krypteringen virker, lykkes tilkoblingen med `security:` satt. |
| `BadSecurityChecksFailed` / sertifikatfeil | Godkjenn klientsertifikatet i PLS-en (se Sikkerhet), og start så serveren på nytt. |
| PLS-en skriver, men KNX endres ikke | Er `dir` `to_knx` eller `both`? Er KNX tilkoblet (topplinjen)? Hendelsesloggen viser `PLS → KNX … feilet` med årsaken. |
