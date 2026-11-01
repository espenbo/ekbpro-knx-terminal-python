[English](../en/03-user-guide.md) · **Norsk**

# Brukerveiledning

Start med `./scada`, lokalt eller over SSH (`ssh -t user@server ~/ekbpro-knx-terminal-python/scada`).
Ved oppstart vises logoen i 5 sekunder. En hvilken som helst tast hopper over den.

## Skjermen

```
┌─ toppfelt: tittel · KNX-status · telegrammer/s · alarmer · bruker · klokke ────────────┐
│ meny       │ aktiv visning                                                             │
│ (tre)      │                                                                           │
├────────────┴───────────────────────────────────────────────────────────────────────────┤
│ tastelinje: F1 Oversikt  F2 Rom … │ hint for gjeldende visning                         │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

**Toppfeltet** viser om KNX er tilkoblet (grønn ●) eller frakoblet (rød ●), og trafikken i telegrammer
per sekund. Med flere KNX IP-grensesnitt vises én prikk per grensesnitt (grønn tilkoblet, rød ikke), og
`● PLC` når PLS-broen er satt opp. Det viser også antall ukvitterte alarmer (blinkende rødt),
innlogget bruker med IP, og en klokke. I demomodus vises et gult **DEMO**-merke.

## Taster

| Tast | Funksjon |
|---|---|
| `F1` | Oversikt |
| `F2` | Etasjer og rom |
| `F3` | Gruppeadresser |
| `F4` | Monitor: KNX-busmonitoren; trykket igjen, monitoren for neste protokoll |
| `F5` | Alarmer |
| `F6` | Hendelseslogg |
| `F7` | Trender |
| `F8` | Plantegning |
| `F9` | Skjermsparer |
| `F10` | Avslutt |
| `F11` | Gatewayer (tilkoblinger, tagger og gatewayer – se [Protokoller og gatewayer](11-protocols-gateways.md)) |
| `Ctrl+E` | Anlegg: ett ventilasjonsaggregat om gangen (`ui.systems`) |
| `Ctrl+G` | Energimålere: tabell, effektkurve og forbruk (`ui.systems`) |
| `F12` | Om |
| `Ctrl+T` | Fargetema |
| `Ctrl+L` | Språk |
| `Ctrl+B` | Skjul eller vis menyen |
| `Ctrl+P` | Kommandopalett |
| `Ctrl+Q`, `Ctrl+C` | Avslutt |
| `Tab` / `Shift+Tab` | Neste eller forrige kontroll |
| `Enter` / `Space` | Betjen eller åpne |
| `/` | Søk eller filtrer i gjeldende visning |

Musa fungerer i de fleste terminaler: klikk, rulling og knapper.

Når du flytter markøren i menyen, vises visninger og rom med en gang. Menyvalg som åpner en dialog eller virker
med en gang (fargetema, språk, om, avslutt), trenger `Enter`.

Terminalen tømmes når du avslutter.

## F1 Oversikt

- **Statusfliser:**
  - **Ute** og **Inne snitt** viser temperaturen; ute har en minigraf for siste døgn, inne
    viser spennet min–maks for rommene. Uten en KNX-adresse for utetemperaturen viser Ute utetemperaturføleren til
    det første ventilasjonsaggregatet i `ui.systems`.
  - **Effekt** viser W eller kW, med minigraf. Uten en KNX-adresse for effekt viser den energimålerne i `ui.systems`
    én om gangen (5 sekunder hver), med navnet på måleren i tittelen og «2/6» under.
  - **Lys på** viser hvor mange av lysene som er på.
  - **KNX tlg/s** viser trafikken.
  - **Alarmer** viser aktive og ukvitterte alarmer.
  - Fargen betyr noe: grønt er normalt, gult er advarsel, rødt er kritisk og grått er ingen data.
- **Klima:** faktisk temperatur, settpunkt, modus, varme og en minigraf for siste døgn per rom.
  Temperaturen blir gul når rommet er mer enn 1,5° under settpunktet. Enter åpner rommet.
- **Temperaturgraf:** ute og snitt inne for siste døgn.
- **Romstatus:** ett kort per rom.
  - `● 1/4` betyr at 1 av rommets 4 lys er på (fylt prikk: minst ett på); `○ 0/3` betyr ingen av 3.
  - Gul ramme betyr at et lys er på, gul `!` er advarsel og rød `!!` er kritisk.
  - ♨ betyr at varmen er på.
  - Et klikk åpner rommet.
- **Bussaktivitet:** de siste telegrammene. Bare endrede verdier utheves, gjentakelser er dempet og alarmer er røde.
- **KNX-trafikk:** telegrammer per minutt for siste time.
- **Lys på:** Enter slår av lyset under markøren.

## F2 Etasjer og rom

`F2` åpner første rom i første etasje. Velg etasje og rom i menyen til venstre. Velger du en
etasje, vises alle rommene der samtidig. Hver funksjon i rommet er et kort med kontroller:

| Kontroll | Betjening |
|---|---|
| **Bryter** | Klikk, eller Mellomrom/Enter. Verdien til venstre er statustilbakemeldingen. |
| **Dimmer** | `−` / `+` endrer med 10 % (`ui.dim_step`). `…` setter en nøyaktig verdi. |
| **Settpunkt** | `−` / `+` endrer med 0,5 °C (`ui.setpoint_step`). `…` setter en nøyaktig verdi. |
| **Driftsmodus** | Nedtrekksliste: Auto, Komfort, Standby, Natt/Eco, Frostsikring |
| **Scene** | Skriv et tall 1–64 og trykk Send |
| **Verdier** | Bare visning. Alarmtilstander er røde, og «på» er grønt. |

Hold musa over en rad for å se gruppeadressene, DPT og når verdien sist ble endret.

## F3 Gruppeadresser

Alle gruppeadresser med navn, DPT, levende verdi, tid og kilde.

- **Treet til venstre** filtrerer på hoved- og midtgruppe.
- **Søkefeltet (`/`)** søker i adresse, navn, DPT og beskrivelse. Flere ord betyr at alle må passe.
- **`u`** viser eller skjuler ubrukte adresser og plassholdere.
- **Enter** åpner adressedialogen med detaljer og flagg.
  - `w` skriver en verdi, med bekreftelse.
  - `r` sender en leseforespørsel.
  - `t` åpner adressen i Trender.

Når du skriver verdier, tolkes de etter DPT-en, for eksempel `21.5` (eller `21,5`), `on`, `Comfort`
eller `75`. Er DPT-en ukjent, skriver du råbytes i heks, for eksempel `0C 33`.

## F4 Monitor

Menyvalget **Monitor** har ett valg per protokoll som er i bruk: **KNX**, og for eksempel **OPC UA (2)**,
**Modbus (4)** og **BACnet/IP (1)** (antall tilkoblinger). `F4` åpner det første (KNX, eller den første
protokollen i en installasjon uten KNX); trykket igjen går den videre til neste protokoll.

### Monitor › Modbus, OPC UA, BACnet, MQTT

Øverst tilkoblingene for protokollen med rolle, adresse, antall tagger og tilstand (som i F11). Under vises hver
**verdiendring** i taggene deres (← tid, tilkobling, tagg, beskrivelse, verdi med enhet, kvalitet når den ikke er
god) og hver **skriving** (→ verdi og brukeren som skrev), live. Verdier som ikke endrer seg, vises ikke: huben
sender bare endringer videre.

- **`/`** filtrerer på tilkobling, tagg, beskrivelse, verdi eller bruker; `skriv` viser bare skrivinger,
  `sluppet` slippene.
- **`p`** setter på pause og fortsetter, **`c`** tømmer visningen.
- **Enter, klikk eller `w`** på en linje skriver en ny verdi til taggen, med bekreftelse – bare for tagger med
  `writable: true`. For BACnet viser dialogen også skriveprioriteten (se «Skriving til BACnet» under).

### Skriving til BACnet: prioritet og slipp

En BACnet-verdi skrives inn i én av 16 plasser i enhetens prioritetstabell; den høyeste aktive plassen (det laveste
tallet) bestemmer verdien. Skrivedialogen (Monitor, Anlegg › Innstillinger, F11 `w`) har feltet **Prioritet**,
forhåndsvalgt med taggens eller tilkoblingens `priority` (16 når ingen er satt):

- **16** er den vanlige plassen for automatikk. En skriving der blir overskrevet neste gang regulatoren skriver.
- **8** (manuell betjening) og andre tall under standarden **overstyrer automatikken til verdien slippes**.
  Dialogen advarer om dette, og bekreftelsen nevner prioriteten.
- **Slipp** skriver «ingenting» inn i den valgte plassen, slik at verdien med nest lavere prioritet tar over igjen.
  Slipp den samme prioriteten som du skrev med.
- 1, 2 og 5 (livssikkerhet og kritisk utstyr) tilbys aldri, og serveren avviser dem.

Hendelsesloggen viser hver skriving med prioriteten når den ikke var standard, og slipp som
«sluppet (prioritet 8)».

Monitoren viser det som har skjedd siden TUI-en ble startet (opptil 10 000 linjer for alle protokollene).

### Monitor › KNX (busmonitor)

Alle telegrammer på bussen, live. Hver linje viser tid, retning (← inn, → ut), kilde, mål, navn,
type (Skriv, Svar, Les), DPT, rådata og dekodet verdi. Med flere KNX IP-grensesnitt viser en **Via**-kolonne
hvilket grensesnitt telegrammet kom gjennom (filteret treffer den også).

- **`/`** filtrerer, for eksempel `1/1`, `stue`, `1.1.24` eller `read`. Flere ord betyr at alle må passe.
- **`p`** setter på pause og fortsetter.
- **`e`** (eller knappen *Eksporter*) eksporterer telegrammer. Velg
  - **formatet**: *ETS-gruppemonitor (.xml)* – åpne den i ETS med *Diagnostics → Group Monitor → Open*;
    *CSV* – `;`-separert med overskriftslinje, åpnes i Excel/LibreOffice; eller *JSON-linjer* – ett objekt per linje, for
    skript;
  - **tidsrommet**: telegrammene som vises i monitoren, eller siste time, døgn eller 7 dager fra serverens
    logg (opptil 500 000 telegrammer).

  Filteret gjelder i begge tilfeller. Fila `knx-busmonitor-<date>-<time>.<ext>` lagres i hjemmemappa
  di (`ui.export_dir`). I ETS-fila skrives gruppeadressene uten prosjektprefiks.
- **`c`** tømmer visningen. Loggen i databasen beholdes.
- **`↑` / `↓`, `PgUp` / `PgDn` og `Home` / `End`** ruller. Når du står nederst, følger lista nye telegrammer.
- **Enter eller klikk** åpner adressedialogen.

## F5 Alarmer

Aktive og ukvitterte alarmer vises øverst, historikken nederst.

En alarm har to uavhengige tilstander: **aktiv** eller **normal**, og **kvittert** eller **ukvittert**.
Den forsvinner fra den øverste lista når den både er tilbake til normal og kvittert.

- `k` kvitterer den valgte alarmen
- `K` kvitterer alle
- `h` oppdaterer historikken

Nye alarmer gir et varsel på skjermen og et pip (terminalbjelle) i alle åpne økter. Hvem som kvitterte
en alarm, lagres.

## F6 Hendelseslogg

Viser betjening med bruker og IP («`1/1/0 Stue Vinduslys switching ← On`»), alarmer som utløses, opphører
og kvitteres, KNX-forbindelsen, og klienter som kobler til og fra. `/` filtrerer.

Tekstene lagres på det språket serveren kjørte med da hendelsen skjedde
(se [Språk](#språk-ctrll)), så eldre oppføringer kan være på et annet språk enn brukergrensesnittet.

## F7 Trender

1. **Søk** til venstre: «temp» viser alle temperaturer, og «stue temp» temperaturene i stua.
   Søket dekker adresse, navn, beskrivelse og hovedgruppe.
2. **Enter eller klikk** legger til eller fjerner en kurve. Hver kurve får sin egen farge (●). Du kan ha opptil 6 kurver;
   legger du til en sjuende, faller den eldste ut.
3. **Tidsrom:** 1t, 6t, 24t, 7d eller 30d.
4. **Graftype:** Linje, Trapp, Fylt eller Søyler (snitt per time eller døgn).

`Del` fjerner alle kurver. Kurvene oppdateres live.

Trendene bygger på telegramloggen, så de går så langt tilbake som `server.retention_days`, og bare så langt tilbake
som serveren har kjørt. Loggede tagger i `protocols.yaml` (`log`) står også i lista, med tilkoblingen som adresse.

## Anlegg (Ctrl+E)

Hvert ventilasjonsaggregat har sin egen oppføring under **Anlegg** i menyen (`Anlegg → 360.001`, `360.002` …);
`PgUp` / `PgDn` går til neste eller forrige aggregat. Siden for et aggregat har to faner (`←` / `→` på fanelinjen):

- **Oversikt:** i drift/stoppet, alarm, modus, statustekst, settpunkt og effekten fra måleren, og aggregatet
  tegnet med bokser – tilluft fra uteluft gjennom filter, gjenvinner, varme, kjøl og vifte nederst, avtrekk
  gjennom filter og vifte til avkast øverst – med temperaturer, pådrag, luftmengder og trykk. Filteralarm vises
  som `⚠ Alarm` i filterboksen, en alarm gjør kortet rødt, og verdier fra en tapt tilkobling merkes som gamle.
  På en smal skjerm brukes en mindre tegning. Under: grafer for siste døgn – temperaturer (ute, tilluft,
  avtrekk, avkast, settpunkt) og pådrag (vifter, gjenvinner, varme, kjøl). Grafene krever at taggene logges
  (`log` i `protocols.yaml`).
- **Innstillinger:** taggene i tilkoblingen til aggregatet, delt i grupper – `groups` i `ui.systems`, ellers
  én gruppe per komponent (TFM-kode som `JV401`, eller første ledd i navnet), resten under «Annet». Hver rad viser
  beskrivelsen uten gruppenavnet, verdien, taggen (uten `prefix`) og om den kan skrives (`✎`). Skriv i feltet for
  å filtrere. **Enter** (eller `w`) på en `✎`-rad åpner et vindu for den nye verdien; etter en bekreftelse som
  viser gammel og ny verdi skrives den, og hendelsesloggen viser hvem som gjorde det. Bare tagger med
  `writable: true` i `protocols.yaml` kan skrives.

`m` åpner systemvenderen for aggregatet: én knapp per oppføring i `mode_texts`, deretter en bekreftelse. Rollen
`mode` må være en skrivbar tagg.

Hva som vises, settes opp i [`ui.systems`](04-configuration.md#anleggsvisning-ctrle).

## Energimålere (Ctrl+G)

En tabell over målerne: effekt, energi, strøm og spenning per fase, cos φ, frekvens og effekten siste døgn.
Måleren under markøren (`↑` / `↓`) får en effektkurve og forbruket per time eller døgn, regnet ut fra
energitelleren, og forbruket i dag og i perioden. `d` / `w` / `m` velger 24 timer, 7 dager eller 30 dager.
Kurvene krever at effekt- og energitaggene logges (`log` i `protocols.yaml`).

## F8 Plantegning

Viser bygget tegnet fra [`plan.yaml`](06-floor-plan.md). Hvert rom viser temperatur → settpunkt, lys
(`● 1/4` = 1 av 4 lys på), ♨ varme og alarm. Fargen på rommet viser statusen.

- **`←` / `→`, `PgUp` / `PgDn`, `1`–`9` eller `n` / `p`** bytter etasje. Du kan også klikke på etasjefanene.
- **`↑` / `↓`** velger rom.
- **Enter eller klikk** åpner rommet med alle kontrollene.

## F9 Skjermsparer

Starter av seg selv etter 5 minutter uten betjening (`ui.screensaver_minutes`), eller med `F9`.
Den roterer hvert 20. sekund (`ui.screensaver_rotate_s`) mellom disse sidene (`ui.screensaver_pages` velger dem og
bestemmer rekkefølgen; sider uten data hoppes over):

| Nøkkel | Side |
|---|---|
| `clock` | Klokke, dato og utetemperatur med siste døgn |
| `home` | Hjemme nå: temperatur, varme og lys per rom |
| `sky` | Himmelen over byggene: sola i buen sin om dagen, månen og stjernene om natta, ett vindu per rom som lyser når et lys i rommet er på. Soloppgang og solnedgang regnes ut for `ui.latitude`/`ui.longitude` (standard Fredrikstad). |
| `temps` | Temperaturer med settpunktmarkør |
| `ventilation` | Hvert ventilasjonsaggregat i `ui.systems` som en strektegning: lufta går gjennom kanalene og viftene snurrer når aggregatet går (beveger seg) |
| `heatmap` | Hvert rom som en flis farget etter avviket fra settpunktet, etasje for etasje |
| `energy` | Effekt fra en KNX-effektmåler (bare hvis en blir funnet) |
| `meters` | Alle energimålerne i `ui.systems`: effekt nå, siste døgn, telleren og summen |
| `rain` | Telegrammer som faller nedover skjermen som digitalt regn (beveger seg) |
| `air` | Inneklima, CO₂ og fuktighet (bare hvis slike følere blir funnet) |
| `records` | I dag: varmeste og kaldeste rom, ute min./maks., lys på, aktive alarmer og de travleste gruppeadressene |
| `knx` | KNX-trafikk |
| `bounce` | Klokka med store sifre som spretter rundt på skjermen (beveger seg) |

Sidene merket «beveger seg» tegnes på nytt to ganger i sekundet. Over en treg SSH-forbindelse kan de utelates med
`ui.screensaver_pages`. Utetemperaturen kommer fra `ui.outdoor_ga`, ellers fra utetemperaturføleren til det første
aggregatet i `ui.systems`. Innholdet flytter seg litt ved hvert sidebytte for å unngå innbrenning. **Ukvitterte alarmer tar over hele
skjermen** med langsom rød blinking. En hvilken som helst tast eller et klikk tar deg tilbake til vanlig visning.

## Fargetema (Ctrl+T)

| Tema | Stil |
|---|---|
| Dempet grønn | Standard. Rolig grunnfarge, og statusfargene skiller seg ut. |
| Grønn fosfor | Retro VT100, alt i grønt |
| Amber | Retro IBM- eller Wyse-terminal i amber |
| Moderne mørk | Nøytral mørk med blått, lilla og tydelige signalfarger |
| Solarized | Moderne med lav kontrast, skånsom for øynene |
| Storm Automasjon | Storm Elektro sine farger: dyp blå paneler, grå tekst, hvit fokus, grønt for OK og rødt/oransje for alarmer. Viser Storm Automasjon-logoen ved oppstart og i Om (F12). |

`↑` / `↓` forhåndsviser, Enter velger og Esc avbryter. Valget lagres per bruker **og per installasjon** i
`~/.config/knxscada/ui.json`, så to anlegg på samme maskin kan ha forskjellige farger.

## Språk (Ctrl+L)

Brukergrensesnittet finnes på engelsk (standard) og norsk bokmål. `Ctrl+L`, eller menyvalget
**Språk**, åpner en velger. Valget lagres per bruker i `~/.config/knxscada/ui.json` (samme fil som
fargetemaet), og visualiseringen starter seg selv på nytt med det nye språket med en gang.

For å bruke et annet språk bare i én økt, start med:

```bash
./scada tui --language nb
```

Hvilket språk som brukes: `--language` → brukerens eget valg (Ctrl+L) → `ui.language` i `config.yaml` → engelsk.

Gruppeadressenavn, romnavn og andre navn fra ETS vises slik de står i prosjektet, og oversettes ikke.
Tekster i hendelsesloggen og alarmtekster lages av serveren, på serverens språk
(`ui.language`).

## F12 Om

Viser versjon, lisens, prosjekt, antall adresser og rom, KNX-forbindelse, trafikk, alarmer, bruker, tema,
språk og plattform.
