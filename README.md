# Extron Matrix Switcher for Home Assistant

Control and monitor an Extron CrossPoint / MAV Plus matrix switcher, or an
IN1508 scaling presentation switcher, from Home Assistant, with two-way status:
changes made on the unit's front panel, its web page or another controller
appear in Home Assistant straight away, and changes made in Home Assistant are
sent to the unit.

> **Status:** 0.2.0. Written from live probes of three units: an **Extron MAV
> Plus 2424 AV** (firmware 2.03, Ethernet), an **Extron MAV Plus 1616**
> (firmware 1.06, serial port through a serial-to-Ethernet bridge) and an
> **Extron IN1508** (firmware 2.25, serial through a bridge). The integration
> itself has so far been run against simulations of those units, built from the
> probe results. Other CrossPoint and MAV sizes use the same instruction set and
> are expected to work, but have not been tried.

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
| All outputs Input | `select` | The same as the **All outputs** media player, as a drop-down: choose one input (or **None**) for every output, video and audio. Shows **Mixed** when the outputs differ. |
| Mute all video / Mute all audio | `switch` | On when every output is muted; turning it on or off mutes or unmutes them all. |
| Routing summary | `sensor` | "24 of 24 outputs routed"; the attributes list every output's video and audio source, and every input's outputs. |
| Sync clock | `button` | Sets the unit's clock to Home Assistant's local time (configuration category). |
| Time zone | `select` | The unit's offset from GMT, every half hour from -12:00 to +14:00 (configuration category). Choosing one also sets the unit's clock to Home Assistant's local time, because the unit's displayed time moves with the offset. |
| Daylight saving | `select` | Off, North America, Europe or Brazil (configuration category). Choosing one also sets the unit's clock to Home Assistant's local time, because the unit's displayed time moves with the rule. |
| Reset all mutes | `button` | On by default. See [Resets](#resets). |
| Reset all input levels / Reset all output volumes | `button` | Off by default. See [Resets](#resets). |
| Temperature, Fan 1 / 2 speed, voltage rails | `sensor` | Diagnostic. Temperature in °C, fans in rpm. Each unit shows every value it reports and only those (a MAV Plus 1616 reports six, a 2424 reports twelve). |
| Primary / Secondary power supply | `binary_sensor` | Diagnostic. **On means a fault.** Only on units that report them. |
| Unit clock | `sensor` | Diagnostic. The unit's date and time, as a date-time (read as Home Assistant's local time, which is what **Sync clock** writes). |
| Firmware, part number, unit name, IP address, subnet mask, gateway, MAC address, DHCP, time zone offset, daylight saving, open connections, connection timeout, serial port settings, free file space, inputs, outputs, outputs with a source | `sensor` | Diagnostic information, refreshed with each full refresh. |
| Connection | `binary_sensor` | On while the unit is answering; its attributes show the firmware, part number, login level and the latest problem. |

Names come from the unit. Audio and video inputs and outputs can each be given
their own names in Home Assistant (see [Names](#names)).

## Install

1. In HACS choose **Custom repositories**, add this repository's address with
   the category **Integration**, then download **Extron Matrix Switcher**.
2. Restart Home Assistant.
3. **Settings > Devices & services > Add integration > Extron**.

Enter the unit's IP address (port 23). Leave the password empty if the unit has
none. A unit with Ethernet is reached directly. A unit with only a serial port
(the IN1508 is one) is reached through a serial-to-Ethernet bridge: give the
bridge's address and the TCP port it forwards to the unit's serial port (9600
baud, 8 data bits, no parity, 1 stop bit). The integration works out what it has
been given: a matrix switcher (with or without an audio side, with or without a
copyright message on connecting) or an IN1508.

## Security

* The unit's Telnet link is **not encrypted** and the password (if you set one)
  is sent in clear text. Use this only on a network you trust.
* The password is kept in Home Assistant's configuration storage.
* **Read-only** (a tick box when you add the unit, changeable with Reconfigure)
  makes the integration refuse every change at the lowest level, whatever
  entities or automations ask. Controls still show the unit's state; trying to
  use one gives a clear error. Use this for a unit that is in service.
* With an administrator password the integration can change anything the front
  panel can, including presets and the lock. It never sends a whole-switcher or factory
  reset and never changes a password or the network settings. The only resets it
  can send are the three milder ones in [Resets](#resets) (each needs a second
  press) and the one-preset **Clear preset**.

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

The input and output **Group** selects, the **Save / Clear preset** buttons and the
**Reset all input levels** and **Reset all output volumes** buttons are off by default. **Configure > Show advanced controls** turns them all on
together; unticking turns them all off again (including any you had switched on
by hand). Home Assistant reloads the unit about 30 seconds after enabling.

## Resets

Three buttons reset one kind of setting on every input or output at once.
**Reset all mutes** is on by default; the other two are off (see Advanced controls):

* **Reset all mutes** unmutes the video and audio of every output.
* **Reset all input levels** sets every audio input level to 0 dB.
* **Reset all output volumes** sets every output's volume to 64, the top of the
  unit's scale. **Anything audible connected to the outputs could suddenly be
  loud.**

Each button needs a **second press within 10 seconds**; the first press only arms
it and shows a notification saying what will happen. The unit does not announce
these resets, so the integration reads everything again afterwards. A read-only
unit refuses them. Nothing else changes (ties, presets, names, network settings
and passwords are untouched).

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

## IN1508 scaling switcher

An IN1508 (part number 60-569) is controlled over its serial port through a
serial-to-Ethernet bridge (see Install). It gets a device of its own with:

| Entity | Type | What it does |
| -- | -- | -- |
| Input | `select` | Choose input 1 to 8 for the output (video and audio together). The unit takes up to about three seconds to switch. |
| Output resolution / Refresh rate | `select` | The output format. Each resolution allows only some refresh rates (50, 60 or 75 Hz); choosing a resolution keeps the rate where the new resolution allows it, otherwise 60 Hz or the nearest allowed. A rate the resolution refuses gives an error saying which rates it allows. |
| Picture-in-picture | `switch` | Turn picture-in-picture on or off. |
| Picture-in-picture size | `select` | One quarter, one ninth, one sixteenth or one twenty-fifth of the screen, or side by side. |
| Freeze, Video mute, Audio mute | `switch` | Freeze the picture, or mute the video or audio output. |
| Volume | `number` | Output volume, 0 to 100. |
| Colour, Tint, Brightness, Contrast, Sharpness | `number` | Picture controls, 0 to 128, **for the selected input**. A control the unit does not offer for the selected input is unavailable. |
| Input gain | `number` | Audio gain of the selected input, -50 to +9 dB. |
| Aspect ratio | `select` | 4:3 or 16:9 for the selected input (each input keeps its own). Configuration category. |
| Signal standard | `sensor` | The video standard the unit detects (None, NTSC 3.58, NTSC 4.43, PAL or SECAM). Diagnostic. |
| Firmware, part number, Connection | `sensor` / `binary_sensor` | Diagnostic. |

Things worth knowing:

* Colour is offered on inputs 1 to 4 only. The unit refuses **Tint** while the
  input carries a PAL signal, so Tint is unavailable then.
* Mutes and freeze can change by themselves. The video output mutes itself when
  the selected input has no signal, and choosing a different input clears audio
  mute and freeze. The integration reads them again after every input change.
* There are no ties, presets, names or groups on this unit, so none are shown.
  The options screen offers the refresh interval only.
* A serial bridge allows only one connection to the unit at a time, so the
  integration uses a single connection here (the matrix switchers use two).

## How it connects

For a matrix switcher, two Telnet connections are kept open: one for questions and changes, and one
that the unit uses to report anything that changes. A harmless keep-alive is
sent every 45 seconds (the unit drops a silent connection after about five
minutes). If the unit is off or the link drops, or the unit closes a quiet
connection, the entities go unavailable and the integration reconnects by
itself. Ties are read again when the unit announces a front-panel tie or a
preset recall.

## Limitations

* **Front panel lock:** only limits the front panel. Commands from Home
  Assistant still work while it is on.
* **I/O groups** only limit which ties the front panel allows; commands from
  Home Assistant ignore them.
* **Recall** restores the preset's ties only.
* **Volume** is shown on the unit's own 0 to 64 scale (0 to 100 on the IN1508).
* **Clock and time zone:** the unit's clock shows local time, so it moves when
  the time zone changes. Choosing a **Time zone** therefore also sets the clock
  to Home Assistant's local time. Offsets that are not a whole or half hour
  (such as +05:45) are not offered. A unit that has its offset set some other
  way shows no choice selected.
* **Unit clock:** the unit gives no time zone with its clock, so the sensor
  reads it as Home Assistant's local time.
* **Cleared presets:** a unit that is told by another controller to clear a
  preset does not always announce it, so the integration finds out at the next
  full refresh.
* The password prompt and user-level logins have not been tried on a real unit,
  and no matrix switcher other than a MAV Plus 1616 and 2424 has been probed.

## Tests

```
pip install pytest-homeassistant-custom-component
pytest
```

The tests run against a simulated unit (`tests/ha/sim.py`) built from what the
real unit did in the probe runs.

## Licence

MIT (see the `LICENSE` file). Copyright (c) 2026 Joel Goodman.
