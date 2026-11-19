"""Home Assistant level tests against the simulated unit."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.extron.const import DOMAIN

from .sim import Sim

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


def make_entry(port: int, **extra) -> MockConfigEntry:
    """Make an entry for the simulator."""
    data = {
        "host": "127.0.0.1",
        "port": port,
        "password": "",
        "read_only": False,
        "inputs": 24,
        "outputs": 24,
        "model_name": "Crosspoint-MAV 24x24 (60-472-01)",
        "unit_name": "Crosspoint-MAV-00-00-01",
        **extra,
    }
    return MockConfigEntry(
        domain=DOMAIN, data=data, title="Test Extron", unique_id=data["unit_name"]
    )


async def setup(hass: HomeAssistant, sim: Sim, **extra) -> MockConfigEntry:
    """Set up the integration against the simulated unit."""
    port = await sim.start()
    entry = make_entry(port, **extra)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def entity_id(hass: HomeAssistant, entry, domain: str, key: str) -> str:
    """Find an entity id from its unique id key."""
    found = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert found, f"no {domain} {key}"
    return found


def state_of(hass: HomeAssistant, entry, domain: str, key: str):
    """Return a state."""
    return hass.states.get(entity_id(hass, entry, domain, key))


async def finish(hass: HomeAssistant, entry, sim: Sim) -> None:
    """Unload and stop."""
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def call(hass: HomeAssistant, domain: str, service: str, **data) -> None:
    """Call a service and wait for it."""
    await hass.services.async_call(domain, service, data, blocking=True)


async def enable_hidden(hass: HomeAssistant, entry) -> None:
    """Turn on the entities that are off by default, then reload."""
    registry = er.async_get(hass)
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.disabled_by is not None:
            registry.async_update_entity(item.entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def test_entities_and_states(hass: HomeAssistant) -> None:
    """The entities exist and show the unit's state."""
    sim = Sim()
    sim.video[5], sim.audio[5] = 3, 4
    sim.vmute[2] = True
    sim.volume[24] = 50
    sim.gain[3] = -18
    sim.lock = 2
    entry = await setup(hass, sim)
    assert state_of(hass, entry, "binary_sensor", "connection").state == "on"
    link = state_of(hass, entry, "select", "out_1_link")
    assert link.state == "Input #01"
    assert link.attributes["options"][0] == "Input #01" and link.attributes["options"][
        -2:
    ] == ["None", "Mixed"]
    assert state_of(hass, entry, "select", "out_2_link").state == "None"
    assert state_of(hass, entry, "select", "out_5_link").state == "Mixed"
    assert state_of(hass, entry, "select", "out_5_video").state == "Input #03 Video"
    assert state_of(hass, entry, "select", "out_5_audio").state == "Input #04 Audio"
    assert state_of(hass, entry, "switch", "out_2_video_mute").state == "on"
    assert state_of(hass, entry, "switch", "out_2_audio_mute").state == "off"
    assert float(state_of(hass, entry, "number", "out_24_volume").state) == 50
    assert float(state_of(hass, entry, "number", "in_3_level").state) == -18
    assert state_of(hass, entry, "select", "lock").state.startswith("Mode 2")
    assert state_of(hass, entry, "sensor", "preset_1").state == "empty"
    assert state_of(hass, entry, "sensor", "preset_32").state == "empty"
    assert state_of(hass, entry, "button", "preset_3_recall").state == "unavailable"
    names = {s.attributes.get("friendly_name") for s in hass.states.async_all()}
    assert "Test Extron Output #01 Video Input" in names
    assert "Test Extron Input #01 Audio Level" in names
    assert "Test Extron Output #24 Audio Volume" in names
    await finish(hass, entry, sim)


