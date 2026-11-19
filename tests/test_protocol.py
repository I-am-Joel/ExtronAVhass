"""Protocol tests: the client against the simulated unit."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "ha"))
from sim import Sim

from custom_components.extron.protocol import (
    ExtronAuthError,
    ExtronClient,
    ExtronCommandError,
    ExtronReadOnlyError,
    ExtronTimeoutError,
    async_probe,
    error_code,
    parse_banner,
    parse_digits,
    parse_gain,
    parse_size,
    parse_tie_list,
)

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


def test_parsers():
    """The pure parsing helpers."""
    assert parse_size("V24X24 A24X24") == (24, 24)
    assert parse_size("nonsense") is None
    assert parse_tie_list("02 02 -- 15 Vid") == [2, 2, None, 15]
    assert parse_gain("+05") == 5
    assert parse_gain("-18") == -18
    assert parse_gain("E13") is None
    assert parse_digits("0,0,0,", 3) == [0, 0, 0]
    assert parse_digits("00", 3) is None
    assert error_code("E11") == 11
    assert error_code("11") is None
    banner = parse_banner(
        "(c) Copyright 2006, Extron Electronics, Crosspoint-MAV, V2.03, 60-472-01"
    )
    assert banner == {
        "product": "Crosspoint-MAV",
        "firmware": "2.03",
        "part_number": "60-472-01",
    }


async def make(sim: Sim, **kw) -> ExtronClient:
    """Start a client against the simulator and wait for its first read."""
    port = await sim.start()
    client = ExtronClient("127.0.0.1", port, sim.n, sim.n, reconnect_delay=0.2, **kw)
    await client.async_start()
    assert await client.async_wait_ready(10)
    return client


async def test_initial_read():
    """Everything is read at start-up."""
    sim = Sim()
    sim.video[5], sim.audio[5] = 3, 4
    sim.vmute[2] = True
    sim.volume[24] = 50
    sim.gain[3] = -18
    sim.lock = 2
    sim.presets[30] = {
        "video": dict(sim.video),
        "audio": dict(sim.audio),
        "name": "NIGHT",
    }
    client = await make(sim)
    try:
        assert client.available
        assert client.video[5] == 3 and client.audio[5] == 4
        assert client.video[24] == 0
        assert client.video_mute[2] and not client.video_mute[3]
        assert client.volume[24] == 50 and client.volume[1] == 64
        assert client.gain[3] == -18 and client.gain[1] == 0
        assert client.lock_mode == 2
        assert client.groups_in == [0] * 24
        assert (
            client.in_names[1] == "Input #01" and client.out_names[24] == "Output #24"
        )
        assert client.preset_count == 32
        assert client.presets[30].saved and client.presets[30].name == "NIGHT"
        assert not client.presets[29].saved
        assert client.banner["firmware"] == "2.03"
        # the command connection stayed in plain mode, the other is in mode 3
        modes = sorted(c.verbose for c in sim.conns)
        assert modes == [0, 3]
    finally:
        await client.async_stop()
        await sim.stop()


async def test_changes_reach_the_unit():
    """Ties, mutes, volume, level, lock, groups."""
    sim = Sim()
    client = await make(sim)
    try:
        await client.async_set_tie(24, 3)
        assert sim.video[24] == 3 and sim.audio[24] == 3
        await client.async_set_tie(24, 4, audio=False)
        assert sim.video[24] == 4 and sim.audio[24] == 3
        await client.async_set_tie(24, 5, video=False)
        assert sim.audio[24] == 5 and sim.video[24] == 4
        await client.async_set_tie(24, 0)
        assert sim.video[24] == 0 and sim.audio[24] == 0
        await client.async_set_mute(23, video=True, muted=True)
        await client.async_set_mute(23, video=False, muted=True)
        assert sim.vmute[23] and sim.amute[23]
        await client.async_set_mute(23, video=True, muted=False)
        assert not sim.vmute[23] and sim.amute[23]
        await client.async_set_volume(24, 0)
        assert sim.volume[24] == 0
        await client.async_set_gain(24, 7)
        assert sim.gain[24] == 7
        await client.async_set_gain(24, -18)
        assert sim.gain[24] == -18
        await client.async_set_lock(1)
        assert sim.lock == 1
        await client.async_set_group(output=False, number=24, group=3)
        assert sim.groups_in[23] == 3 and sum(sim.groups_in) == 3
        await client.async_set_group(output=True, number=1, group=4)
        assert sim.groups_out[0] == 4
        # state follows
        assert (
            client.volume[24] == 0 and client.gain[24] == -18 and client.lock_mode == 1
        )
    finally:
        await client.async_stop()
        await sim.stop()


async def test_invalid_values_are_refused_before_sending():
    """Range checks."""
    sim = Sim()
    client = await make(sim)
    try:
        for coro in (
            client.async_set_volume(1, 65),
            client.async_set_volume(25, 10),
            client.async_set_gain(1, 25),
            client.async_set_gain(1, -19),
            client.async_set_tie(1, 25),
            client.async_set_lock(3),
            client.async_set_group(output=False, number=1, group=5),
            client.async_recall_preset(33),
        ):
            with pytest.raises(ValueError):
                await coro
    finally:
        await client.async_stop()
        await sim.stop()


async def test_unit_errors_become_exceptions():
    """An E-code reply raises."""
    sim = Sim()
    client = await make(sim)
    try:
        sim.preset_max = 32
        with pytest.raises(ExtronCommandError) as err:
            await client._request("5.")  # recalling an empty preset
        assert err.value.code == 11
        assert client.last_error and "E11" in client.last_error
    finally:
        await client.async_stop()
        await sim.stop()


async def test_events_from_other_controllers():
    """Changes made elsewhere are seen without asking."""
    sim = Sim()
    client = await make(sim)
    try:
        sim.panel_mute(5, True)
        sim.panel_mute(6, True, video=False)
        sim.panel_volume(7, 33)
        sim.panel_gain(8, -4)
        sim.panel_lock(2)
        await asyncio.sleep(0.2)
        assert client.video_mute[5] and client.audio_mute[6]
        assert client.volume[7] == 33 and client.gain[8] == -4 and client.lock_mode == 2
        # a front-panel tie only says "Qik": the client reads the ties again
        sim.panel_tie(9, 12)
        await asyncio.sleep(0.4)
        assert client.video[9] == 12 and client.audio[9] == 12
        # a second controller's tie is announced in full
        other = ExtronClient("127.0.0.1", sim.port, 24, 24)
        await other.async_start()
        assert await other.async_wait_ready(10)
        await other.async_set_tie(10, 2, audio=False)
        await asyncio.sleep(0.2)
        assert client.video[10] == 2 and client.audio[10] == 0
        await other.async_set_mute(11, video=True, muted=True)
        await asyncio.sleep(0.2)
        assert client.video_mute[11]
        await other.async_stop()
    finally:
        await client.async_stop()
        await sim.stop()


async def test_presets():
    """Saved or empty, save, recall and clear."""
    sim = Sim()
    client = await make(sim)
    try:
        assert not client.presets[3].saved
        await client.async_set_tie(2, 7)
        await client.async_save_preset(3)
        assert client.presets[3].saved and sim.presets[3]["video"][2] == 7
        await client.async_set_tie(2, 9)
        await client.async_recall_preset(3)
        assert client.video[2] == 7 and sim.video[2] == 7
        await client.async_clear_preset(3)
        assert not client.presets[3].saved and 3 not in sim.presets
        # a saved preset without a name (the unit may answer with an empty line)
        sim.presets[4] = {
            "video": dict(sim.video),
            "audio": dict(sim.audio),
            "name": "",
        }
        await client.async_refresh_presets()
        assert client.presets[4].saved and client.presets[4].name == ""
    finally:
        await client.async_stop()
        await sim.stop()


async def test_read_only_never_writes():
    """Read-only blocks every change at the protocol level."""
    sim = Sim()
    client = await make(sim, read_only=True)
    try:
        calls = (
            client.async_set_tie(1, 2),
            client.async_set_mute(1, video=True, muted=True),
            client.async_set_volume(1, 5),
            client.async_set_gain(1, 5),
            client.async_set_lock(1),
            client.async_set_group(output=True, number=1, group=1),
            client.async_recall_preset(1),
            client.async_save_preset(1),
            client.async_clear_preset(1),
        )
        for coro in calls:
            with pytest.raises(ExtronReadOnlyError):
                await coro
        # only reads were sent
        assert sim.writes() == [], sim.writes()
    finally:
        await client.async_stop()
        await sim.stop()


async def test_reconnects_after_a_drop():
    """The link comes back by itself and state is read again."""
    sim = Sim()
    client = await make(sim)
    try:
        sim.drop_all()
        await asyncio.sleep(0.1)
        assert not client.available
        sim.volume[5] = 11
        for _ in range(50):
            if client.available:
                break
            await asyncio.sleep(0.2)
        assert client.available
        assert client.volume[5] == 11
    finally:
        await client.async_stop()
        await sim.stop()


async def test_password():
    """A password-protected unit: wrong or missing password fails, right one works."""
    sim = Sim(password="secret")
    port = await sim.start()
    try:
        with pytest.raises(ExtronAuthError):
            await async_probe("127.0.0.1", port, "")
        with pytest.raises(ExtronAuthError):
            await async_probe("127.0.0.1", port, "wrong")
        info = await async_probe("127.0.0.1", port, "secret")
        assert info["inputs"] == 24 and info["unit_name"] == "Crosspoint-MAV-00-00-01"
        assert info["privilege"] == "Administrator" and info["firmware"] == "2.03"
        client = ExtronClient("127.0.0.1", port, 24, 24, password="secret")
        await client.async_start()
        assert await client.async_wait_ready(10)
        await client.async_stop()
    finally:
        await sim.stop()


async def test_silent_unit_is_noticed():
    """A request that gets no answer fails and restarts the link."""
    sim = Sim()
    client = await make(sim)
    client._request_timeout = 0.3
    try:
        sim.mute_replies = True
        with pytest.raises(ExtronTimeoutError):
            await client.async_set_volume(1, 5)
        sim.mute_replies = False
        for _ in range(100):  # first it notices, then it comes back
            if not client.available:
                break
            await asyncio.sleep(0.05)
        assert not client.available
        for _ in range(100):
            if client.available:
                break
            await asyncio.sleep(0.1)
        assert client.available
        await client.async_set_volume(1, 6)
        assert sim.volume[1] == 6
    finally:
        await client.async_stop()
        await sim.stop()


def test_parse_status_and_info() -> None:
    """The S reply and the unit-information replies."""
    from custom_components.extron.protocol import parse_info, parse_status

    status = parse_status("5.06 3.32 2.51 11.15 -5.04 -11.98 11.52 71.60 1081 1095 1 1")
    assert status["temp_f"] == 71.6 and status["psu2"] == 1 and status["fan1"] == 1081
    assert parse_status("3.3 5.0 -5 15 -15 70") == {}
    assert parse_status("E10") == {}
    assert parse_info("timeout", "00030") == 300
    assert parse_info("connections", "006") == 6
    assert parse_info("free_space", "1245184 Bytes Left") == 1245184
    assert parse_info("dhcp", "0") is False
    assert parse_info("ip", "E13") is None
