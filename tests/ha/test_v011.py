"""0.1.1 features: diagnostics, media player, route, mute all, clock, advanced option."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.extron.const import DOMAIN

from .sim import Sim
from .test_integration import call, entity_id, finish, setup, state_of

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


async def test_diagnostic_sensors(hass: HomeAssistant) -> None:
    """Status and information appear."""
    sim = Sim()
    entry = await setup(hass, sim)
    assert float(state_of(hass, entry, "sensor", "diag_temp_f").state) == 22.0
    assert float(state_of(hass, entry, "sensor", "diag_fan1").state) == 1081
    assert float(state_of(hass, entry, "sensor", "diag_v5").state) == 5.06
    assert state_of(hass, entry, "sensor", "diag_ip").state == "192.168.0.254"
    assert state_of(hass, entry, "sensor", "diag_firmware").state == "2.03"
    assert state_of(hass, entry, "sensor", "diag_connections").state == "6"
    assert state_of(hass, entry, "sensor", "diag_timeout").state == "300"
    assert state_of(hass, entry, "sensor", "diag_dhcp").state == "Off"
    assert state_of(hass, entry, "sensor", "diag_outputs").state == "24"
    assert state_of(hass, entry, "binary_sensor", "diag_psu1").state == "off"
    summary = state_of(hass, entry, "sensor", "routing_summary")
    assert summary.state == "1 of 24 outputs routed"
    assert summary.attributes["routes"]["Output #01"]["video"] == "Input #01"
    await finish(hass, entry, sim)


async def test_all_outputs_player(hass: HomeAssistant) -> None:
    """The media player ties an input to every output."""
    sim = Sim()
    entry = await setup(hass, sim)
    player = entity_id(hass, entry, "media_player", "all_outputs")
    assert hass.states.get(player).attributes["source"] == "Mixed"
    await call(
        hass, "media_player", "select_source", entity_id=player, source="Input #05"
    )
    await hass.async_block_till_done()
    assert set(sim.video.values()) == {5} and set(sim.audio.values()) == {5}
    assert hass.states.get(player).attributes["source"] == "Input #05"
    await call(hass, "media_player", "select_source", entity_id=player, source="None")
    assert set(sim.video.values()) == {0}
    with pytest.raises(ServiceValidationError):
        await call(
            hass, "media_player", "select_source", entity_id=player, source="Mixed"
        )
    await finish(hass, entry, sim)


async def test_route_action(hass: HomeAssistant) -> None:
    """One input to several named outputs, with validation."""
    sim = Sim()
    entry = await setup(hass, sim)
    player = entity_id(hass, entry, "media_player", "all_outputs")
    await call(
        hass,
        DOMAIN,
        "route",
        entity_id=player,
        input="input #07",
        outputs=["Output #03", "5", "output #09"],
    )
    await hass.async_block_till_done()
    assert [sim.video[o] for o in (3, 5, 9)] == [7, 7, 7]
    assert [sim.audio[o] for o in (3, 5, 9)] == [7, 7, 7]
    assert sim.video[4] == 0
    await call(
        hass,
        DOMAIN,
        "route",
        entity_id=player,
        input="Input #02",
        outputs=["Output #04", "Output #06"],
        mode="video",
    )
    assert sim.video[4] == 2 and sim.audio[4] == 0
    with pytest.raises(ServiceValidationError):
        await call(
            hass,
            DOMAIN,
            "route",
            entity_id=player,
            input="Nope",
            outputs=["Output #04"],
        )
    with pytest.raises(ServiceValidationError):
        await call(
            hass,
            DOMAIN,
            "route",
            entity_id=player,
            input="Input #02",
            outputs=["Nowhere"],
        )
    await finish(hass, entry, sim)


async def test_mute_all_and_clock(hass: HomeAssistant) -> None:
    """Mute all switches and the clock button."""
    sim = Sim()
    entry = await setup(hass, sim)
    switch = entity_id(hass, entry, "switch", "mute_all_video")
    await call(hass, "switch", "turn_on", entity_id=switch)
    await hass.async_block_till_done()
    assert all(sim.vmute.values()) and not any(sim.amute.values())
    assert hass.states.get(switch).state == "on"
    await call(hass, "switch", "turn_off", entity_id=switch)
    assert not any(sim.vmute.values())
    await call(
        hass,
        "button",
        "press",
        entity_id=entity_id(hass, entry, "button", "sync_clock"),
    )
    assert sim.clock != "Thu, 19 Nov 2026 16:23:07"
    await finish(hass, entry, sim)


async def test_new_actions_refused_when_read_only(hass: HomeAssistant) -> None:
    """Nothing new writes on a read-only unit."""
    sim = Sim()
    entry = await setup(hass, sim, read_only=True)
    player = entity_id(hass, entry, "media_player", "all_outputs")
    with pytest.raises(ServiceValidationError):
        await call(
            hass, "media_player", "select_source", entity_id=player, source="Input #05"
        )
    with pytest.raises(ServiceValidationError):
        await call(
            hass,
            "switch",
            "turn_on",
            entity_id=entity_id(hass, entry, "switch", "mute_all_audio"),
        )
    with pytest.raises(ServiceValidationError):
        await call(
            hass,
            "button",
            "press",
            entity_id=entity_id(hass, entry, "button", "sync_clock"),
        )
    with pytest.raises(ServiceValidationError):
        await call(
            hass,
            DOMAIN,
            "route",
            entity_id=player,
            input="Input #02",
            outputs=["Output #04", "Output #05"],
        )
    assert sim.writes() == []
    await finish(hass, entry, sim)


async def test_advanced_option(hass: HomeAssistant) -> None:
    """The tick enables the hidden entities, and unticking hides them again."""
    sim = Sim()
    entry = await setup(hass, sim)
    registry = er.async_get(hass)
    uid = f"{entry.entry_id}_out_3_group"
    assert registry.async_get_entity_id("select", DOMAIN, uid) is not None
    assert (
        registry.async_get(
            registry.async_get_entity_id("select", DOMAIN, uid)
        ).disabled_by
        is not None
    )
    hass.config_entries.async_update_entry(entry, options={"show_advanced": True})
    await hass.async_block_till_done()
    item = registry.async_get(registry.async_get_entity_id("select", DOMAIN, uid))
    assert item.disabled_by is None
    assert hass.states.get(item.entity_id) is not None
    hass.config_entries.async_update_entry(entry, options={"show_advanced": False})
    await hass.async_block_till_done()
    item = registry.async_get(item.entity_id)
    assert item.disabled_by is not None
    await finish(hass, entry, sim)