async def test_controls(hass: HomeAssistant) -> None:
    """Ties, mutes, volume, level, lock and groups from Home Assistant."""
    sim = Sim()
    entry = await setup(hass, sim)
    link = entity_id(hass, entry, "select", "out_24_link")
    await call(hass, "select", "select_option", entity_id=link, option="Input #07")
    assert sim.video[24] == 7 and sim.audio[24] == 7
    assert hass.states.get(link).state == "Input #07"
    video = entity_id(hass, entry, "select", "out_24_video")
    await call(
        hass, "select", "select_option", entity_id=video, option="Input #09 Video"
    )
    assert sim.video[24] == 9 and sim.audio[24] == 7
    assert hass.states.get(link).state == "Mixed"
    with pytest.raises(ServiceValidationError):
        await call(hass, "select", "select_option", entity_id=link, option="Mixed")
    await call(hass, "select", "select_option", entity_id=link, option="None")
    assert sim.video[24] == 0 and sim.audio[24] == 0
    mute = entity_id(hass, entry, "switch", "out_24_audio_mute")
    await call(hass, "switch", "turn_on", entity_id=mute)
    assert sim.amute[24]
    await call(hass, "switch", "turn_off", entity_id=mute)
    assert not sim.amute[24]
    await call(
        hass,
        "number",
        "set_value",
        entity_id=entity_id(hass, entry, "number", "out_24_volume"),
        value=20,
    )
    assert sim.volume[24] == 20
    await call(
        hass,
        "number",
        "set_value",
        entity_id=entity_id(hass, entry, "number", "in_24_level"),
        value=-6,
    )
    assert sim.gain[24] == -6
    await call(
        hass,
        "number",
        "set_value",
        entity_id=entity_id(hass, entry, "number", "in_24_level"),
        value=12,
    )
    assert sim.gain[24] == 12
    await call(
        hass,
        "select",
        "select_option",
        entity_id=entity_id(hass, entry, "select", "lock"),
        option="Off",
    )
    assert sim.lock == 0
    await finish(hass, entry, sim)


async def test_front_panel_changes_appear(hass: HomeAssistant) -> None:
    """Changes made on the unit show up without asking."""
    sim = Sim()
    entry = await setup(hass, sim)
    sim.panel_mute(5, True)
    sim.panel_volume(6, 12)
    sim.panel_tie(7, 11)
    for _ in range(20):
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        if state_of(hass, entry, "select", "out_7_link").state == "Input #11":
            break
    assert state_of(hass, entry, "switch", "out_5_video_mute").state == "on"
    assert float(state_of(hass, entry, "number", "out_6_volume").state) == 12
    assert state_of(hass, entry, "select", "out_7_link").state == "Input #11"
    await finish(hass, entry, sim)


async def test_groups_off_by_default_and_work(hass: HomeAssistant) -> None:
    """Group selects are disabled until turned on."""
    sim = Sim()
    entry = await setup(hass, sim)
    registry = er.async_get(hass)
    group = registry.async_get_entity_id(
        "select", DOMAIN, f"{entry.entry_id}_in_24_group"
    )
    assert group and registry.async_get(group).disabled_by is not None
    assert hass.states.get(group) is None
    await enable_hidden(hass, entry)
    await call(hass, "select", "select_option", entity_id=group, option="Group 3")
    assert sim.groups_in[23] == 3
    assert hass.states.get(group).state == "Group 3"
    await finish(hass, entry, sim)


async def test_preset_buttons_and_confirmation(hass: HomeAssistant) -> None:
    """Saving over a saved preset needs a second press."""
    sim = Sim()
    entry = await setup(hass, sim)
    await enable_hidden(hass, entry)
    save = entity_id(hass, entry, "button", "preset_5_save")
    recall = entity_id(hass, entry, "button", "preset_5_recall")
    clear = entity_id(hass, entry, "button", "preset_5_clear")
    sensor = entity_id(hass, entry, "sensor", "preset_5")
    assert hass.states.get(recall).state == "unavailable"

    # empty: saves straight away
    await call(
        hass,
        "select",
        "select_option",
        entity_id=entity_id(hass, entry, "select", "out_1_link"),
        option="Input #02",
    )
    await call(hass, "button", "press", entity_id=save)
    assert 5 in sim.presets and sim.presets[5]["video"][1] == 2
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "saved"
    assert hass.states.get(recall).state != "unavailable"

    # saved: first press only arms
    await call(
        hass,
        "select",
        "select_option",
        entity_id=entity_id(hass, entry, "select", "out_1_link"),
        option="Input #09",
    )
    await call(hass, "button", "press", entity_id=save)
    assert sim.presets[5]["video"][1] == 2
    assert hass.states.get(save).attributes["armed"] is True
    notes = persistent_notification._async_get_or_create_notifications(hass)
    assert any("again within" in n["message"] for n in notes.values())
    # ... and it times out
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert hass.states.get(save).attributes["armed"] is False
    await call(hass, "button", "press", entity_id=save)  # arms again
    assert sim.presets[5]["video"][1] == 2
    await call(hass, "button", "press", entity_id=save)  # second press goes ahead
    assert sim.presets[5]["video"][1] == 9
    assert hass.states.get(save).attributes["armed"] is False

    # recall
    await call(
        hass,
        "select",
        "select_option",
        entity_id=entity_id(hass, entry, "select", "out_1_link"),
        option="None",
    )
    await call(hass, "button", "press", entity_id=recall)
    assert sim.video[1] == 9

    # clear also asks twice
    await call(hass, "button", "press", entity_id=clear)
    assert 5 in sim.presets
    await call(hass, "button", "press", entity_id=clear)
    assert 5 not in sim.presets
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "empty"
    await finish(hass, entry, sim)


