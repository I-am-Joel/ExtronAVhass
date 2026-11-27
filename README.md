# Extron Matrix Switcher for Home Assistant

Control and monitor an Extron AV devices including CrossPoint / MAV Plus matrix switchers and presentation scalers from Home Assistant, with two-way status:
changes made in the integration or on a device's front panel, its web page or another controller,
are synchronised in real time.

> **Status:** 0.2.0. Written from live probes and real-world tests of three units: an **Extron MAV
> Plus 2424 AV** (firmware 2.03, Ethernet), an **Extron MAV Plus 1616**
> (firmware 1.06, serial port through an Ethernet to serial bridge) and an
> **Extron IN1508** (firmware 2.25, serial through a bridge). Other CrossPoint and MAV sizes use the same instruction set and
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

Enter the unit's IP address if the device is connected to your network via Ethernet (the default port 23). Leave the password empty if the unit has
none. If you're connecting a device via an Ethernet to serial bridge then enter the
bridge's IP and the TCP port it forwards to the unit's serial port. For those units tested, the communication configuration for the bridge is 9600
baud, 8 data bits, no parity, 1 stop bit - but check your own device to be certain.

The integration will then work out what you've connected, eg a matrix switcher or a presentation scaler, what breakout features are available and what controls can be exposed.

## Security

* The unit's Telnet link is **not encrypted** and the password (if you set one)
  is sent in clear text. Use this only on a network you trust.
* The password is kept in Home Assistant's configuration storage.
* **Read-only** (a tick box when you add the unit, changeable with Reconfigure)
  makes the integration refuse every change at the lowest level, whatever
  entities or automations ask. Controls still show the unit's state; trying to
  use one gives a clear error.
* With full access the integration can change anything the front
  panel can, including presets and the lock. Excluded from this are factory reset, password control and some reset functions.

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
by hand).

## Resets

Three buttons reset one kind of setting on every input or output at once.
**Reset all mutes** is enabled by default; the other two are disabled (as per "Advanced controls"):

* **Reset all mutes** unmutes the video and audio of every output.
* **Reset all input levels** sets every audio input level to 0 dB.
* **Reset all output volumes** sets every output's volume to 64, the top of the
  unit's scale.

Each reset button needs to be double pressed. Extron devices don't announce
these resets, so the integration rereads the device afterwards. A read-only
unit refuses them.

## Presets

Presets store **ties only** (not volume, levels or mutes), matching front panel behaviour. 
If the preset is blank, Save acts without challenge; if it already holds something, the first press only "arms"
the button (a notification appears and the button's `armed` attribute turns
on), and a second press within 10 seconds goes ahead.

## Names

Open the integration and choose **Configure** to edit four lists, one name per
line: video input names, audio input names, video output names, audio output
names. A blank line keeps the default (the unit's name, with "Video" or
"Audio" appended). Names only change what Home Assistant shows; nothing is written
to the device. The same screen sets the full-refresh interval (60 seconds by
default; the unit reports changes by itself - full refreshes serve as a safety net).

If you rename an input on the device itself, reload the integration to pick up
the new name.

## Presentation scaler (tested on an IN1508)

Presentation scalers show entities relevant to their functionality:

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

From testing:

* Colour is offered on inputs 1 to 4 only. The unit refuses **Tint** while the
  input carries a PAL signal.
* Mutes and freeze are dynamically changed by the device. The video output mutes itself when
  the selected input has no signal and choosing a different input clears audio
  mute and freeze. The integration reads them again after every input change.

## Limitations

* **Front panel lock:** only limits the device's front panel. Commands from Home
  Assistant still work while it is on.
* **I/O groups** only limit which ties the front panel allows; commands from
  Home Assistant ignore them.
* **Recall** restores the preset's ties only.
* **Volume** is shown on the unit's own 0 to 64 scale (eg 0 to 100 on the IN1508).
* **Clock and time zone:** the device's clock shows local time, so it moves when
  the time zone changes. Choosing a **Time zone** therefore also sets the clock
  to Home Assistant's local time. Offsets that are not a whole or half hour
  (such as +05:45) are not offered. This is a limit of the device not the integration. A device that has its offset set some other
  way shows no choice selected.
* **Unit clock:** the unit gives no time zone with its clock, so the sensor
  reads it as Home Assistant's local time.
* **Cleared presets:** a unit that is told by another controller to clear a
  preset does not always announce it, so the integration finds out at the next
  full refresh (adjustable in Configuration, set to 60 seconds by default).
* The password prompt and user-level logins have not been tested on a real unit.

## Licence

MIT (see the `LICENSE` file). Copyright (c) 2026 Joel Goodman.
