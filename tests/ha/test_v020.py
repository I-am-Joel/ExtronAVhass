"""0.2.0 features: unit clock sensor, six or twelve status values, serial units, video-only units,
the time zone in a form every unit answers, the IN1508, and noticing changes made elsewhere."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extron.const import DOMAIN
from custom_components.extron.in1508 import (
    VALID_RATES,
    clamp_rate,
    parse_in_status,
)
from custom_components.extron.protocol import (
    ExtronClient,
    async_probe,
    format_offset,
    normalise_offset,
    parse_audio_size,
    parse_clock,
    parse_status,
)

from .sim import Sim
from .sim1508 import Sim1508
from .test_integration import call, entity_id, finish, setup, state_of

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


# -- pure helpers --------------------------------------------------------------------------------


def test_status_with_six_or_twelve_values() -> None:
    """The 1616 reports six values and the 2424 twelve; each shows only what it reports."""
    six = parse_status("3.27 5.07 -5.07 14.95 -14.97 98.60")
    assert six == {
        "v3_3": 3.27,
        "v5": 5.07,
        "v_m5": -5.07,
        "v15": 14.95,
        "v_m15": -14.97,
        "temp_f": 98.6,
    }
    twelve = parse_status("5.06 3.32 2.51 11.15 -5.04 -11.98 11.52 69.80 1081 1081 1 1")
    assert len(twelve) == 12 and twelve["fan2"] == 1081
    assert parse_status("1 2 3") == {}
    assert parse_status("E13") == {}


def test_clock_and_zone_readings() -> None:
    """The clock reading and the zone readings in every form the units use."""
    zone = dt_util.get_default_time_zone()
    when = parse_clock("Fri, 09 Oct 2020 08:05:04", zone)
    assert when == datetime(2020, 10, 9, 8, 5, 4, tzinfo=zone)
    assert parse_clock("nonsense", zone) is None
    assert parse_clock("Fri, 31 Foo 2020 08:05:04", zone) is None
    assert normalise_offset("00:00") == "+00:00"  # zero is read with no sign
    assert normalise_offset("0.00") == "+00:00"
    assert normalise_offset("-05:30") == "-05:30"
    assert normalise_offset("+14:00") == "+14:00"
    assert format_offset(330) == "5.30" and format_offset(-330) == "-5.30"
    assert parse_audio_size("V16X16 A16X16") == (16, 16)
    assert parse_audio_size("V16X16 A0X0") == (0, 0)


def test_scaler_helpers() -> None:
    """Status line and rate rules of the IN1508."""
    assert parse_in_status("Chn1 Pch0 Std0") == (1, 0, 0)
    assert parse_in_status("V16X16 A16X16") is None
    assert clamp_rate(3, 3) == 3
    assert clamp_rate(15, 3) == 2  # 1080p allows only 60 Hz
    assert clamp_rate(12, 3) == 1  # 576p allows only 50 Hz
    assert all(rates for rates in VALID_RATES.values())


# -- the matrix switchers -----------------------------------------------------------------------------


async def test_serial_unit_without_a_banner(hass: HomeAssistant) -> None:
    """A unit on a serial bridge gives no copyright message and a six-value status."""
    sim = Sim(16, banner=False, short_status=True)
    entry = await setup(hass, sim, inputs=16, outputs=16)
    client = entry.runtime_data.client
    assert client.banner == {"part_number": "60-472-01", "firmware": "2.03"}
    assert state_of(hass, entry, "sensor", "diag_v5").state == "5.07"
    assert state_of(hass, entry, "sensor", "diag_v15").state == "14.95"
    assert state_of(hass, entry, "sensor", "diag_firmware").state == "2.03"
    registry = er.async_get(hass)
    assert not registry.async_get_entity_id(  # only what the unit reports
        "sensor", DOMAIN, f"{entry.entry_id}_diag_fan1"
    )
    assert not registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_diag_psu1"
    )
    await finish(hass, entry, sim)


async def test_probe_reads_a_serial_unit_and_a_video_only_unit() -> None:
    """The setup screens find the part number without a banner, and notice a missing audio side."""
    sim = Sim(16, banner=False)
    port = await sim.start()
    info = await async_probe("127.0.0.1", port)
    assert (info["inputs"], info["outputs"], info["audio"]) == (16, 16, True)
    assert info["part_number"] == "60-472-01" and info["firmware"] == "2.03"
    await sim.stop()

    video_only = Sim(8, audio=False)
    port = await video_only.start()
    info = await async_probe("127.0.0.1", port)
    assert info["audio"] is False and info["outputs"] == 8
    await video_only.stop()


async def test_video_only_unit_has_no_audio_entities(hass: HomeAssistant) -> None:
    """Without an audio side there are no audio selects, mutes, volumes or levels."""
    sim = Sim(8, audio=False)
    entry = await setup(hass, sim, inputs=8, outputs=8, audio=False)
    registry = er.async_get(hass)
    ids = {e.unique_id.removeprefix(f"{entry.entry_id}_") for e in registry.entities.values()}
    assert "out_1_video" in ids and "out_1_video_mute" in ids
    assert not {i for i in ids if "audio" in i and "diag" not in i}
    assert not {i for i in ids if i.endswith(("_volume", "_level")) or (i.startswith("out_") and i.endswith("_link"))}
    assert "reset_input_levels" not in ids and "reset_mutes" in ids
    # A tie from the media player goes out as video only.
    player = entity_id(hass, entry, "media_player", "all_outputs")
    await call(hass, "media_player", "select_source", entity_id=player, source="Input #03")
    assert sim.video[1] == 3 and "3*%" in sim.log
    await finish(hass, entry, sim)


async def test_unit_clock_is_a_timestamp(hass: HomeAssistant) -> None:
    """The clock sensor is a date-time, read as Home Assistant's local time."""
    sim = Sim()
    sim.clock = "Thu, 19 Nov 2026 16:23:07"
    entry = await setup(hass, sim)
    state = state_of(hass, entry, "sensor", "diag_clock")
    assert state.attributes["device_class"] == "timestamp"
    expected = datetime(2026, 11, 19, 16, 23, 7, tzinfo=dt_util.get_default_time_zone())
    assert dt_util.parse_datetime(state.state) == expected.astimezone(UTC)
    await finish(hass, entry, sim)