async def test_read_only(hass: HomeAssistant) -> None:
    """A read-only unit shows state but refuses every change."""
    sim = Sim()
    sim.video[3] = 4
    entry = await setup(hass, sim, read_only=True)
    assert state_of(hass, entry, "select", "out_3_video").state == "Input #04 Video"
    with pytest.raises(ServiceValidationError):
        await call(
            hass,
            "select",
            "select_option",
            entity_id=entity_id(hass, entry, "select", "out_3_link"),
            option="Input #02",
        )
    with pytest.raises(ServiceValidationError):
        await call(
            hass,
            "switch",
            "turn_on",
            entity_id=entity_id(hass, entry, "switch", "out_3_video_mute"),
        )
    with pytest.raises(ServiceValidationError):
        await call(
            hass,
            "number",
            "set_value",
            entity_id=entity_id(hass, entry, "number", "out_3_volume"),
            value=5,
        )
    assert sim.video[3] == 4 and not sim.vmute[3] and sim.volume[3] == 64
    assert sim.writes() == []
    await finish(hass, entry, sim)


async def test_unavailable_and_recovery(hass: HomeAssistant) -> None:
    """Entities go unavailable when the unit drops and come back."""
    sim = Sim()
    entry = await setup(hass, sim)
    sim.drop_all()
    for _ in range(40):
        await asyncio.sleep(0.05)
        if state_of(hass, entry, "select", "out_1_link").state == "unavailable":
            break
    assert state_of(hass, entry, "select", "out_1_link").state == "unavailable"
    assert state_of(hass, entry, "binary_sensor", "connection").state == "off"
    for _ in range(150):
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        if state_of(hass, entry, "select", "out_1_link").state != "unavailable":
            break
    assert state_of(hass, entry, "select", "out_1_link").state == "Input #01"
    assert state_of(hass, entry, "binary_sensor", "connection").state == "on"
    await finish(hass, entry, sim)


async def test_failed_command_is_an_error(hass: HomeAssistant) -> None:
    """A unit that is not answering gives a clear error (and nothing is retried silently)."""
    sim = Sim()
    entry = await setup(hass, sim)
    entry.runtime_data.client._request_timeout = 0.3
    sim.mute_replies = True
    with pytest.raises(HomeAssistantError):
        await call(
            hass,
            "number",
            "set_value",
            entity_id=entity_id(hass, entry, "number", "out_3_volume"),
            value=5,
        )
    sim.mute_replies = False
    await finish(hass, entry, sim)


