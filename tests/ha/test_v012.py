"""0.1.2 features: milder resets, time zone and daylight saving, All outputs, the new status parsing."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.extron.protocol import (
    format_offset,
    normalise_offset,
    parse_info,
)

from .sim import Sim
from .test_integration import call, enable_hidden, entity_id, finish, setup, state_of

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


def test_offset_helpers() -> None:
    """Offsets are written H:MM and read back as +HH:MM."""
    assert format_offset(0) == "0:00"
    assert format_offset(60) == "1:00"
    assert format_offset(-300) == "-5:00"
    assert format_offset(330) == "5:30"
    assert format_offset(-720) == "-12:00"
    assert format_offset(840) == "14:00"
    assert "+" not in format_offset(60)
    assert normalise_offset("00:00") == "+00:00"
    assert normalise_offset("+01:00") == "+01:00"
    assert normalise_offset("-05:00") == "-05:00"
    assert normalise_offset("abc") is None
    assert parse_info("gmt_offset", "00:00") == "+00:00"
    assert parse_info("dst", "2") == 2
    assert parse_info("dst", "4") is None


async def test_time_zone_and_daylight_saving(hass: HomeAssistant) -> None:
    """The two selects write to the unit, and the clock is set after a zone change."""
    sim = Sim()
    entry = await setup(hass, sim)
    zone = entity_id(hass, entry, "select", "time_zone")
    dst = entity_id(hass, entry, "select", "daylight_saving")
    assert hass.states.get(zone).state == "GMT-12:00"
    assert hass.states.get(dst).state == "Off"
    await call(hass, "select", "select_option", entity_id=zone, option="GMT+00:00")
    assert sim.zone == "00:00"
    assert hass.states.get(zone).state == "GMT+00:00"
    assert any(line.endswith("CT") for line in sim.writes())  # the clock followed
    await call(hass, "select", "select_option", entity_id=zone, option="GMT+05:30")
    assert sim.zone == "+05:30"
    await call(hass, "select", "select_option", entity_id=zone, option="GMT-05:00")
    assert sim.zone == "-05:00"
    await call(hass, "select", "select_option", entity_id=dst, option="Europe")
    assert sim.dst == 2
    assert hass.states.get(dst).state == "Europe"
    assert state_of(hass, entry, "sensor", "diag_dst").state == "Europe"
    assert all("+" not in w for w in sim.writes() if w.endswith("CZ"))
    await finish(hass, entry, sim)


async def test_change_made_elsewhere_is_picked_up(hass: HomeAssistant) -> None:
    """Another connection changing the zone or daylight saving updates the selects."""
    sim = Sim()
    entry = await setup(hass, sim)
    sim.event("Ipz+01:00")
    sim.event("Ipx3")
    await hass.async_block_till_done()
    import asyncio  # noqa: PLC0415

    await asyncio.sleep(0.2)
    assert hass.states.get(entity_id(hass, entry, "select", "time_zone")).state == "GMT+01:00"
    assert hass.states.get(entity_id(hass, entry, "select", "daylight_saving")).state == "Brazil"
    await finish(hass, entry, sim)


async def test_all_outputs_select(hass: HomeAssistant) -> None:
    """One select ties an input to every output, and shows Mixed while they differ."""
    sim = Sim()
    entry = await setup(hass, sim)
    select = entity_id(hass, entry, "select", "all_outputs_link")
    assert hass.states.get(select).state == "Mixed"
    await call(hass, "select", "select_option", entity_id=select, option="Input #03")
    assert all(v == 3 for v in sim.video.values()) and all(v == 3 for v in sim.audio.values())
    assert hass.states.get(select).state == "Input #03"
    with pytest.raises(ServiceValidationError):
        await call(hass, "select", "select_option", entity_id=select, option="Mixed")
    await call(hass, "select", "select_option", entity_id=select, option="None")
    assert all(v == 0 for v in sim.video.values())
    await finish(hass, entry, sim)


async def test_resets_press_twice_and_reread(hass: HomeAssistant) -> None:
    """Each reset arms on the first press, acts on the second, and the state is read again."""
    sim = Sim()
    entry = await setup(hass, sim, show_advanced=True)
    await enable_hidden(hass, entry)
    sim.vmute[3] = sim.amute[7] = True
    sim.gain[5] = 3
    sim.volume[5] = 40
    for key in ("reset_mutes", "reset_input_levels", "reset_output_volumes"):
        button = entity_id(hass, entry, "button", key)
        await call(hass, "button", "press", entity_id=button)
        assert hass.states.get(button).attributes["armed"] is True
    assert not [w for w in sim.writes() if w.endswith(("ZZ", "ZA", "ZV"))]  # nothing sent yet
    for key in ("reset_mutes", "reset_input_levels", "reset_output_volumes"):
        button = entity_id(hass, entry, "button", key)
        await call(hass, "button", "press", entity_id=button)
        assert hass.states.get(button).attributes["armed"] is False
    assert not any(sim.vmute.values()) and not any(sim.amute.values())
    assert sim.gain[5] == 0 and sim.volume[5] == 64
    # The unit said nothing to other connections, so the integration read it all again.
    assert hass.states.get(entity_id(hass, entry, "number", "out_5_volume")).state == "64"
    assert hass.states.get(entity_id(hass, entry, "number", "in_5_level")).state == "0"
    assert hass.states.get(entity_id(hass, entry, "switch", "out_3_video_mute")).state == "off"
    await finish(hass, entry, sim)


async def test_resets_are_off_by_default(hass: HomeAssistant) -> None:
    """The reset buttons only appear with Show advanced controls."""
    sim = Sim()
    entry = await setup(hass, sim)
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    item = er.async_get(hass).async_get_entity_id(
        "button", "extron", f"{entry.entry_id}_reset_output_volumes"
    )
    assert er.async_get(hass).async_get(item).disabled_by is not None
    await finish(hass, entry, sim)


async def test_new_controls_refused_when_read_only(hass: HomeAssistant) -> None:
    """Nothing new writes on a read-only unit."""
    sim = Sim()
    entry = await setup(hass, sim, read_only=True)
    for key, option in (
        ("time_zone", "GMT+01:00"),
        ("daylight_saving", "Europe"),
        ("all_outputs_link", "Input #02"),
    ):
        with pytest.raises(ServiceValidationError):
            await call(
                hass,
                "select",
                "select_option",
                entity_id=entity_id(hass, entry, "select", key),
                option=option,
            )
    assert sim.writes() == []
    await finish(hass, entry, sim)