async def test_time_zone_is_sent_in_the_prompt_form(hass: HomeAssistant) -> None:
    """No plus sign is sent, so no unit holds its reply back until the next command."""
    sim = Sim()
    entry = await setup(hass, sim)
    zone = entity_id(hass, entry, "select", "time_zone")
    await call(hass, "select", "select_option", entity_id=zone, option="GMT+01:00")
    assert "ESC1.00CZ" in sim.log
    assert sim.late_zone_reply is None
    assert hass.states.get(zone).state == "GMT+01:00"
    await call(hass, "select", "select_option", entity_id=zone, option="GMT+00:00")
    assert "ESC0.00CZ" in sim.log
    assert hass.states.get(zone).state == "GMT+00:00"
    await call(hass, "select", "select_option", entity_id=zone, option="GMT-05:30")
    assert "ESC-5.30CZ" in sim.log
    await finish(hass, entry, sim)


async def test_daylight_saving_change_sets_the_clock(hass: HomeAssistant) -> None:
    """Changing the rule moves the unit's displayed time, so the clock is set again."""
    sim = Sim()
    entry = await setup(hass, sim)
    dst = entity_id(hass, entry, "select", "daylight_saving")
    before = len(sim.writes())
    await call(hass, "select", "select_option", entity_id=dst, option="Europe")
    new = sim.writes()[before:]
    assert new[0] == "ESC2CX" and new[1].endswith("CT")
    await finish(hass, entry, sim)


async def test_reset_all_mutes_is_on_by_default(hass: HomeAssistant) -> None:
    """Reset all mutes is available without the advanced option; the other resets are not."""
    sim = Sim()
    entry = await setup(hass, sim)
    registry = er.async_get(hass)
    mutes = registry.async_get(entity_id(hass, entry, "button", "reset_mutes"))
    assert mutes.disabled_by is None
    levels = registry.async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_reset_input_levels"
    )
    assert registry.async_get(levels).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    await finish(hass, entry, sim)


