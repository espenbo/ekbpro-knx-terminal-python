[English](../en/09-docker.md) · **Norsk**

# Docker (f.eks. på en WAGO PFC200/PFC300)

Programmet kan kjøre i en container på hvilken som helst Linux-maskin med Docker – også på en PLS som WAGO PFC300,
som har Docker men ikke Python. Bildet inneholder bare programmet; alt som hører til *din*
installasjon, ligger i en mappe på verten som monteres som `/data`:

```
/home/knx/                     på verten  =  /data i containeren
  config.yaml                  konfigurasjonen din (lages av init)
  MyHouse.knxproj              ETS-prosjektet
  plan.yaml                    plantegning (valgfri)
  var/                         database, prosjektcache og socket
  .config/knxscada/ui.json     fargetema og språk valgt i TUI-en
```

Serveren kjører som hovedprosessen i containeren. Visualiseringen (TUI) åpnes inne i den samme containeren
med `docker exec`, fra en SSH-økt på verten.

## Krav

- Docker (på WAGO PFC: slå det på i det nettbaserte administrasjonsgrensesnittet, *Configuration → Docker*).
- Nettverkstilgang fra verten til KNX IP-grensesnittet.
- Omtrent 250 MB diskplass til bildet. På PFC-en lagrer Docker dataene sine på den store `/home`-partisjonen.

Testet på en WAGO PFC300 (750-8302, fastvare 04.08.09, aarch64, Docker 25): alle avhengigheter installeres som
ferdigbygde pakker, det trengs ingen kompilator.

## 1. Få programmet over på verten

PFC-en har ikke `git`, men den har `curl` og `tar`:

```sh
cd /home
curl -L https://github.com/espenbo/ekbpro-knx-terminal-python/archive/refs/heads/main.tar.gz | tar xz
cd ekbpro-knx-terminal-python-main
```

## 2. Bygg bildet

```sh
docker build -t ekbpro-knx .
```

Dette laster ned Python-grunnbildet og avhengighetene – noen minutter på en PFC.

For Modbus (RS485 eller TCP), MQTT, OPC UA-serveren eller BACnet/IP legger du til protokollmodulene
([Protokoller og gatewayer](11-protocols-gateways.md)):

```sh
docker build --build-arg MODULES="modbus mqtt" -t ekbpro-knx .
```

En Modbus RTU-linje trenger serieporten i containeren, f.eks. `--device /dev/ttyUSB0` på `docker run`.
BACnet/IP bruker UDP-kringkasting (Who-Is, I-Am), som ikke går gjennom Dockers eget nett – behold `--network host`
(steg 4).

## 3. Lag konfigurasjonen

Kopier ETS-prosjektet til datamappa (for eksempel med `scp` fra PC-en), og la så `init` skrive
`config.yaml` der:

```sh
mkdir -p /home/knx
# fra PC-en:  scp MyHouse.knxproj root@<plc>:/home/knx/
docker run --rm -v /home/knx:/data ekbpro-knx init /data/MyHouse.knxproj --gateway 192.168.1.50 --language nb
cp docker/knx-host.sh /home/knx/            # hjelpeskript som åpner visualiseringen (steg 5)
cp plan.example.yaml /home/knx/plan.yaml    # valgfritt: plantegning å redigere
```

Rediger `/home/knx/config.yaml` med `vi` ved behov (se [Konfigurasjon](04-configuration.md)).

## 4. Start serveren

```sh
docker run -d --name knx \
  --restart unless-stopped \
  --network host \
  --cpus 1 --memory 512m \
  --log-opt max-size=5m --log-opt max-file=2 \
  -e TZ=Europe/Oslo \
  -v /home/knx:/data \
  ekbpro-knx
docker logs -f knx                          # Ctrl+C går ut av loggen, serveren fortsetter å kjøre
```

