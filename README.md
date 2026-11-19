# Extron Matrix Switcher for Home Assistant

Control and monitor an Extron CrossPoint / MAV Plus matrix switcher from Home
Assistant, with two-way status: changes made on the unit's front panel, its
web page or another controller appear in Home Assistant straight away, and
changes made in Home Assistant are sent to the unit.

> **Status:** 0.1.1. Written from live probes of one unit, an **Extron MAV Plus
> 2424 AV** (firmware 2.03), and tested against a simulation of it. 0.1.0 ran on
> that unit; the 0.1.1 additions have only been tested against the simulation.
> Other CrossPoint and MAV sizes use the same instruction set and are expected
> to work, but have not been tried.

## What you get

For each switcher you add, one device with:

| Entity | Type | What it does |
| -- | -- | -- |
| *Output* Input | `select` | Choose one input for the output's **video and audio together**. Shows **Mixed** when they differ. |
| *Output* Video Input / Audio Input | `select` | Choose the input for the output's video, or its audio, on its own. **None** unties it. |
| *Output* Video Mute / Audio Mute | `switch` | Mute or unmute that output's video or audio. |
| *Output* Audio Volume | `number` | Output volume, 0 to 64 (the unit's own scale). |
| *Input* Audio Level | `number` | Input audio level, -18 to +24 dB. |
| Preset 1 ... 32 | `sensor` | **saved** or **empty**; the preset's name is an attribute. |
| Recall preset 1 ... 32 | `button` | Recall a saved preset (see [Limitations](#limitations)). |
| Save preset / Clear preset | `button` | Off by default. See [Presets](#presets). |
| Front panel lock | `select` | Off, Mode 1 or Mode 2 (diagnostic category). |
| *Input* / *Output* Group | `select` | Off by default. The unit's I/O group (see [Limitations](#limitations)). |
| All outputs | `media_player` | Choose one input (or **None**) for **every** output at once, video and audio. Shows **Mixed** when the outputs differ. |
| Mute all video / Mute all audio | `switch` | On when every output is muted; turning it on or off mutes or unmutes them all. |
| Routing summary | `sensor` | "24 of 24 outputs routed"; the attributes list every output's video and audio source, and every input's outputs. |
| Sync clock | `button` | Sets the unit's clock to Home Assistant's local time (configuration category). |
| Temperature, Fan 1 / 2 speed, voltage rails | `sensor` | Diagnostic. Temperature in °C, fans in rpm. |
| Primary / Secondary power supply | `binary_sensor` | Diagnostic. **On means a fault.** |
| Firmware, part number, unit name, IP address, subnet mask, gateway, MAC address, DHCP, unit clock, time zone offset, daylight saving, open connections, connection timeout, serial port settings, free file space, inputs, outputs, outputs with a source | `sensor` | Diagnostic information, refreshed with each full refresh. |
| Connection | `binary_sensor` | On while the unit is answering; its attributes show the firmware, part number, login level and the latest problem. |

Names come from the unit. Audio and video inputs and outputs can each be given
their own names in Home Assistant (see [Names](#names)).

## Install

1. In HACS choose **Custom repositories**, add this repository's address with
   the category **Integration**, then download **Extron Matrix Switcher**.
2. Restart Home Assistant.
3. **Settings > Devices & services > Add integration > Extron**.

Enter the unit's IP address (port 23). Leave the password empty if the unit has
none. The unit must have Ethernet; the front-panel RS-232 jack is not used.

## Security

* The unit's Telnet link is **not encrypted** and the password (if you set one)
  is sent in clear text. Use this only on a network you trust.
* The password is kept in Home Assistant's configuration storage.
* **Read-only** (a tick box when you add the unit, changeable with Reconfigure)
  makes the integration refuse every change at the lowest level, whatever
  entities or automations ask. Controls still show the unit's state; trying to
  use one gives a clear error. Use this for a unit that is in service.
* With an administrator password the integration can change anything the front
  panel can, including presets and the lock. It never sends a reset command and
  never changes a password or the network settings.

## Route action

`extron.route` ties one input to several named outputs in a single command.
Target the unit's **All outputs** media player and give:

```yaml
action: extron.route
target:
  entity_id: media_player.<your_unit>_all_outputs
data:
  input: Blu-ray          # an input name or number, or None
  outputs:                # output names or numbers
    - Lounge
    - Kitchen
  mode: both              # both (default), video or audio
```

Names are not case sensitive. A name that is not recognised gives an error and
nothing is changed. A read-only unit refuses it.

## Advanced controls

The input and output **Group** selects and the **Save / Clear preset** buttons
are off by default. **Configure > Show advanced controls** turns them all on
together; unticking turns them all off again (including any you had switched on
by hand). Home Assistant reloads the unit about 30 seconds after enabling.

## Presets

Presets store **ties only** (not volume, levels or mutes), exactly as on the
front panel. The unit has no "are you sure" of its own over the network, so the
**Save preset** and **Clear preset** buttons add one: if the preset is empty,
Save acts at once; if it already holds something, the first press only "arms"
the button (a notification appears and the button's `armed` attribute turns
on), and a second press within 10 seconds goes ahead. The wait is
a safety measure and resets itself.

## Names

Open the integration and choose **Configure** to edit four lists, one name per
line: video input names, audio input names, video output names, audio output
names. A blank line keeps the default (the unit's name, with " Video" or
" Audio" added). Names only change what Home Assistant shows; nothing is written
to the unit. The same screen sets the full-refresh interval (60 seconds by
default; the unit reports changes by itself, so this is only a safety net).

If you rename an input on the unit itself, reload the integration to pick up
the new name.

## How it connects

Two Telnet connections are kept open: one for questions and changes, and one
that the unit uses to report anything that changes. A harmless keep-alive is
sent every 45 seconds (the unit drops a silent connection after about five
minutes). If the unit is off or the link drops, the entities go unavailable and
the integration reconnects by itself.

## Limitations

* **Front panel lock:** only limits the front panel. Commands from Home
  Assistant still work while it is on.
* **I/O groups** only limit which ties the front panel allows; commands from
  Home Assistant ignore them.
* **Recall** restores the preset's ties only.
* **Volume** is shown on the unit's own 0 to 64 scale.
* **Clock:** the unit's time-zone offset is not changed. Sync clock writes local
  time, which is what the unit then displays.
* **Diagnostics on smaller units:** units up to the 1616 reply to the status
  command in a different, shorter form that is not understood yet, so they get
  no temperature, fan, power or voltage sensors until it is probed.
* The password prompt, user-level logins and sizes other than 24 x 24 have not
  been tried on a real unit.

## Tests

```
pip install pytest-homeassistant-custom-component
pytest
```

The tests run against a simulated unit (`tests/ha/sim.py`) built from what the
real unit did in the probe runs.