async def test_reset_all_mutes_is_switched_on_for_an_existing_install(
    hass: HomeAssistant,
) -> None:
    """A button the integration had left off is switched on; one the user turned off stays off."""
    sim = Sim()
    port = await sim.start()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Extron",
        unique_id="u",
        data={
            "host": "127.0.0.1",
            "port": port,
            "inputs": 24,
            "outputs": 24,
            "model_name": "x",
        },
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "button",
        DOMAIN,
        f"{entry.entry_id}_reset_mutes",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    kept = registry.async_get_or_create(
        "button",
        DOMAIN,
        f"{entry.entry_id}_reset_input_levels",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(entity_id(hass, entry, "button", "reset_mutes")).disabled_by is None
    assert registry.async_get(kept.entity_id).disabled_by is er.RegistryEntryDisabler.USER
    await finish(hass, entry, sim)


async def test_cleared_preset_is_noticed_without_an_announcement() -> None:
    """The 2424 does not announce a cleared preset, so the periodic read finds it."""
    sim = Sim()
    port = await sim.start()
    client = ExtronClient(
        "127.0.0.1", port, 24, 24, refresh_interval=0.5, reconnect_delay=0.2
    )
    await client.async_start()
    try:
        assert await client.async_wait_ready(10)
        await client.async_save_preset(7)
        assert client.presets[7].saved
        sim.presets.pop(7)  # cleared by another controller; nothing is announced
        for _ in range(60):
            if not client.presets[7].saved:
                break
            await asyncio.sleep(0.1)
        assert not client.presets[7].saved
    finally:
        await client.async_stop()
        await sim.stop()


async def test_reconnects_after_the_unit_closes_the_connection() -> None:
    """A unit that hangs up (it does after about five quiet minutes) is reconnected to at once."""
    sim = Sim()
    port = await sim.start()
    client = ExtronClient("127.0.0.1", port, 24, 24, reconnect_delay=0.2)
    await client.async_start()
    try:
        assert await client.async_wait_ready(10)
        sim.drop_all()
        for _ in range(100):
            if not client.available:
                break
            await asyncio.sleep(0.05)
        for _ in range(100):
            if client.available:
                break
            await asyncio.sleep(0.1)
        assert client.available
        await client.async_set_volume(1, 9)
        assert sim.volume[1] == 9
    finally:
        await client.async_stop()
        await sim.stop()


# -- the IN1508 ------------------------------------------------------------------------------------------


async def setup_scaler(hass: HomeAssistant, sim: Sim1508, **extra) -> MockConfigEntry:
    """Set up the integration against the simulated IN1508."""
    port = await sim.start()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test IN1508",
        unique_id="in1508-test",
        data={
            "host": "127.0.0.1",
            "port": port,
            "kind": "in1508",
            "model_name": "IN1508 (60-569-01)",
            "read_only": False,
            **extra,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def finish_scaler(hass: HomeAssistant, entry, sim: Sim1508) -> None:
    """Unload and stop."""
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_probe_recognises_an_in1508() -> None:
    """The setup screens recognise the IN1508 by its answer and part number."""
    sim = Sim1508()
    port = await sim.start()
    info = await async_probe("127.0.0.1", port)
    assert info["kind"] == "in1508" and info["part_number"] == "60-569-01"
    assert info["firmware"] == "2.25" and info["inputs"] == 8
    await sim.stop()


async def test_in1508_entities_show_the_unit(hass: HomeAssistant) -> None:
    """Input, output format, mutes, volume and the selected input's picture controls."""
    sim = Sim1508()
    sim.res, sim.rate = 15, 2
    entry = await setup_scaler(hass, sim)
    assert state_of(hass, entry, "select", "input").state == "Input 1"
    assert state_of(hass, entry, "select", "resolution").state == "1080p"
    assert state_of(hass, entry, "select", "rate").state == "60 Hz"
    assert state_of(hass, entry, "select", "pip_size").state == "1/4 screen"
    assert state_of(hass, entry, "switch", "video_mute").state == "on"
    assert state_of(hass, entry, "switch", "audio_mute").state == "off"
    assert state_of(hass, entry, "switch", "freeze").state == "off"
    assert state_of(hass, entry, "switch", "pip").state == "off"
    assert float(state_of(hass, entry, "number", "volume").state) == 100
    assert float(state_of(hass, entry, "number", "picture_colour").state) == 64
    assert float(state_of(hass, entry, "number", "picture_contrast").state) == 59
    assert state_of(hass, entry, "number", "picture_tint").state == "unavailable"
    assert state_of(hass, entry, "sensor", "diag_firmware").state == "2.25"
    assert state_of(hass, entry, "binary_sensor", "connection").state == "on"
    await finish_scaler(hass, entry, sim)


async def test_in1508_input_change_takes_its_time(hass: HomeAssistant) -> None:
    """Selecting an input waits for the unit, then shows the mutes the unit set by itself."""
    sim = Sim1508(select_delay=0.4)
    entry = await setup_scaler(hass, sim)
    select = entity_id(hass, entry, "select", "input")
    await call(hass, "select", "select_option", entity_id=select, option="Input 3")
    assert sim.input == 3
    assert hass.states.get(select).state == "Input 3"
    assert state_of(hass, entry, "switch", "video_mute").state == "off"  # a signal on input 3
    # Colour is not offered on input 5, and the picture controls follow the selected input.
    await call(hass, "select", "select_option", entity_id=select, option="Input 5")
    assert state_of(hass, entry, "number", "picture_colour").state == "unavailable"
    assert float(state_of(hass, entry, "number", "picture_contrast").state) == 45
    assert state_of(hass, entry, "switch", "video_mute").state == "on"
    await finish_scaler(hass, entry, sim)


async def test_in1508_changes_reach_the_unit(hass: HomeAssistant) -> None:
    """Mutes, freeze, PIP, volume, picture controls, gain and the output format."""
    sim = Sim1508()
    entry = await setup_scaler(hass, sim)
    await call(hass, "switch", "turn_off", entity_id=entity_id(hass, entry, "switch", "video_mute"))
    assert sim.vmute == 0
    await call(hass, "switch", "turn_on", entity_id=entity_id(hass, entry, "switch", "audio_mute"))
    assert sim.amute == 1
    await call(hass, "switch", "turn_on", entity_id=entity_id(hass, entry, "switch", "freeze"))
    assert sim.freeze == 1
    await call(hass, "switch", "turn_on", entity_id=entity_id(hass, entry, "switch", "pip"))
    assert sim.pip_on == 1
    await call(
        hass, "number", "set_value",
        entity_id=entity_id(hass, entry, "number", "volume"), value=40,
    )
    assert sim.volume == 40
    await call(
        hass, "number", "set_value",
        entity_id=entity_id(hass, entry, "number", "picture_brightness"), value=80,
    )
    assert sim.picture[1]["Y"] == 80
    gain = entity_id(hass, entry, "number", "gain")
    await call(hass, "number", "set_value", entity_id=gain, value=-20)
    assert sim.picture[1]["G"] == -20 and "20g" in sim.log
    await call(hass, "number", "set_value", entity_id=gain, value=9)
    assert sim.picture[1]["G"] == 9 and "9G" in sim.log
    with pytest.raises(ServiceValidationError):
        await call(hass, "number", "set_value", entity_id=gain, value=-60)
    await call(
        hass, "select", "select_option",
        entity_id=entity_id(hass, entry, "select", "aspect"), option="16:9",
    )
    assert sim.picture[1]["9"] == 1
    await call(
        hass, "select", "select_option",
        entity_id=entity_id(hass, entry, "select", "pip_size"), option="1/16 screen",
    )
    assert sim.pip_size == 2
    await finish_scaler(hass, entry, sim)


async def test_in1508_output_format_rules(hass: HomeAssistant) -> None:
    """A resolution keeps the rate if it can; a rate the resolution refuses is explained."""
    sim = Sim1508()
    entry = await setup_scaler(hass, sim)
    res = entity_id(hass, entry, "select", "resolution")
    rate = entity_id(hass, entry, "select", "rate")
    await call(hass, "select", "select_option", entity_id=rate, option="75 Hz")
    assert (sim.res, sim.rate) == (3, 3)
    await call(hass, "select", "select_option", entity_id=res, option="1080p")
    assert (sim.res, sim.rate) == (15, 2)  # 1080p allows only 60 Hz
    with pytest.raises(ServiceValidationError, match="only available at 60 Hz"):
        await call(hass, "select", "select_option", entity_id=rate, option="50 Hz")
    assert hass.states.get(rate).attributes["allowed_rates"] == ["60 Hz"]
    await finish_scaler(hass, entry, sim)


async def test_in1508_front_panel_changes_appear(hass: HomeAssistant) -> None:
    """The unit's announcements update the entities without being asked."""
    sim = Sim1508(select_delay=0.0)
    entry = await setup_scaler(hass, sim)
    sim.panel_input(3)
    sim.panel_brightness(70)
    await asyncio.sleep(1.5)
    await hass.async_block_till_done()
    assert state_of(hass, entry, "select", "input").state == "Input 3"
    assert float(state_of(hass, entry, "number", "picture_brightness").state) == 70
    assert state_of(hass, entry, "switch", "video_mute").state == "off"
    await finish_scaler(hass, entry, sim)


async def test_in1508_read_only_and_reconnect(hass: HomeAssistant) -> None:
    """Read-only refuses changes; a dropped connection comes back by itself."""
    sim = Sim1508()
    entry = await setup_scaler(hass, sim)
    client = entry.runtime_data.client
    sim.drop_all()
    for _ in range(100):
        if not client.available:
            break
        await asyncio.sleep(0.05)
    for _ in range(150):
        if client.available:
            break
        await asyncio.sleep(0.1)
    assert client.available
    await finish_scaler(hass, entry, sim)

    sim = Sim1508()
    entry = await setup_scaler(hass, sim, read_only=True)
    with pytest.raises(ServiceValidationError):
        await call(hass, "switch", "turn_on", entity_id=entity_id(hass, entry, "switch", "freeze"))
    assert sim.freeze == 0
    await finish_scaler(hass, entry, sim)


# -- a serial matrix unit: one stream --------------------------------------------------------------------


async def test_serial_unit_uses_one_connection_and_hears_changes() -> None:
    """A unit on a serial bridge gets one connection, and front-panel changes still arrive."""
    sim = Sim(16, banner=False)
    port = await sim.start()
    client = ExtronClient("127.0.0.1", port, 16, 16, reconnect_delay=0.2)
    await client.async_start()
    try:
        assert await client.async_wait_ready(10)
        assert len(sim.conns) == 1
        sim.panel_tie(4, 9)  # announced as Qik; the ties are read again
        sim.panel_mute(2, True)
        sim.panel_volume(3, 20)
        for _ in range(50):
            if client.video.get(4) == 9 and client.volume.get(3) == 20:
                break
            await asyncio.sleep(0.1)
        assert client.video[4] == 9 and client.audio[4] == 9
        assert client.video_mute[2] is True
        assert client.volume[3] == 20
        await client.async_set_volume(5, 33)
        assert sim.volume[5] == 33 and client.volume[5] == 33
    finally:
        await client.async_stop()
        await sim.stop()


async def test_announcement_between_a_question_and_its_answer() -> None:
    """A line the unit volunteers is not mistaken for the answer, so nothing slips out of step."""
    sim = Sim(16, banner=False)
    sim.volume[7] = 12
    sim.gain[2] = -6
    sim.video[3] = 5
    port = await sim.start()
    client = ExtronClient("127.0.0.1", port, 16, 16, reconnect_delay=0.2)
    await client.async_start()
    try:
        assert await client.async_wait_ready(10)
        sim.before_reply = ["Out03 Vol11"]  # said just before the answer to the next question
        client.async_request_refresh()
        await asyncio.sleep(1.0)
        assert client.volume[7] == 12 and client.gain[2] == -6 and client.video[3] == 5
        assert client.volume[3] == 64  # read after the announcement, so the real value wins
        assert client.available
    finally:
        await client.async_stop()
        await sim.stop()
