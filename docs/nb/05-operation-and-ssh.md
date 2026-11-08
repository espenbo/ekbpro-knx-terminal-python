[English](../en/05-operation-and-ssh.md) · **Norsk**

# Drift og SSH

## Server og klienter

```
 KNX IP-grensesnitt ──UDP/TCP──  scada server  ── Unix-socket ──┬── ./scada (lokalt)
                                   │                            ├── ssh -t … ./scada
                                   └── var/knxscada.db          └── ssh scada@…  (egen bruker)
```

- **Serveren** holder KNX-forbindelsen, logger alt, vurderer alarmer og deler sanntidsdata.
  Den kjører hele tiden.
- **Visualiseringen** (`./scada`) er en ren klient. Start og stopp den så ofte du vil. Den påvirker verken
  logging eller alarmer.

## Serveren som systemd-tjeneste

Enklest er `./scada service`, kjørt som brukeren som eier programmappa:

```bash
./scada service install                                   # standard config.yaml → knxscada
./scada -c sites/hus/config.yaml service install          # et anlegg → knxscada-hus
./scada service status                                    # installert, aktivert, kjører, siden når
./scada service remove
```

`install` lager enheten fra [`deploy/knxscada.service`](../../deploy/knxscada.service) for konfigurasjonen som er i
bruk: brukeren din, gruppen til anleggsmappa, `-c <config.yaml>` for et anlegg og `ReadWritePaths` = mappa til
databasen. Uten root skrives enheten til `var/systemd/<navn>.service`, og `sudo`-kommandoene skrives ut (install,
`daemon-reload`, `enable`, `restart`), så ingenting endres før du kjører dem. `--name` gir et annet tjenestenavn.

**Vakthund.** Alle bakgrunnsoppgavene i serveren (husholdningen med databasecommit, protokolltilkoblingene,
PLS-broene, KNX-tilkoblingen) kjører under oppsyn: stopper en med en uventet feil, skrives feilen i hendelsesloggen
(F6, «… feilet: … – startes igjen om N s»), og oppgaven starter igjen. En løkke som slutter å slå («housekeeping har
ikke kjørt på 45 s») og en hendelsesløkke som har stått fast («Serveren sto fast i 3,2 s»), meldes også. Enheten er
`Type=notify` med `WatchdogSec=60`: serveren sier fra til systemd når den er oppe og pinger hvert 5. sekund mens
løkkene går; henger den, stopper pingene, og systemd starter den på nytt. En omstart sender ingenting til KNX eller
utstyr. En enhet installert før 0.3.0 (`Type=simple`) virker fortsatt, men uten systemd-vakthunden; installer den
på nytt med `./scada service install` for å få den.

For hånd:

[`deploy/knxscada.service`](../../deploy/knxscada.service) inneholder plassholderne `YOUR_USER` og `INSTALL_DIR`.
Kjør dette fra programmappa som brukeren som eier den – det fyller dem inn og installerer tjenesten:

```bash
sed -e "s#YOUR_USER#$USER#g" -e "s#INSTALL_DIR#$PWD#g" deploy/knxscada.service \
    | sudo tee /etc/systemd/system/knxscada.service
sudo systemctl daemon-reload
sudo systemctl enable --now knxscada
journalctl -u knxscada -f          # følg loggen
```

Stopp og start serveren med `sudo systemctl stop|start|restart knxscada`. Serveren stopper med en gang, også når
klienter er tilkoblet. Klientene viser «mistet serveren» og kobler seg til igjen av seg selv når serveren er tilbake.
Kjører du serveren i en terminal, stopper `Ctrl+C` den. En ny `Ctrl+C` tvinger den til å avslutte.

Enheten er herdet: filsystemet er skrivebeskyttet for serveren unntatt `var/`, den får ingen nye rettigheter og
ingen capabilities, og minnet er begrenset til 1 GB. Derfor hører `protocols.yaml` hjemme i `var/`
(`server.protocols: var/protocols.yaml`): bare der kan F11 lagre den.

### Flere installasjoner som tjenester

Flere anlegg kan kjøre på én maskin, hvert med egen server, socket og database. Legg hvert i sin egen
mappe under `sites/` (ignoreres av git), f.eks. `sites/cabin/config.yaml` med prosjektfila ved siden av, og
bruk malen [`deploy/knxscada@.service`](../../deploy/knxscada@.service) – delen etter `@` er
mappenavnet:

```bash
mkdir -p sites/cabin
./scada -c sites/cabin/config.yaml init ~/Cabin.knxproj --gateway 10.0.0.20

sed -e "s#YOUR_USER#$USER#g" -e "s#INSTALL_DIR#$PWD#g" deploy/knxscada@.service \
    | sudo tee /etc/systemd/system/knxscada@.service
sudo systemctl daemon-reload
sudo systemctl enable --now knxscada@cabin        # server for sites/cabin
journalctl -u knxscada@cabin -f                   # loggen dens
./scada -c sites/cabin/config.yaml                # visualiseringen dens
```

Hovedinstallasjonen (`config.yaml` i programmappa, tjenesten `knxscada`) og et hvilket som helst antall
`knxscada@<site>`-tjenester kjører side om side:

```bash
systemctl status knxscada knxscada@cabin knxscada@office
sudo systemctl restart knxscada@office            # etter endring i sites/office/config.yaml
```

Nås KNX-grensesnittene til et anlegg gjennom VPN (Tailscale, WireGuard), la tjenesten starte etter den:
`sudo systemctl edit knxscada@office` og legg til `[Unit]` / `After=tailscaled.service`.

## Bruk over SSH

```bash
ssh -t user@server ~/ekbpro-knx-terminal-python/scada
```

`-t` gir en ekte terminal, og det er påkrevd.

Hver SSH-bruker kan velge sitt eget fargetema (`Ctrl+T`) og språk (`Ctrl+L`). For én økt på et annet
språk: `ssh -t user@server ~/ekbpro-knx-terminal-python/scada tui --language nb`.

**Anbefalte terminaler:**

- Linux: GNOME Terminal, Konsole, Kitty, WezTerm
- macOS: iTerm2
- Windows: Windows Terminal
- iPhone/iPad: Blink Shell, Termius eller Prompt 3. Helt enkle terminalapper (uten alternativ skjerm, uten
  xterm-spørringer) kan ikke vise fullskjermprogrammer: teksten `$p$p` eller `22;default` dukker opp på skjermen.

**Terminalinnstillinger:**

- `TERM=xterm-256color`. Støtter terminalen truecolor, får du riktigere farger med `COLORTERM=truecolor`.
- En font med rammetegn og braille-tegn (`─ │ ┌ ⣿`), for eksempel DejaVu Sans Mono, JetBrains Mono eller Cascadia Mono.
- Minst ca. 120 × 35 tegn. Oversikten utnytter større vinduer.
- **PuTTY:** sett *Window → Translation* til UTF-8 og *Terminal → Keyboard → Function keys* til «Xterm R6».

### Egen SSH-bruker som går rett inn i visualiseringen

```bash
sudo useradd -m -s /bin/bash -G $USER scada      # gruppen til programeieren gir tilgang til socketen
sudo passwd scada                                 # eller legg inn en SSH-nøkkel
sed "s#INSTALL_DIR#$PWD#g" deploy/sshd_scada.conf | sudo tee /etc/ssh/sshd_config.d/scada.conf
sudo systemctl reload ssh
```

Kjør det fra programmappa som programeieren (`INSTALL_DIR` i `ForceCommand` fylles inn av `sed`).
Etter det går `ssh scada@server` rett inn i
visualiseringen. Når den avsluttes, lukkes økta. For å låse språket for denne brukeren, legg
`--language nb` til på `ForceCommand`-linja (eller la brukeren velge med `Ctrl+L`).

`scada`-brukeren må kunne lese programmappa og `var/`, og skrive til socketen. Sjekk at
hjemmemappa til eieren har `chmod o+x` eller gruppetilgang.

## Tilgang og sikkerhet

- **Alle som kan koble seg til socketen, kan betjene anlegget** hvis ikke `server.access` sier noe annet (se
  under). Socketen har rettighetene `660` (eier
  og gruppe). Gi bare brukere du stoler på medlemskap i gruppen. Bedre: installer programmet under en egen
  konto (f.eks. i `/opt`), så gruppen gir tilgang til socketen, men ikke skrivetilgang til programfilene.
