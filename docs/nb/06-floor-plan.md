[English](../en/06-floor-plan.md) · **Norsk**

# Plantegning

Plantegningen (F8) tegnes fra `plan.yaml` i programmappen. Velg en annen fil med `ui.plan` i
`config.yaml`. Fila leses på nytt hver gang du åpner F8, så endringer vises uten omstart.

`plan.yaml` beskriver ditt eget bygg og er ikke en del av repoet. Start fra eksempelet:

```bash
cp plan.example.yaml plan.yaml
```

Uten `plan.yaml` viser F8 en melding i stedet for en tegning.

## Format

Mål i millimeter, med origo i øvre venstre hjørne som på en papirtegning.

```yaml
floors:
  - name: "1 etg"          # vises som fane
    width: 8000            # yttermål
    depth: 7300
    rooms:
      - {label: "Kjøkken", x: 200,  y: 240,  w: 2420, h: 3040, room: "Kjøkken"}
      - {label: "Stue",    x: 200,  y: 3470, w: 7540, h: 3630, room: "Stue"}
  - name: "U etg. Kjeller"
    width: 8000
    depth: 7300
    rooms:
      - {label: "Krypkjeller", x: 4000, y: 240, w: 3740, h: 6860}       # ikke KNX
```

| Felt | Betydning |
|---|---|
| `name` | Navnet på etasjen, vises som fane. Etasjene vises i den rekkefølgen de står i lista. |
| `width`, `depth` | Yttermålene til etasjen (standard 8000 hver) |
| `label` | Tekst i rommet (standard tom) |
| `x`, `y` | Øvre venstre hjørne (innvendig). **Påkrevd** – et rom uten dem stopper F8 og `plan-dxf` med en feil. |
| `w`, `h` | Bredde og dybde. **Påkrevd**, som `x` og `y`. |
| `room` | Navnet på rommet i ETS. Kobler temperatur, lys, varme og alarmer til rommet. Utelat for rom uten KNX. |

ETS-romnavnene står i menyen under F2 og i utskriften fra `./scada info`. Store og små bokstaver
spiller ingen rolle. Etiketter og etasjenavn er din egen tekst og vises som de er skrevet, på hvilket som helst språk.

**Tips:**

- Tegn rommene med en liten avstand, for eksempel 150 mm for en vegg. Da blir veggene godt synlige.
- Tegningen skaleres til terminalen og tar hensyn til at et tegn er omtrent dobbelt så høyt som det er bredt.
  I små vinduer blir smale rom (under ca. 3 tegn) bare en strek.
- Rom som ikke er koblet til ETS, vises uten status. Klikker du på et slikt rom, får du en melding om å legge til `room:`.
  Et `room:` som ikke passer med noe ETS-rom, vises som «(ukjent ETS-rom …)» inne i rommet.

## Eksport til CAD

```bash
./scada plan-dxf plan.dxf
```

Filnavnet er relativt til mappen du kjører kommandoen fra (standard `plan.dxf`).

Dette lager en DXF-fil (AutoCAD R12) med etasjene ved siden av hverandre. Den har lagene `OUTER_WALL` (omrisset av etasjen),
`ROOM` (romrektangler) og `TEXT` (etasjenavn, rometiketter og arealer). Fila kan åpnes i LibreCAD, QCAD,
FreeCAD, DraftSight, AutoCAD og de fleste andre CAD-programmer. Uten filnavn skrives `plan.dxf`.

Fila er ment for visning og videre tegning i CAD. `plan.yaml` er fortsatt kilden til visningen i terminalen,
og det finnes ingen import fra DXF.
