# Floor plan

The floor plan (F8) is drawn from `plan.yaml` in the program directory. Choose another file with `ui.plan` in
`config.yaml`. The file is re-read every time you open F8, so changes are shown without a restart.

`plan.yaml` describes your own building and is not part of the repository. Start from the example:

```bash
cp plan.example.yaml plan.yaml
```

Without a `plan.yaml`, F8 shows a message instead of a drawing.

## Format

Dimensions in millimetres, with the origin in the top-left corner as on a paper drawing.

```yaml
floors:
  - name: "1 etg"          # shown as a tab
    width: 8000            # outer dimensions
    depth: 7300
    rooms:
      - {label: "Kjøkken", x: 200,  y: 240,  w: 2420, h: 3040, room: "Kjøkken"}
      - {label: "Stue",    x: 200,  y: 3470, w: 7540, h: 3630, room: "Stue"}
  - name: "U etg. Kjeller"
    width: 8000
    depth: 7300
    rooms:
      - {label: "Krypkjeller", x: 4000, y: 240, w: 3740, h: 6860}       # no KNX
```

| Field | Meaning |
|---|---|
| `name` | Floor name, shown as a tab. Floors are shown in the order they are listed. |
| `width`, `depth` | Outer dimensions of the floor |
| `label` | Text in the room |
| `x`, `y` | Top-left corner (inside) |
| `w`, `h` | Width and depth |
| `room` | Name of the room in ETS. Links temperature, lights, heating and alarms to the room. Omit for rooms without KNX. |

The ETS room names are listed in the menu under F2 and in the output of `./scada info`. Upper and lower case
do not matter. Labels and floor names are your own text and are shown as written, in any language.

**Tips:**

- Draw the rooms with a small gap, for example 150 mm for a wall. That makes the walls clearly visible.
- The drawing is scaled to the terminal and takes into account that a character is about twice as tall as it is wide.
  In small windows, narrow rooms (less than about 3 characters) become just a line.
- Rooms that are not linked to ETS are shown without status. Clicking one shows a message telling you to add `room:`.
  A `room:` that does not match any ETS room is shown as "(unknown ETS room …)" inside the room.

## Export to CAD

```bash
./scada plan-dxf plan.dxf
```

This creates a DXF file (AutoCAD R12) with the floors side by side. It has the layers `OUTER_WALL` (floor outline),
`ROOM` (room rectangles) and `TEXT` (floor names, room labels and areas). The file opens in LibreCAD, QCAD,
FreeCAD, DraftSight, AutoCAD and most other CAD programs. Without a file name, `plan.dxf` is written.

The file is meant for viewing and further drawing in CAD. `plan.yaml` is still the source for the terminal view,
and there is no import from DXF.