async def test_not_ready_when_unit_is_off(hass: HomeAssistant) -> None:
    """Setup retries later instead of creating entities with unknown names."""
    entry = make_entry(1)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state.name == "SETUP_RETRY"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_config_flow(hass: HomeAssistant) -> None:
    """Setting up from the screen."""
    sim = Sim()
    port = await sim.start()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    # nothing listening
    bad = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Matrix",
            "host": "127.0.0.1",
            "port": 1,
            "password": "",
            "read_only": False,
        },
    )
    assert bad["errors"] == {"base": "cannot_connect"}
    good = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Matrix",
            "host": "127.0.0.1",
            "port": port,
            "password": "",
            "read_only": True,
        },
    )
    assert good["type"] is FlowResultType.CREATE_ENTRY
    assert (
        good["data"]["inputs"] == 24
        and good["data"]["outputs"] == 24
        and good["data"]["read_only"] is True
    )
    assert good["result"].unique_id == "Crosspoint-MAV-00-00-01"
    # the same unit again
    again = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    dup = await hass.config_entries.flow.async_configure(
        again["flow_id"],
        {
            "name": "X",
            "host": "127.0.0.1",
            "port": port,
            "password": "",
            "read_only": False,
        },
    )
    assert dup["type"] is FlowResultType.ABORT and dup["reason"] == "already_configured"
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(good["result"].entry_id)
    await sim.stop()


async def test_config_flow_password(hass: HomeAssistant) -> None:
    """A protected unit asks for a password."""
    sim = Sim(password="secret")
    port = await sim.start()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    base = {"name": "Matrix", "host": "127.0.0.1", "port": port, "read_only": False}
    wrong = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**base, "password": "nope"}
    )
    assert wrong["errors"] == {"base": "invalid_auth"}
    right = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**base, "password": "secret"}
    )
    assert (
        right["type"] is FlowResultType.CREATE_ENTRY
        and right["data"]["password"] == "secret"
    )
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(right["result"].entry_id)
    await sim.stop()


async def test_reconfigure(hass: HomeAssistant) -> None:
    """Change the read-only setting."""
    sim = Sim()
    entry = await setup(hass, sim)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    done = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"host": "127.0.0.1", "port": sim.port, "password": "", "read_only": True},
    )
    assert (
        done["type"] is FlowResultType.ABORT
        and done["reason"] == "reconfigure_successful"
    )
    await hass.async_block_till_done()
    assert entry.data["read_only"] is True
    await finish(hass, entry, sim)


async def test_options_flow_names(hass: HomeAssistant) -> None:
    """Rename inputs and outputs; unchanged lines stay default."""
    sim = Sim()
    entry = await setup(hass, sim)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.FORM
    data = entry.runtime_data
    fields = {
        "input_names": "\n".join(["Sky"] + data.in_video[1:]),
        "audio_input_names": "\n".join(data.in_audio),
        "output_names": "\n".join(["Lounge TV"] + data.out_video[1:]),
        "audio_output_names": "\n".join(data.out_audio),
        "refresh_seconds": 120,
    }
    bad = await hass.config_entries.options.async_configure(
        flow["flow_id"], {**fields, "input_names": "Sky\nSky"}
    )
    assert bad["errors"] == {"input_names": "wrong_count"}
    dup = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {**fields, "input_names": "\n".join(["Sky", "Sky"] + data.in_video[2:])},
    )
    assert dup["errors"] == {"input_names": "duplicate_names"}
    done = await hass.config_entries.options.async_configure(flow["flow_id"], fields)
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert (
        entry.options["input_names"][0] == "Sky"
        and entry.options["input_names"][1] == ""
    )
    assert entry.options["refresh_seconds"] == 120
    assert (
        state_of(hass, entry, "select", "out_1_video").attributes["options"][0] == "Sky"
    )
    assert (
        state_of(hass, entry, "select", "out_1_video").attributes["friendly_name"]
        == "Test Extron Lounge TV Input"
    )
    await finish(hass, entry, sim)


async def test_duplicate_unit_names_are_made_unique(hass: HomeAssistant) -> None:
    """Two inputs with the same name on the unit get (2) added."""
    sim = Sim()
    sim.in_names[2] = sim.in_names[1]
    entry = await setup(hass, sim)
    assert entry.runtime_data.in_base[:2] == ["Input #01", "Input #01 (2)"]
    await finish(hass, entry, sim)


async def test_diagnostics(hass: HomeAssistant) -> None:
    """Diagnostics hide the address and password."""
    from custom_components.extron.diagnostics import async_get_config_entry_diagnostics

    sim = Sim()
    entry = await setup(hass, sim, password="s3cret")
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["entry"]["data"]["host"] == "**REDACTED**"
    assert result["entry"]["data"]["password"] == "**REDACTED**"
    assert result["state"]["available"] is True
    await finish(hass, entry, sim)