- All betjening logges i hendelsesloggen (F6) med Linux-kontoen – hentet fra kjernen, ikke fra
  klienten – og IP-en til SSH-klienten. Bare adresser som finnes i ETS-prosjektet, kan skrives til.
- `config.yaml` kan inneholde passord (KNX Secure-keyring, PLS): `init` lager den med modus `600`; behold det slik
  (`chmod 600 config.yaml`).
- systemd-enheten i `deploy/` er herdet (skrivebeskyttet filsystem unntatt `var/`, ingen nye rettigheter, ingen
  capabilities). SSH-oppsettet for `scada`-brukeren slår av all videresending, også av socketen.
- Bruk SSH-nøkler, ikke passord, og vurder `AllowUsers` i `sshd_config`.
- Serveren åpner ingen nettverksporter. Den lytter bare på den lokale Unix-socketen.
- Hold KNX-nettet atskilt fra internett. Bruk KNX Secure der det går.

## Tilgangsnivåer (`server.access`)

Socketen slipper inn eieren og gruppen. Innenfor det gir `server.access` i `config.yaml` hver Linux-konto et nivå:

| Nivå | Kan |
|---|---|
| `read` | se alt: verdier, telegrammer, trender, alarmer, hendelsesloggen, tagglistene |
| `operate` | også skrive verdier (KNX og protokolltagger), sende KNX-leseforespørsler, kvittere alarmer |
| `configure` | også endre `protocols.yaml` i F11 og laste den inn på nytt |

```yaml
server:
  access:
    configure: [caesar]          # kontonavn …
    operate: ["@tomteveien30"]   # … @gruppe (primær eller ekstra Unix-gruppe) …
    read: ["*"]                  # … eller * (alle kontoer som kan åpne socketen)
```

En konto får det høyeste nivået som nevner den. En konto som ikke står noe sted, avvises når den kobler til (TUI-en
viser «har ikke tilgang», hendelsesloggen «Tilkobling avvist»). Serverens egen konto og root har alltid alle nivåer.
Uten `server.access` kan alle som kan åpne socketen gjøre alt, som før – en oppdatering stenger ingen ute. Kontoen
hentes fra kjernen, ikke fra klienten. Et nivå under `configure` vises i toppfeltet ved brukeren; en avvist kommando
gir en melding som «kari har ikke lov til dette (tilgang: les, krever: operatør)». Endringer gjelder når serveren
startes på nytt; en feil i `server.access` logges og slipper da ingen inn utenom serverens egen konto og root.

## Sikkerhetskopi

Ta vare på disse:

| Fil | Innhold |
|---|---|
| `config.yaml`, `plan.yaml` | Konfigurasjonen din |
| `*.knxproj`, `*.knxkeys` | ETS-prosjekt og eventuell keyring. Keyring-fila er sensitiv. |
| `var/knxscada.db` | Historikk: telegrammer, hendelser og alarmer |
| `plc_map.yaml`, de genererte `GVL_*.st` | PLS-kobling, hvis du bruker PLS-broen |
| `var/opcua/` | OPC UA-klientsertifikat og nøkkel. Går de tapt, lages nye, og PLS-en må godkjenne dem på nytt. Sensitivt. |

Databasen kan kopieres mens serveren kjører:

```bash
sqlite3 var/knxscada.db ".backup 'backup-$(date +%F).db'"
```

`var/project_cache.json` bygges opp igjen automatisk og trenger ikke sikkerhetskopi. De personlige innstillingene i
`~/.config/knxscada/ui.json` kan også utelates.

## Ressursbruk

Målt på en x86-PC (Python 3.13) med demotrafikk på ca. 1,3 telegrammer/s:

| | Minne (RSS) | CPU |
|---|---|---|
| Server | ca. 60 MB | ca. 0,2 % i drift. En kort topp ved oppstart mens prosjektet leses. |
| Visualisering, per økt | ca. 75 MB | noen få % opp til ca. 10 % mens skjermen oppdateres |
| Database | ca. 115 byte per telegram | – |

Over SSH sendes bare endringene på skjermen.

Databasen vokser med ca. 10 MB per døgn ved 1 telegram/s. Det blir ca. 0,9 GB over 90 dager.
Senk `retention_days` hvis det er lite diskplass. På en Raspberry Pi er CPU-bruken høyere enn i tabellen,
men den er ikke målt.
