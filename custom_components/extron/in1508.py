"""Extron IN1508 scaling presentation switcher (SIS over a serial-to-Ethernet bridge).

Unlike the matrix switchers the IN1508 has one control stream: a second connection to a
serial bridge makes the bridge drop the first. So this client keeps one connection, with a
reader task that hands each line either to the command waiting for its answer or to the
listener for announcements (front-panel changes).

What was learnt from the real unit (firmware 2.25):

* every change is answered with a tagged line (``Vmt1``, ``Brt64``, ``Rte3*2``...);
* reads answer in about 10 ms, but selecting another input takes up to 3 seconds and is
  answered with ``ChnN`` followed by ``Reconfig``;
* the picture controls act on the selected input only;
* a mute comes on by itself when the selected input has no signal, and changing input clears
  audio mute and freeze, so mute and freeze are read again after every input change.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .protocol import (
    ExtronCommandError,
    ExtronConnectionError,
    ExtronError,
    ExtronLink,
    ExtronReadOnlyError,
    ExtronTimeoutError,
    error_code,
)

_LOGGER = logging.getLogger(__name__)

PART_PREFIX = "60-569"
INPUTS = 8

MIN_VOLUME, MAX_VOLUME = 0, 100
MIN_PICTURE, MAX_PICTURE = 0, 128
MIN_GAIN, MAX_GAIN = -50, 9  # the unit takes lower values than -50 up to a point, but -50 is the limit used

SELECT_TIMEOUT = 8.0  # an input change takes up to about 3 seconds
READ_TIMEOUT = 3.0
SETTLE_SECONDS = 0.6  # quiet time after an announcement before the state is read again

ERROR_TEXT = {
    9: "invalid function",
    10: "invalid command",
    13: "value out of range",
    17: "not valid for the signal on this input",
}

RESOLUTIONS = {
    1: "640 x 480",
    2: "800 x 600",
    3: "1024 x 768",
    4: "1280 x 1024",
    5: "1024 x 852",
    6: "1024 x 1024",
    7: "1366 x 768",
    8: "1365 x 1024",
    9: "1400 x 1050",
    10: "1600 x 1200",
    11: "480p",
    12: "576p",
    13: "720p",
    14: "1080i",
    15: "1080p",
    16: "1280 x 768",
    17: "1440 x 900",
    18: "1680 x 1050",
    19: "1280 x 800",
    20: "852 x 480",
}
RATES = {1: "50 Hz", 2: "60 Hz", 3: "75 Hz"}
# The refresh rates the unit accepts for each output resolution (as tested).
VALID_RATES = {
    **dict.fromkeys(range(1, 9), (1, 2, 3)),
    9: (1, 2),
    10: (1, 2),
    11: (2,),
    12: (1,),
    13: (1, 2),
    14: (1, 2),
    15: (2,),
    16: (2,),
    17: (2, 3),
    18: (2,),
    19: (1, 2),
    20: (1, 2),
}
PIP_SIZES = {
    0: "1/4 screen",
    1: "1/9 screen",
    2: "1/16 screen",
    3: "1/25 screen",
    4: "Side by side",
    5: "Side by side (full screen)",
}
ASPECTS = {0: "4:3", 1: "16:9"}
STANDARDS = {0: "None", 1: "NTSC 3.58", 2: "PAL", 3: "NTSC 4.43", 4: "SECAM"}

# The picture controls: key -> (read, set, reply tag, minimum, maximum)
PICTURE = {
    "colour": ("C", "C", "Col", MIN_PICTURE, MAX_PICTURE),
    "tint": ("T", "T", "Tin", MIN_PICTURE, MAX_PICTURE),
    "brightness": ("Y", "Y", "Brt", MIN_PICTURE, MAX_PICTURE),
    "contrast": ("^", "^", "Con", MIN_PICTURE, MAX_PICTURE),
    "sharpness": ("D", "D", "Shp", MIN_PICTURE, MAX_PICTURE),
}
PICTURE_KEYS = (*PICTURE, "gain", "aspect")

_ANY_ERROR = r"E\d\d"
_RE_STATUS = re.compile(r"^Chn(\d+)\s+Pch(\d+)\s+Std(\d+)$")
_RE_CHN = re.compile(r"^Chn(\d+)$")
_RE_NUMBER = {
    "mute_video": re.compile(r"^Vmt([01])$"),
    "mute_audio": re.compile(r"^Amt([01])$"),
    "freeze": re.compile(r"^Frz([01])$"),
    "volume": re.compile(r"^Vol(\d+)$"),
    "pip": re.compile(r"^Pip([01])$"),
    "pip_size": re.compile(r"^Pmd(\d)$"),
}
_RE_RATE = re.compile(r"^Rte(\d+)\*(\d+)$")
_RE_GAIN = re.compile(r"^Aud([+-]?\d+)$")
_RE_ASPECT = re.compile(r"^Asp([01])$")
_RE_PCH = re.compile(r"^Pch(\d+)$")
# Lines the unit volunteers: any of them means "look again shortly".
_ANNOUNCE = re.compile(
    r"^(Reconfig|Chn\d+|Pch\d+|Pip[01]|Frz[01]|Vmt[01]|Amt[01]|Vol\d+|Col\d+|Tin\d+|"
    r"Brt\d+|Con\d+|Shp\d+|Aud[+-]?\d+|Pmd\d|Rte\d+\*\d+|Asp[01]|Exe\d)$"
)


def parse_in_status(line: str) -> tuple[int, int, int] | None:
    """Parse ``Chn1 Pch0 Std0`` into (input, pip input, signal standard)."""
    match = _RE_STATUS.match(line.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def clamp_rate(resolution: int, rate: int) -> int:
    """Return ``rate`` if the resolution allows it, else 60 Hz if it does, else the first allowed."""
    allowed = VALID_RATES.get(resolution, ())
    if rate in allowed:
        return rate
    return 2 if 2 in allowed else (allowed[0] if allowed else rate)


class Extron1508CommandError(ExtronCommandError):
    """The unit answered with an error code (worded for the IN1508)."""

    def __init__(self, code: int) -> None:
        """Remember the code."""
        super().__init__(code)
        self.args = (f"Unit reported E{code:02d} ({ERROR_TEXT.get(code, 'error')})",)


@dataclass
class Picture:
    """What one input's picture controls hold (None: the unit does not offer it for that input)."""

    values: dict[str, int | None] = field(default_factory=dict)