| Valg | Hvorfor |
|---|---|
| `--restart unless-stopped` | Starter igjen etter omstart eller krasj (Docker selv må starte ved oppstart). |
| `--network host` | KNX-tunnelering bruker UDP og routing bruker multicast – slik virker begge uten portkobling. |
| `--cpus 1 --memory 512m` | På en PLS må styreprogrammet (f.eks. CODESYS) alltid få nok CPU. Serveren trenger langt mindre. |
| `--log-opt …` | Begrenser containerloggen, det sparer flashminnet. |
| `-e TZ=…` | Lokal tid i hendelsesloggen og diagrammene. |

## 5. Åpne visualiseringen

På verten (for eksempel etter `ssh root@<plc>`):

```sh
/home/knx/knx-host.sh                       # visualiseringen
/home/knx/knx-host.sh tui --language en     # på engelsk for denne økta
/home/knx/knx-host.sh info                  # sammendrag av prosjektet
```

Eller rett fra PC-en: `ssh -t root@<plc> /home/knx/knx-host.sh`

Hjelpeskriptet sender SSH-klientadressen og terminaltypen din inn i containeren, så hendelsesloggen viser hvem
som betjente hva (`root@192.168.1.10`). Uten det: `docker exec -it knx knx`.

Eksporten fra busmonitoren (`e` i F4) skrives inne i containeren. Sett `ui.export_dir: /data/export` i
`config.yaml`, så havner filene i `/home/knx/export` på verten.

## Prøv uten installasjon

```sh
docker run --rm -it --network host -v /home/knx:/data --name knx-demo ekbpro-knx server --demo
# i en ny økt:
KNX_CONTAINER=knx-demo /home/knx/knx-host.sh
```

Demoen bruker sin egen database (`var/knxscada-demo.db`) og rører aldri bussen.

## Oppdatering

```sh
cd /home
rm -rf ekbpro-knx-terminal-python-main
curl -L https://github.com/espenbo/ekbpro-knx-terminal-python/archive/refs/heads/main.tar.gz | tar xz
cd ekbpro-knx-terminal-python-main
docker build -t ekbpro-knx .
docker rm -f knx                            # stopper serveren (den avslutter ryddig)
# … så docker run-kommandoen fra steg 4 igjen
docker image prune -f                       # fjerner det gamle bildet
```

Konfigurasjon, database og innstillinger blir liggende i `/home/knx`. Etter at du har endret `.knxproj`, kjører du
`/home/knx/knx-host.sh import` og starter serveren på nytt med `docker restart knx`.

## Merknader for WAGO PFC

- **PLS-bro:** med `--network host` når containeren PLS-ens egen OPC UA-server på
  `opc.tcp://127.0.0.1:4840` – se [PLS-bro](10-plc-opcua.md). Kjør `plc-map` i containeren
  (`/home/knx/knx-host.sh plc-map`); `plc_map.yaml` og `GVL_KNX.st` havner i `/home/knx`.

- **Flashminne:** serveren skriver telegramloggen hele tiden (omtrent 10–20 MB i døgnet ved 1–2
  telegrammer/s, skrevet én gang i sekundet). Hold datamappa på `/home`, og senk `retention_days` i
  `config.yaml` hvis disken blir full.
- **Lesing ved oppstart:** som standard leser serveren tilbakemeldings-, føler- og alarmadressene når den starter,
  aldri styreadresser (`read_on_start: status`, se [Konfigurasjon](04-configuration.md#server)).
- **Én tunnel per server:** kjører programmet også på en annen maskin mot det samme KNX IP-grensesnittet,
  bruker hver server sin egen tunnel, og hver har sin egen logg.
- **Fastvareoppdateringer:** sjekk etterpå at Docker fortsatt er slått på og at containeren kjører
  (`docker ps`). Mangler bildet, bygger du det på nytt (steg 2); dataene i `/home/knx` blir liggende.
- **Sikkerhet:** containeren kjører med vertens nettverk. Alle som kan kjøre `docker` på verten, kan åpne
  visualiseringen og betjene anlegget – på PFC-en er det `root`.

## Sikkerhetskopi

Alt som betyr noe, ligger i `/home/knx`. Databasen kan kopieres mens serveren kjører:

```sh
docker exec knx python -c "import sqlite3; s=sqlite3.connect('/data/var/knxscada.db'); s.backup(sqlite3.connect('/data/var/backup.db'))"
```
