[English](../en/07-troubleshooting.md) · **Norsk**

# Feilsøking

Start serveren med `-v` for detaljert logg: `./scada server -v`.
Kjører den som tjeneste, finner du loggen med `journalctl -u knxscada -f`.

## KNX

| Problem | Løsning |
|---|---|
| **«Fikk ikke kontakt med KNX … Prøver igjen om … s»** | Sjekk `gateway_ip` og at maskinen når grensesnittet (`ping`). Har maskinen flere nettverkskort, setter du `local_ip`. Brannmuren må slippe gjennom UDP 3671. |
| **Tilkoblingen blir avvist, eller den kobler til og fra hele tiden** | Alle tunnelene i IP-grensesnittet er i bruk, for eksempel av ETS, en annen visualisering eller Home Assistant. Lukk andre tilkoblinger, bruk `routing` med en IP-router, eller velg en annen tunnel i grensesnittet. |
| **Verdier vises som «–» eller som en nedtonet verdi etter oppstart** | Det har ikke kommet noe telegram til den adressen siden oppstart (en nedtonet verdi er den gamle verdien fra databasen). Ved oppstart leser serveren bare adresser der statusobjektet har leseflagget R i ETS (`read_on_start: status`); de andre får verdi med neste telegram. Med `read_on_start: false` leses ingenting. |
| **Et lys eller en aktuator kobler når serveren starter** | Skyldes `read_on_start: all` (eller eldre versjoner): en annen enhet svarte på en lesing av styreadressen med en gammel verdi. Bruk `false` eller `status`. |
| **Verdier vises som rå bytes** | Gruppeadressen har ingen DPT i ETS. Sett DPT og eksporter prosjektet på nytt. |
| **En bryter reagerer ikke** | Sjekk i busmonitoren (F4) at telegrammet går ut (→). Kommer det ingen status tilbake, har aktuatoren ikke noe statusobjekt, eller statusadressen er ikke koblet. |
| **KNX Secure kobler ikke til** | Sjekk `keyring_file` og `keyring_password`, og at `connection` er `tunneling_secure` eller `routing_secure`. Keyring-fila må være eksportert fra samme prosjektversjon. |

## Visualiseringen

| Problem | Løsning |
|---|---|
| **«ingen server» i topplinjen** | Serveren kjører ikke, eller du bruker en annen `config.yaml` enn serveren. Start den med `./scada server`. |
| **«Permission denied» på socketen** | Brukeren din er ikke med i gruppa til eieren av socketen. Se [Drift og SSH](05-operation-and-ssh.md#tilgang-og-sikkerhet). |
| **Rare tegn eller bokser i stedet for streker** | Terminalfonten mangler strek- eller braille-tegn, eller terminalen er ikke satt til UTF-8. Bytt font, eller sett `ui.ascii: true` for diagrammene. |
| **Store sifre er uleselige** | Sett `ui.big_digits: false` (standard). |
| **F-tastene virker ikke over SSH** | Noen klienter, blant dem PuTTY, sender andre koder. I PuTTY setter du *Keyboard → Function keys* til «Xterm R6». Menyen kan alltid brukes med piltastene og Enter, eller med musa. |
| **`Ctrl+Q` virker ikke** | Terminalen bruker `Ctrl+Q` til flytkontroll (XON). Bruk `F10`, `Ctrl+C` eller menyvalget «Avslutt». |
| **Piltastene virker ikke i plantegningen** | Bruk `PgUp` / `PgDn`, `1`–`9` eller `n` / `p`, eller klikk på etasjefanene. |
| **Tregt over SSH** | Bruk et mindre terminalvindu eller en raskere forbindelse. Skjermspareren reduserer trafikken når ingen bruker skjermen. |
| **Rommene er tomme i F2** | Prosjektet har verken ETS-funksjoner eller en brukbar gruppeadressestruktur. Se [Bruke ditt eget KNX-prosjekt](02-own-project.md#3-slik-bygges-visualiseringen). |
| **Endringer i ETS vises ikke** | Eksporter `.knxproj` på nytt, kjør `./scada import`, og start serveren og visualiseringen på nytt. |
| **Feil språk** | Rekkefølgen er `./scada tui --language` → brukerens eget valg (`Ctrl+L`, lagret i `~/.config/knxscada/ui.json`) → `ui.language` → engelsk. Velg språk på nytt med `Ctrl+L`, eller slett `ui.json`. |
| **Hendelsesloggen eller alarmene er på et annet språk enn menyene** | Disse tekstene lages av serveren på dens språk (`ui.language` da serveren startet) og lagres i databasen. Eldre oppføringer beholder språket de ble skrevet på. Start serveren på nytt etter at du har endret `ui.language`. |
| **Terminalen er rotete etter et krasj** | Kjør `reset`. Ved vanlig avslutning tømmes skjermen automatisk. |

## Prosjektet

| Problem | Løsning |
|---|---|
| **«Finner ikke prosjektfila …»** | Sjekk `project.file`. Relative stier regnes fra mappa `config.yaml` ligger i. |
| **Feil passord, eller prosjektet kan ikke leses** | Sett `project.password`. ETS 4–6 støttes, men prosjekter fra svært gamle ETS-versjoner må åpnes og eksporteres på nytt i en nyere ETS. |
| **«Could not find application_program_ref …»** | En advarsel fra xknxproject om en enhet uten applikasjonsprogram, for eksempel et USB-grensesnitt. Den er ufarlig. |

## Informasjon til en feilrapport

Ta med:

- utskriften fra `./scada info`
- de siste linjene fra `./scada server -v`
- terminalen og SSH-klienten du bruker
- et skjermbilde