async def async_describe_in1508(link: ExtronLink) -> dict[str, Any] | None:
    """Describe an IN1508 on this connection (it has just answered ``I``), or None if it is not one."""
    info: dict[str, Any] = {"kind": "in1508", "product": "IN1508", "inputs": INPUTS, "outputs": 1}
    for wire, key in (("N", "part_number"), ("Q", "firmware")):
        await link.write(wire)
        reply = await link.read_line(3)
        if reply is None:
            raise ExtronTimeoutError("No answer from the unit")
        info[key] = reply.strip()
    if not str(info["part_number"]).startswith(PART_PREFIX):
        return None
    return info


class Extron1508Client:
    """Keeps an IN1508's state up to date and carries out changes."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        read_only: bool = False,
        refresh_interval: float = 60.0,
        reconnect_delay: float = 5.0,
    ) -> None:
        """Initialise (nothing connects until async_start)."""
        self._host = host
        self._port = port
        self.read_only = read_only
        self._refresh_interval = refresh_interval
        self._reconnect_delay = reconnect_delay

        self._task: asyncio.Task | None = None
        self._link: ExtronLink | None = None
        self._lock = asyncio.Lock()
        self._waiter: tuple[re.Pattern[str], asyncio.Future[str]] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._ready = asyncio.Event()
        self._settle_task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()

        self.available = False
        self.banner: dict[str, str] = {}
        self.status: dict[str, float] = {}
        self.info: dict[str, Any] = {}
        self.privilege = ""
        self.last_reply: datetime | None = None
        self.last_error: str | None = None
        self.last_error_time: datetime | None = None

        self.input: int | None = None
        self.pip_input = 0
        self.standard: int | None = None
        self.video_mute: bool | None = None
        self.audio_mute: bool | None = None
        self.freeze: bool | None = None
        self.volume: int | None = None
        self.pip: bool | None = None
        self.pip_size: int | None = None
        self.resolution: int | None = None
        self.rate: int | None = None
        self.pictures: dict[int, Picture] = {}

    # -- listeners and lifecycle ---------------------------------------------

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for any state change; returns a remover."""
        self._listeners.append(callback)

        def remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return remove

    def _notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                _LOGGER.exception("Error in state listener")

    def _set_available(self, available: bool) -> None:
        if self.available != available:
            self.available = available
            self._notify()

    def _record_error(self, message: str) -> None:
        self.last_error = message
        self.last_error_time = datetime.now(UTC)

    @property
    def refresh_interval(self) -> float:
        """Return the seconds between full reads."""
        return self._refresh_interval

    async def async_start(self) -> None:
        """Start the background task."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def async_wait_ready(self, timeout: float) -> bool:
        """Wait for the first full read of the unit; False on timeout."""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def async_stop(self) -> None:
        """Stop and close everything."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for job in list(self._background) + ([self._settle_task] if self._settle_task else []):
            job.cancel()
        link, self._link = self._link, None
        if link is not None:
            await link.close()

    def _spawn(self, coro: Any) -> None:
        async def runner() -> None:
            try:
                await coro
            except ExtronError as err:
                _LOGGER.debug("Follow-up read failed: %s", err)

        task = asyncio.create_task(runner())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # -- requests ----------------------------------------------------------------

    async def _request(self, wire: str, expect: str, timeout: float = READ_TIMEOUT) -> str:
        """Send one command; return the line that matches ``expect`` (an error code raises)."""
        link = self._link
        if link is None:
            raise ExtronConnectionError("Not connected")
        pattern = re.compile(rf"^(?:{expect}|{_ANY_ERROR})$")
        async with self._lock:
            future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            self._waiter = (pattern, future)
            try:
                await link.write(wire)
                try:
                    line = await asyncio.wait_for(future, timeout)
                except TimeoutError:
                    self._record_error("No reply from the unit")
                    # A late reply would put every later answer out of step: start again.
                    self._spawn(self._drop_link())
                    raise ExtronTimeoutError("No reply from the unit") from None
            except ExtronConnectionError:
                self._spawn(self._drop_link())
                raise
            finally:
                self._waiter = None
        self.last_reply = datetime.now(UTC)
        code = error_code(line)
        if code is not None:
            error = Extron1508CommandError(code)
            self._record_error(str(error))
            raise error
        return line

    async def _drop_link(self) -> None:
        link = self._link
        if link is not None:
            await link.close()

    def _on_line(self, line: str) -> None:
        """Give a line to the command waiting for it, or treat it as an announcement."""
        line = line.strip()
        if not line:
            return
        waiter = self._waiter
        if waiter is not None and not waiter[1].done() and waiter[0].match(line):
            waiter[1].set_result(line)
            return
        if _ANNOUNCE.match(line):
            self._settle()

    def _settle(self) -> None:
        """Read the state again once the unit has been quiet for a moment."""
        if self._settle_task is not None:
            self._settle_task.cancel()

        async def later() -> None:
            await asyncio.sleep(SETTLE_SECONDS)
            with contextlib.suppress(ExtronError):
                await self._read_state()

        self._settle_task = asyncio.create_task(later())

    def _check_writable(self) -> None:
        if self.read_only:
            raise ExtronReadOnlyError("This unit is set to read-only")

    # -- changes ---------------------------------------------------------------------

    async def async_select_input(self, number: int) -> None:
        """Select the input shown on the output (video and audio together)."""
        self._check_writable()
        if not 1 <= number <= INPUTS:
            raise ValueError(f"Input {number} is not on this unit")
        await self._request(f"{number}!", r"Chn\d+", SELECT_TIMEOUT)
        self.input = number
        self._notify()
        # Mutes and freeze can change by themselves with the input.
        await self._read_state()

    async def async_set_mute(self, *, video: bool, muted: bool) -> None:
        """Mute or unmute the video or the audio."""
        self._check_writable()
        await self._request(
            f"{int(muted)}{'B' if video else 'Z'}", r"Vmt[01]" if video else r"Amt[01]"
        )
        if video:
            self.video_mute = muted
        else:
            self.audio_mute = muted
        self._notify()

    async def async_set_freeze(self, frozen: bool) -> None:
        """Freeze or release the picture."""
        self._check_writable()
        await self._request(f"{int(frozen)}F", r"Frz[01]")
        self.freeze = frozen
        self._notify()

    async def async_set_volume(self, value: int) -> None:
        """Set the output volume (0 to 100)."""
        self._check_writable()
        if not MIN_VOLUME <= value <= MAX_VOLUME:
            raise ValueError(f"Volume must be {MIN_VOLUME} to {MAX_VOLUME}")
        await self._request(f"{value}V", r"Vol\d+")
        self.volume = value
        self._notify()

    async def async_set_pip(self, on: bool) -> None:
        """Turn picture-in-picture on or off."""
        self._check_writable()
        await self._request(f"{int(on)}*5#", r"Pip[01]")
        self.pip = on
        self._notify()
        await self._read_state()

    async def async_set_pip_size(self, size: int) -> None:
        """Set the picture-in-picture window size (0 to 5)."""
        self._check_writable()
        if size not in PIP_SIZES:
            raise ValueError("The picture-in-picture size must be 0 to 5")
        await self._request(f"{size}*4#", r"Pmd\d")
        self.pip_size = size
        self._notify()

    async def async_set_output(self, resolution: int, rate: int) -> None:
        """Set the output resolution and refresh rate."""
        self._check_writable()
        if resolution not in RESOLUTIONS:
            raise ValueError("That output resolution is not offered by this unit")
        if rate not in VALID_RATES[resolution]:
            allowed = ", ".join(RATES[r] for r in VALID_RATES[resolution])
            raise ValueError(f"{RESOLUTIONS[resolution]} is only available at {allowed}")
        await self._request(f"{resolution}*{rate}=", r"Rte\d+\*\d+", SELECT_TIMEOUT)
        self.resolution, self.rate = resolution, rate
        self._notify()

    def _picture(self, number: int | None = None) -> Picture:
        number = number if number is not None else self.input
        if number is None:
            raise ExtronConnectionError("The selected input is not known yet")
        return self.pictures.setdefault(number, Picture())

    async def async_set_picture(self, key: str, value: int) -> None:
        """Set a picture control for the selected input."""
        self._check_writable()
        if key == "gain":
            if not MIN_GAIN <= value <= MAX_GAIN:
                raise ValueError(f"Input gain must be {MIN_GAIN} to +{MAX_GAIN} dB")
            await self._request(
                f"{abs(value)}{'G' if value >= 0 else 'g'}", r"Aud[+-]?\d+"
            )
        elif key == "aspect":
            if value not in ASPECTS:
                raise ValueError("The aspect ratio must be 4:3 or 16:9")
            await self._request(f"{value}*9#", r"Asp[01]")
        else:
            _read, setter, tag, low, high = PICTURE[key]
            if not low <= value <= high:
                raise ValueError(f"{key.capitalize()} must be {low} to {high}")
            await self._request(f"{value}{setter}", rf"{tag}\d+")
        self._picture().values[key] = value
        self._notify()

    # -- reads -------------------------------------------------------------------------------

    async def _read_number(self, wire: str, pattern: re.Pattern[str]) -> int | None:
        """Read one value; None if the unit says it does not apply (an error reply)."""
        try:
            line = await self._request(wire, pattern.pattern[1:-1])
        except Extron1508CommandError:
            return None
        match = pattern.match(line)
        return int(match.group(1)) if match else None

    async def _read_state(self) -> None:
        """Read the output side and the selected input's picture controls."""
        line = await self._request("I", r"Chn\d+\s+Pch\d+\s+Std\d+")
        status = parse_in_status(line)
        if status is not None:
            self.input, self.pip_input, self.standard = status
        value = await self._read_number("B", _RE_NUMBER["mute_video"])
        self.video_mute = None if value is None else bool(value)
        value = await self._read_number("Z", _RE_NUMBER["mute_audio"])
        self.audio_mute = None if value is None else bool(value)
        value = await self._read_number("F", _RE_NUMBER["freeze"])
        self.freeze = None if value is None else bool(value)
        self.volume = await self._read_number("V", _RE_NUMBER["volume"])
        value = await self._read_number("5#", _RE_NUMBER["pip"])
        self.pip = None if value is None else bool(value)
        self.pip_size = await self._read_number("4#", _RE_NUMBER["pip_size"])
        try:
            line = await self._request("=", r"Rte\d+\*\d+")
        except Extron1508CommandError:
            pass
        else:
            match = _RE_RATE.match(line)
            if match:
                self.resolution, self.rate = int(match.group(1)), int(match.group(2))
        picture = self._picture(self.input) if self.input else None
        if picture is not None:
            for key, (reader, _setter, tag, _low, _high) in PICTURE.items():
                picture.values[key] = await self._read_number(
                    reader, re.compile(rf"^{tag}(\d+)$")
                )
            picture.values["gain"] = await self._read_number("G", _RE_GAIN)
            picture.values["aspect"] = await self._read_number("9#", _RE_ASPECT)
        self._notify()

    # -- background task -------------------------------------------------------------------------

    async def _run(self) -> None:
        """Connect, read, listen and keep alive; reconnect forever if the link drops."""
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - whatever goes wrong, start again
                _LOGGER.debug("Connection to %s:%s lost: %s", self._host, self._port, err)
            self._set_available(False)
            await asyncio.sleep(self._reconnect_delay)

    async def _session(self) -> None:
        link = ExtronLink(self._host, self._port)
        reader: asyncio.Task | None = None
        try:
            await link.open()
            self._link = link
            self.privilege = link.privilege

            async def listen() -> None:
                while True:
                    line = await link.read_line(None)
                    if line is not None:
                        self._on_line(line)

            reader = asyncio.create_task(listen())
            for wire, key in (("N", "part_number"), ("Q", "firmware")):
                self.banner[key] = (await self._request(wire, r"[\d.\-]+")).strip()
            self.banner["product"] = "IN1508"
            await self._read_state()
            self._ready.set()
            self._set_available(True)
            while True:
                done, _pending = await asyncio.wait(
                    {reader}, timeout=self._refresh_interval
                )
                if done:
                    reader.result()
                    raise ExtronConnectionError("Connection closed by the unit")
                await self._read_state()
        finally:
            if reader is not None:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader
            self._link = None
            await link.close()
