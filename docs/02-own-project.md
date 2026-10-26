# Using your own KNX project

The program is not tied to one particular building. Everything is built from the ETS project file, so anyone who
has the `.knxproj` file of their installation can use it.

## 1. Export the project from ETS

In ETS 5 or 6: **Project overview → select the project → Export**. This gives you a `.knxproj` file
(an ETS 6 `.knxprojarchive` works as well).
If the project is password protected, you need the password (`--password`).

If the installation uses **KNX Secure** (IP Secure or Data Secure), you also need the keyring file:
**Project → Security → Export keyring (.knxkeys)**.

## 2. Create the configuration

```bash
./scada init ~/MyHouse.knxproj --gateway 192.168.1.50
```

| Option | Meaning |
|---|---|
| `--gateway IP` | IP address of the KNX IP interface. Omitted means automatic discovery. |
| `--connection TYPE` | `tunneling` (default when a gateway is given), `tunneling_tcp`, `routing`, `automatic` (default without a gateway), `tunneling_secure`, `routing_secure` |
| `--password PW` | Password of the `.knxproj` |
| `--language LANG` | User interface language: `en` (English, default) or `nb` (Norwegian bokmål). Written to `ui.language` in `config.yaml`. |
| `--force` | Overwrite an existing `config.yaml`. The old one is kept as `config.yaml.bak`. |

`init` does the following:

1. Writes `config.yaml` from the template [`config.example.yaml`](../config.example.yaml).
2. Reads the project. This takes 5–30 seconds depending on its size.
3. Stores the result in `var/project_cache.json`.
4. Uses the project name from ETS as the title.
5. Shows what was found:

```
Project:         My House
Group addresses: 1323 (484 in use)
Cards:           39  (built from ETS functions)
  1 etg: Stue, Kjøkken, Bad, Gang, Trapp
  2 etg: Sov 1, Sov 2, Sov 3, Trapperom
```

Floor and room names come from ETS and are shown exactly as they are in the project, whatever the
user interface language is.

For KNX Secure, add this to `config.yaml`:

```yaml
knx:
  connection: tunneling_secure
  gateway_ip: 192.168.1.50
  keyring_file: MyHouse.knxkeys
  keyring_password: "the password you chose when exporting"
```

## 3. How the visualisation is built

The program uses what it finds in the project, in this order.

### A. ETS functions (best result)

If the project has a **building structure** (building → floor → room) with **functions** in the rooms, the
program uses them directly. Functions are for example "Ceiling light – dimmable light" or
"Room heating – radiator", and they are created in ETS under *Buildings*. Each function becomes a card in the
room view, and the roles in ETS decide which control each group address gets:

| ETS role | Becomes |
|---|---|
| On/Off (SwitchOnOff) + Status (InfoOnOff) | Switch with status feedback |
| Brightness (ActualDimmingValue) | Dimmer (− / + / exact value) |
| Room temperature (TempRoom) | Measured value |
| Setpoint (TempRoomSetpoint) | Setpoint with − / + |
| Operating mode (HVACMode) | Drop-down list: Auto, Comfort, Standby, Night/Eco, Frost protection |
| Window contact (WindowStatus), valve position (ValvePosition) | Status display |

Relative dimming (RelativeSetvalueControl, DPT 3) is skipped. Group addresses without a known role are
classified by data type (DPT), whether they can be written, and their name (see below).

### B. The group address structure (fallback)

If the project has no functions, the rooms are built from the group addresses:

- a **main group** becomes a "floor" in the menu
- a **middle group** becomes a "room"
- **group addresses with the same name**, apart from words like *switching / switch / status / feedback /
  state / value* (and the Norwegian *tilbakemelding / verdi*), become one card

Example: "Stue taklys switching" (1/1/2), "Stue taklys status" (1/4/2) and "Stue taklys value" (1/3/2)
become the card *Stue taklys* with a switch and a dimmer.

`./scada info` shows which method was used ("built from …").

Functions that are not in any room are collected under the floor *Other*, room *No room*.

### C. Room names in the group address names

If the project is structured by *function* ("Actual temp Office 1", "Switching Office 1 ceiling") and has no ETS
functions, set `project.rooms` with a pattern that finds the room in the names. All addresses of a room then end
up in that room. See [Configuration](04-configuration.md#rooms-from-the-group-address-names).

### Tips for the best result in ETS

- **Set the DPT on the group addresses.** Without a DPT the value cannot be interpreted and is shown as raw bytes.
  If the address has no DPT, it is taken from the linked communication objects when possible.
- **Name status addresses with "status"**, "feedback", "state" or "tilbakemelding", and control addresses with
  "switching" or "av/på". Then switch and status are paired.
- **Setpoint addresses** are recognised by "setpoint", "setpunkt", "settpunkt", "skal", "ønsket" or "basis" in the name.
- **The read flag (R)** on the status objects lets the server read the current value at start-up when
  `read_on_start: status` is set (off by default). Otherwise "–" is shown until the first telegram arrives.
- **Placeholders** (names consisting only of characters such as `-`, `_`, `.` or `x`) and addresses without linked
  objects are hidden by default. Press `u` in the group address view to show them.

## 4. Customise

| What | Where |
|---|---|
| Alarm rules | `alarms:` in `config.yaml`. The template has general rules for smoke, water leakage, faults and high temperature. See [Configuration](04-configuration.md#alarms). |
| Title, theme, language, screensaver | `ui:` in `config.yaml` |
| Status tiles (outdoor, power, energy) | Found automatically by name. Override with `ui.outdoor_ga`, `ui.power_ga`, `ui.energy_ga`. |
| Floor plan | `plan.yaml`. See [Floor plan](06-floor-plan.md). |

## 5. When the project changes in ETS

Export the `.knxproj` again and replace the old file. The server and the visualisation detect that the file
is newer than the cache and re-read it at the next start. To do it immediately:

```bash
./scada import
sudo systemctl restart knxscada   # or stop and start the server
```

## Several installations on the same machine

(One site that consists of several ETS projects – e.g. a main building and an annex – is configured in *one*
installation with `project.files`: see [Configuration](04-configuration.md#several-ets-projects-in-one-site).)

Create one directory per installation, e.g. under `sites/` (ignored by git); relative paths in its
`config.yaml` – socket, database, project – are resolved from that directory, so they never collide. To run
them as services, see [Several installations as services](05-operation-and-ssh.md#several-installations-as-services).

```bash
mkdir -p sites/cabin
./scada -c sites/cabin/config.yaml init ~/Cabin.knxproj --gateway 10.0.0.20
./scada -c sites/cabin/config.yaml server
./scada -c sites/cabin/config.yaml
```

Each installation gets its own `sites/<name>/var/` with socket, database and project cache.
