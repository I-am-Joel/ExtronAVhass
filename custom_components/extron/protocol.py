"""Extron SIS (Simple Instruction Set) client for CrossPoint and MAV matrix switchers.

Two Telnet connections are kept open to the unit:

* a command connection, left in the unit's plain reply mode (verbose mode 0), used for every
  question and every change;
* an event connection, put in verbose mode 3, which only listens: the unit reports changes
  made anywhere (front panel, web page, other controllers) on it.

Both are kept alive with a harmless ``Q`` (the unit drops a silent connection after about
five minutes).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import socket
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any

_LOGGER = logging.getLogger(__name__)

ESC = "\x1b"
IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240

KEEPALIVE_SECONDS = 45
QUARANTINE_AFTER = 2  # readings that got no answer this many times in a row are left out
MAX_PRESETS = 32
MIN_VOLUME, MAX_VOLUME = 0, 64
MIN_GAIN, MAX_GAIN = -18, 24
MIN_OFFSET_MINUTES, MAX_OFFSET_MINUTES = -12 * 60, 14 * 60  # time zone range the unit accepts

ERROR_TEXT = {
    1: "invalid input number",
    10: "invalid command",
    11: "invalid preset number or preset not saved",
    12: "invalid output number",
    13: "value out of range or invalid command",
    14: "not valid for this configuration",
    17: "system timed out",
    22: "unit busy",
    24: "privilege violation",
    25: "device not present",
    26: "maximum connections exceeded",
    27: "invalid event number",
    28: "bad filename or file not found",
}


class ExtronError(Exception):
    """Base class for errors from this module."""


class ExtronConnectionError(ExtronError):
    """The unit could not be reached or the connection dropped."""


class ExtronTimeoutError(ExtronError):
    """The unit did not answer in time."""


class ExtronAuthError(ExtronError):
    """The unit wants a password and none (or a wrong one) was supplied."""


class ExtronCommandError(ExtronError):
    """The unit answered with an error code."""

    def __init__(self, code: int) -> None:
        """Remember the code."""
        self.code = code
        super().__init__(f"Unit reported E{code:02d} ({ERROR_TEXT.get(code, 'error')})")


class ExtronReadOnlyError(ExtronError):
    """A change was attempted while the unit is set to read-only."""


# ---------------------------------------------------------------------------
# Parsing helpers (pure functions, easy to test)
# ---------------------------------------------------------------------------

_ERROR_RE = re.compile(r"^E(\d\d)$")
_SIZE_RE = re.compile(r"V(\d+)X(\d+)\s+A(\d+)X(\d+)")
_BANNER_RE = re.compile(
    r"Extron Electronics,\s*(.+?),\s*V([\d.]+),\s*(\d{2}-\d{3}-\d{2})"
)


def error_code(line: str) -> int | None:
    """Return n for a line such as ``E13``, else None."""
    match = _ERROR_RE.match(line.strip())
    return int(match.group(1)) if match else None


def parse_size(text: str) -> tuple[int, int] | None:
    """Return (inputs, outputs) from a reply such as ``V24X24 A24X24``."""
    match = _SIZE_RE.search(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def parse_banner(text: str) -> dict[str, str]:
    """Pick the product, firmware and part number out of the copyright message."""
    match = _BANNER_RE.search(text)
    if not match:
        return {}
    return {
        "product": match.group(1),
        "firmware": match.group(2),
        "part_number": match.group(3),
    }


def parse_tie_list(line: str) -> list[int | None]:
    """Parse ``02 02 -- 15 ... Vid`` into [2, 2, None, 15, ...]."""
    values: list[int | None] = []
    for token in line.split():
        if token == "--":
            values.append(None)
        elif token.isdigit():
            values.append(int(token))
    return values


def parse_gain(line: str) -> int | None:
    """Parse ``+05`` or ``-18``."""
    text = line.strip()
    return int(text) if re.fullmatch(r"[+-]\d+", text) else None


STATUS_KEYS = (
    "v5",
    "v3_3",
    "v2_5",
    "fan_v",
    "v_m5",
    "v_m12",
    "v12",
    "temp_f",
    "fan1",
    "fan2",
    "psu1",
    "psu2",
)
# The shorter list the MAV Plus 1616 gives (identified by the size of each value).
STATUS_KEYS_SHORT = ("v3_3", "v5", "v_m5", "v15", "v_m15", "temp_f")


def parse_status(line: str) -> dict[str, float]:
    """Parse the ``S`` reply: six values (MAV Plus 1616) or twelve (MAV Plus 2424).

    Only the values the unit reports are returned (nothing for a list of any other length).
    """
    tokens = line.split()
    keys = {len(STATUS_KEYS): STATUS_KEYS, len(STATUS_KEYS_SHORT): STATUS_KEYS_SHORT}.get(
        len(tokens)
    )
    if keys is None:
        return {}
    try:
        numbers = [float(t) for t in tokens]
    except ValueError:
        return {}
    return dict(zip(keys, numbers, strict=True))


# Unit information, read with escape commands. The free-space reply is last because it is
# followed by a blank line.
INFO_COMMANDS = (
    ("unit_name", "CN"),
    ("ip", "CI"),
    ("subnet", "CS"),
    ("gateway", "CG"),
    ("mac", "CH"),
    ("dhcp", "DH"),
    ("connections", "CC"),
    ("clock", "CT"),
    ("gmt_offset", "CZ"),
    ("dst", "CX"),
    ("timeout", "0TC"),
    ("serial", "1CP"),
    ("free_space", "DF"),
)


def parse_info(key: str, line: str) -> str | int | bool | None:
    """Turn one information reply into a useful value (None if it is not understood)."""
    text = line.strip()
    if not text or error_code(text) is not None:
        return None
    if key == "dhcp":
        return text == "1" if text in ("0", "1") else None
    if key == "gmt_offset":
        return normalise_offset(text)
    if key == "dst":
        return int(text) if text in ("0", "1", "2", "3") else None
    if key in ("connections", "timeout"):
        if not text.isdigit():
            return None
        return int(text) * (10 if key == "timeout" else 1)
    if key == "free_space":
        match = re.match(r"(\d+)\s+Bytes Left", text)
        return int(match.group(1)) if match else None
    return text


def format_offset(minutes: int) -> str:
    """Write a time zone offset the way the unit takes it: H.MM, a minus sign only when negative.

    The unit's manual gives a period between hours and minutes. A leading plus sign is left out
    on purpose: a unit that is given one applies it but only answers at the next command.
    """
    sign = "-" if minutes < 0 else ""
    hours, mins = divmod(abs(minutes), 60)
    return f"{sign}{hours}.{mins:02d}"


def normalise_offset(text: str) -> str | None:
    """Turn the unit's offset reading (00:00, +01:00, -05:00) into +HH:MM or -HH:MM, or None."""
    match = re.fullmatch(r"([+-]?)(\d{1,2})[:.](\d{2})", text.strip())
    if not match:
        return None
    sign = "-" if match.group(1) == "-" and (int(match.group(2)) or int(match.group(3))) else "+"
    return f"{sign}{int(match.group(2)):02d}:{match.group(3)}"


_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}
_CLOCK_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\w*\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})")


def parse_clock(text: str, tz: tzinfo) -> datetime | None:
    """Turn the unit's clock reading (``Fri, 09 Oct 2020 08:05:04``) into a datetime in ``tz``, or None."""
    match = _CLOCK_RE.search(text)
    if not match:
        return None
    month = _MONTHS.get(match.group(2).lower())
    if month is None:
        return None
    try:
        return datetime(
            int(match.group(3)),
            month,
            int(match.group(1)),
            int(match.group(4)),
            int(match.group(5)),
            int(match.group(6)),
            tzinfo=tz,
        )
    except ValueError:
        return None


def parse_digits(line: str, count: int) -> list[int] | None:
    """Parse a run of single digits (mutes, groups), tolerating commas, into ``count`` numbers."""
    digits = re.sub(r"[^0-9]", "", line)
    if len(digits) < count:
        return None
    return [int(ch) for ch in digits[:count]]


@dataclass
class Preset:
    """What the unit holds for one preset."""

    saved: bool
    name: str = ""


_RE_VMUTE = re.compile(r"^Vmt(\d+)\*([01])$")
_RE_AMUTE = re.compile(r"^Amt(\d+)\*([01])$")
_RE_VMUTE_ALL = re.compile(r"^Vmt([01])$")
_RE_AMUTE_ALL = re.compile(r"^Amt([01])$")
_RE_ZONE = re.compile(r"^Ipz(.+)$")
_RE_DST = re.compile(r"^Ipx([0-3])$")
_RE_CLOCK = re.compile(r"^Ipt (.+)$")
_RE_VOLUME = re.compile(r"^Out(\d+)\s+Vol(\d+)$")
_RE_GAIN = re.compile(r"^In(\d+)\s+Aud([+-]\d+)$")
_RE_TIE = re.compile(r"^Out(\d+)\s+In(\d+)\s+(Vid|Aud|All|RGB)$")
_RE_TIE_ALL = re.compile(r"^In(\d+)\s+(Vid|Aud|All|RGB)$")
_RE_LOCK = re.compile(r"^Exe([012])$")
_RE_NAME_IN = re.compile(r"^Nmi(\d+),(.*)$")
_RE_NAME_OUT = re.compile(r"^Nmo(\d+),(.*)$")
_RE_NAME_PRESET = re.compile(r"^Nmg(\d+),(.*)$")
_RE_PRESET_EVENT = re.compile(r"^(Spr|Zpg)(\d+)$")
_RE_RECALL = re.compile(r"^Rpr(\d+)$")


# The lines a unit volunteers (and the tagged answers to changes, which look the same).
_ANNOUNCEMENTS = (
    _RE_VMUTE,
    _RE_AMUTE,
    _RE_VMUTE_ALL,
    _RE_AMUTE_ALL,
    _RE_ZONE,
    _RE_DST,
    _RE_CLOCK,
    _RE_VOLUME,
    _RE_GAIN,
    _RE_TIE,
    _RE_TIE_ALL,
    _RE_LOCK,
    _RE_NAME_IN,
    _RE_NAME_OUT,
    _RE_NAME_PRESET,
    _RE_PRESET_EVENT,
    _RE_RECALL,
    re.compile(r"^(?:Qik|Ver\d+\*[\d.]+)$"),
)
_TIE_LIST = re.compile(r"^(?:\d\d|--)(?: (?:\d\d|--))* (?:Vid|Aud)$")
_NAME_WIRE = re.compile(rf"^{ESC}\d+N[IOG]\r$")
_SHAPES = {
    f"{ESC}VM\r": re.compile(r"^\d+$"),
    f"{ESC}I\r": re.compile(r"^[0-4,]+$"),
    f"{ESC}O\r": re.compile(r"^[0-4,]+$"),
    "X": re.compile(r"^[012]$"),
    "Q": re.compile(r"^[\d.]+$"),
    "N": re.compile(r"^\d\d-\d\d\d-\d\d$"),
    "S": re.compile(r"^[-\d. ]+$"),
}


def is_announcement(line: str) -> bool:
    """Whether a line looks like something a unit volunteers rather than the answer to a question."""
    text = line.strip()
    return any(pattern.match(text) for pattern in _ANNOUNCEMENTS)


def expected_shape(wire: str) -> re.Pattern[str] | None:
    """What a plain read's answer looks like (None for changes, whose answers are tagged lines)."""
    if wire.startswith(ESC) and wire.endswith("VC\r"):
        return _TIE_LIST
    if wire in _SHAPES:
        return _SHAPES[wire]
    if re.fullmatch(r"\d+V", wire):
        return re.compile(r"^\d+$")
    if re.fullmatch(r"\d+G", wire):
        return re.compile(r"^[+-]\d+$")
    return None


def _make_name(raw: str, fallback: str) -> str:
    name = raw.strip()
    return name if name and error_code(name) is None else fallback


# ---------------------------------------------------------------------------
# One Telnet connection
# ---------------------------------------------------------------------------


class ExtronLink:
    """A single Telnet connection to the unit (handles Telnet negotiation and login)."""

    def __init__(self, host: str, port: int, password: str = "") -> None:
        """Initialise (nothing connects until open)."""
        self._host = host
        self._port = port
        self._password = password
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._carry = b""
        self._text = ""
        self.banner: dict[str, str] = {}
        self.privilege = ""

    async def open(self) -> None:
        """Connect, read the copyright message and log in if asked to."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), 10
            )
        except (OSError, TimeoutError) as err:
            raise ExtronConnectionError(str(err) or "connection timed out") from err
        sock = self._writer.get_extra_info("socket")
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        text = await self._gather(quiet=0.5, maximum=4.0)
        self.banner = parse_banner(text)
        if re.search(r"password", text, re.IGNORECASE):
            if not self._password:
                await self.close()
                raise ExtronAuthError("The unit asks for a password")
            await self.write(self._password + "\r")
            reply = await self._gather(quiet=0.5, maximum=4.0)
            if "Login" in reply:
                self.privilege = "Administrator" if "Administrator" in reply else "User"
            else:
                await self.close()
                raise ExtronAuthError("The unit did not accept the password")
        else:
            self.privilege = "Administrator"

    def _decode(self, data: bytes) -> str:
        """Remove Telnet negotiation bytes (politely refusing every option) and return text."""
        data = self._carry + data
        self._carry = b""
        out = bytearray()
        i, n = 0, len(data)
        while i < n:
            byte = data[i]
            if byte != IAC:
                out.append(byte)
                i += 1
                continue
            if i + 1 >= n:
                self._carry = data[i:]
                break
            cmd = data[i + 1]
            if cmd == IAC:
                out.append(IAC)
                i += 2
            elif cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= n:
                    self._carry = data[i:]
                    break
                option = data[i + 2]
                if self._writer is not None and cmd in (DO, WILL):
                    refusal = WONT if cmd == DO else DONT
                    with contextlib.suppress(OSError, RuntimeError):
                        self._writer.write(bytes([IAC, refusal, option]))
                i += 3
            elif cmd == SB:
                end = data.find(bytes([IAC, SE]), i + 2)
                if end < 0:
                    self._carry = data[i:]
                    break
                i = end + 2
            else:
                i += 2
        return out.decode("latin-1")

    async def _fill(self, timeout: float | None) -> bool:
        """Read more data into the buffer. Returns False on timeout; raises if the unit hung up."""
        if self._reader is None:
            raise ExtronConnectionError("Not connected")
        try:
            chunk = await asyncio.wait_for(self._reader.read(2048), timeout)
        except TimeoutError:
            return False
        except OSError as err:
            raise ExtronConnectionError(str(err)) from err
        if not chunk:
            raise ExtronConnectionError("Connection closed by the unit")
        self._text += self._decode(chunk)
        return True

    async def _gather(self, quiet: float, maximum: float) -> str:
        """Collect whatever arrives until the line has been quiet for ``quiet`` seconds."""
        loop = asyncio.get_running_loop()
        end = loop.time() + maximum
        while loop.time() < end:
            if not await self._fill(min(quiet, max(0.01, end - loop.time()))):
                break
        text, self._text = self._text, ""
        return text

    async def read_line(
        self, timeout: float | None, keep_blank: bool = False
    ) -> str | None:
        """Return the next line (blank ones skipped unless ``keep_blank``), or None on timeout."""
        loop = asyncio.get_running_loop()
        end = None if timeout is None else loop.time() + timeout
        while True:
            while "\n" in self._text:
                line, self._text = self._text.split("\n", 1)
                line = line.rstrip("\r")
                if line.strip() or keep_blank:
                    return line
            remaining = None if end is None else end - loop.time()
            if remaining is not None and remaining <= 0:
                return None
            if not await self._fill(remaining):
                return None

    def drain(self) -> None:
        """Forget anything received but not yet read."""
        self._text = ""

    async def write(self, text: str) -> None:
        """Send text exactly as given."""
        if self._writer is None or self._writer.is_closing():
            raise ExtronConnectionError("Not connected")
        try:
            self._writer.write(text.encode("latin-1"))
            await self._writer.drain()
        except OSError as err:
            raise ExtronConnectionError(str(err)) from err

    async def close(self) -> None:
        """Close the connection."""
        writer, self._writer, self._reader = self._writer, None, None
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError, asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 2)


def parse_audio_size(text: str) -> tuple[int, int] | None:
    """Return (audio inputs, audio outputs) from a reply such as ``V24X24 A24X24``."""
    match = _SIZE_RE.search(text)
    if not match:
        return None
    return int(match.group(3)), int(match.group(4))


async def _ask(link: ExtronLink, wire: str) -> str:
    """Send one command during setup and return the reply line."""
    await link.write(wire)
    line = await link.read_line(3)
    if line is None:
        raise ExtronTimeoutError("No answer from the unit")
    return line.strip()


async def async_probe(host: str, port: int, password: str = "") -> dict[str, Any]:
    """Connect once and describe the unit (used by the setup screens).

    Understands matrix switchers (over Ethernet, or over a serial bridge that gives no
    copyright message) and the IN1508 scaling switcher (serial).
    """
    from .in1508 import async_describe_in1508, parse_in_status

    link = ExtronLink(host, port, password)
    await link.open()
    try:
        info: dict[str, Any] = dict(link.banner)
        info["privilege"] = link.privilege
        size_line = await _ask(link, "I")
        if parse_in_status(size_line) is not None:
            described = await async_describe_in1508(link)
            if described is None:
                raise ExtronError("Something answered like an IN1508 but is not one")
            return {**info, **described}
        info["size"] = size_line
        size = parse_size(size_line)
        if size is None:
            raise ExtronError(f"Unexpected size reply: {size_line}")
        info["inputs"], info["outputs"] = size
        audio = parse_audio_size(size_line)
        info["audio"] = bool(audio and audio[1] > 0)
        info["unit_name"] = await _ask(link, f"{ESC}CN\r")
        if not info.get("part_number"):
            # A serial connection gives no copyright message: ask for the part number and version.
            part = await _ask(link, "N")
            version = await _ask(link, "Q")
            if error_code(part) is None and re.fullmatch(r"\d{2}-\d{3}-\d{2}", part):
                info["part_number"] = part
            if error_code(version) is None and re.fullmatch(r"[\d.]+", version):
                info["firmware"] = version
        return info
    finally:
        await link.close()


# ---------------------------------------------------------------------------
# The client: keeps both connections, tracks the unit's state
# ---------------------------------------------------------------------------


class ExtronClient:
    """Keeps a unit's state up to date and carries out changes."""

    def __init__(
        self,
        host: str,
        port: int,
        inputs: int,
        outputs: int,
        *,
        password: str = "",
        read_only: bool = False,
        audio: bool = True,
        refresh_interval: float = 60.0,
        reconnect_delay: float = 5.0,
        request_timeout: float = 3.0,
    ) -> None:
        """Initialise (nothing connects until async_start)."""
        self._host = host
        self._port = port
        self._password = password
        self.read_only = read_only
        self.audio_present = audio
        self.inputs = inputs
        self.outputs = outputs
        self._refresh_interval = refresh_interval
        self._reconnect_delay = reconnect_delay
        self._request_timeout = request_timeout

        self._task: asyncio.Task | None = None
        self._cmd: ExtronLink | None = None
        self._evt: ExtronLink | None = None
        self._lock = asyncio.Lock()
        self._strikes: dict[str, int] = {}
        self.quarantined: set[str] = set()
        self._single = False  # one stream only (a serial unit): announcements share it with the answers
        self._inbox: asyncio.Queue[str] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._refresh_event = asyncio.Event()
        self._ready = asyncio.Event()
        self._background: set[asyncio.Task] = set()

        self.available = False
        self.video: dict[int, int] = {}
        self.audio: dict[int, int] = {}
        self.video_mute: dict[int, bool] = {}
        self.audio_mute: dict[int, bool] = {}
        self.volume: dict[int, int] = {}
        self.gain: dict[int, int] = {}
        self.in_names: dict[int, str] = {}
        self.out_names: dict[int, str] = {}
        self.lock_mode: int | None = None
        self.groups_in: list[int] | None = None
        self.groups_out: list[int] | None = None
        self.presets: dict[int, Preset] = {}
        self.preset_count = 0
        self.status: dict[str, float] = {}
        self.info: dict[str, str | int | bool] = {}
        self.banner: dict[str, str] = {}
        self.privilege = ""
        self.last_reply: datetime | None = None
        self.last_error: str | None = None
        self.last_error_time: datetime | None = None

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
            _LOGGER.debug("Unit %s:%s available=%s", self._host, self._port, available)
            self._notify()

    def _record_error(self, message: str) -> None:
        self.last_error = message
        self.last_error_time = datetime.now(UTC)

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
        for job in list(self._background):
            job.cancel()
        await self._close_links()

    async def _close_links(self) -> None:
        cmd, evt, self._cmd, self._evt = self._cmd, self._evt, None, None
        for link in (cmd, evt):
            if link is not None:
                await link.close()

    @property
    def refresh_interval(self) -> float:
        """Return the seconds between full refreshes."""
        return self._refresh_interval

    def async_request_refresh(self) -> None:
        """Ask for a full refresh as soon as possible."""
        self._refresh_event.set()

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run a follow-up read in the background (errors are only logged)."""

        async def runner() -> None:
            try:
                await coro
            except ExtronError as err:
                _LOGGER.debug("Follow-up read failed: %s", err)

        task = asyncio.create_task(runner())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # -- requests ------------------------------------------------------------------

    def _skip(self, wire: str, skip_with: str | None) -> bool:
        """Whether a read that has gone unanswered repeatedly is left out (answered with ``skip_with``)."""
        if skip_with is None or self._strikes.get(wire, 0) < QUARANTINE_AFTER:
            return False
        if wire not in self.quarantined:
            self.quarantined.add(wire)
            _LOGGER.warning(
                "Command %r got no reply %d times in a row: leaving it out of the readings",
                wire,
                QUARANTINE_AFTER,
            )
        return True

    async def _request_many(
        self,
        wires: list[str],
        settle: frozenset[str] = frozenset(),
        skip_with: str | None = None,
    ) -> list[str]:
        """Send commands one at a time and return one reply line for each.

        A read that has gone unanswered QUARANTINE_AFTER times in a row (over reconnections) is
        not sent again and is answered with ``skip_with``, so one command a unit never answers
        cannot keep it unavailable for good. Only readings pass ``skip_with``.

        One at a time (never several in one write) is the way every command was tested
        against a real unit, and a reply takes only about 15 ms.
        """
        link = self._cmd
        if link is None:
            raise ExtronConnectionError("Not connected")
        if self._single:
            return await self._request_many_single(wires, skip_with)
        async with self._lock:
            lines: list[str] = []
            for wire in wires:
                if self._skip(wire, skip_with):
                    lines.append(skip_with or "")
                    continue
                link.drain()
                try:
                    await link.write(wire)
                    line = await link.read_line(self._request_timeout, keep_blank=True)
                except ExtronConnectionError:
                    # The unit hung up (it does that to a silent connection): start again.
                    self._spawn(self._close_links())
                    raise
                if line is None:
                    self._strikes[wire] = self._strikes.get(wire, 0) + 1
                    self._record_error("No reply from the unit")
                    # A late reply would put every later answer out of step: start again.
                    self._spawn(self._close_links())
                    raise ExtronTimeoutError("No reply from the unit")
                self._strikes.pop(wire, None)
                lines.append(line)
                if wire in settle:
                    # This reply is followed by a blank line: swallow it so it is not
                    # mistaken for the answer to the next command.
                    await link.read_line(0.2, keep_blank=True)
        self.last_reply = datetime.now(UTC)
        return lines

    async def _request_many_single(
        self, wires: list[str], skip_with: str | None = None
    ) -> list[str]:
        """As ``_request_many`` on a single stream: lines the unit volunteers are set aside.

        A reader task hands every line either to the question waiting for its answer or, when
        nobody is waiting, to the listener for changes made elsewhere.
        """
        link = self._cmd
        if link is None:
            raise ExtronConnectionError("Not connected")
        async with self._lock:
            lines: list[str] = []
            for wire in wires:
                if self._skip(wire, skip_with):
                    lines.append(skip_with or "")
                    continue
                inbox: asyncio.Queue[str] = asyncio.Queue()
                self._inbox = inbox
                try:
                    await link.write(wire)
                    line = await self._next_answer(inbox, wire)
                except ExtronConnectionError:
                    self._spawn(self._close_links())
                    raise
                finally:
                    self._inbox = None
                    while not inbox.empty():
                        self._dispatch(inbox.get_nowait())
                if line is None:
                    self._strikes[wire] = self._strikes.get(wire, 0) + 1
                    self._record_error("No reply from the unit")
                    self._spawn(self._close_links())
                    raise ExtronTimeoutError("No reply from the unit")
                self._strikes.pop(wire, None)
                lines.append(line)
        self.last_reply = datetime.now(UTC)
        return lines

    async def _next_answer(self, inbox: asyncio.Queue[str], wire: str) -> str | None:
        """Wait for the answer to ``wire``, passing anything else the unit says to the listener."""
        shape = expected_shape(wire)
        loop = asyncio.get_running_loop()
        end = loop.time() + self._request_timeout
        while True:
            try:
                line = await asyncio.wait_for(inbox.get(), max(0.01, end - loop.time()))
            except TimeoutError:
                return None
            if not line.strip() and not _NAME_WIRE.match(wire):
                continue  # the blank line some replies end with
            if shape is None or shape.match(line.strip()) or error_code(line) is not None:
                return line
            if is_announcement(line):
                self._dispatch(line)
                continue
            return line

    async def _single_reader(self, link: ExtronLink) -> None:
        """Read forever, giving each line to the waiting question or to the change listener."""
        while True:
            line = await link.read_line(None, keep_blank=True)
            if line is None:
                continue
            inbox = self._inbox
            if inbox is not None:
                inbox.put_nowait(line)
            elif line.strip():
                self._dispatch(line)

    async def _request(self, wire: str) -> str:
        """Send one command and return its reply, raising if the unit reports an error."""
        (line,) = await self._request_many([wire])
        code = error_code(line)
        if code is not None:
            error = ExtronCommandError(code)
            self._record_error(str(error))
            raise error
        return line

    def _check_writable(self) -> None:
        if self.read_only:
            raise ExtronReadOnlyError("This unit is set to read-only")

    def _layers(self, video: bool, audio: bool) -> tuple[bool, bool]:
        """A unit with no audio side only ever has video to tie."""
        return video, audio and self.audio_present

    def source_values(self) -> set[int | None]:
        """Return the inputs feeding the outputs (None for an output not read yet)."""
        outputs = range(1, self.outputs + 1)
        values = {self.video.get(o) for o in outputs}
        if self.audio_present:
            values |= {self.audio.get(o) for o in outputs}
        return values

    def _check_output(self, output: int) -> None:
        if not 1 <= output <= self.outputs:
            raise ValueError(f"Output {output} is not on this unit")

    def _check_input(self, number: int, allow_zero: bool = False) -> None:
        if not (1 if not allow_zero else 0) <= number <= self.inputs:
            raise ValueError(f"Input {number} is not on this unit")

    # -- changes -----------------------------------------------------------------------

    async def async_set_tie(
        self, output: int, input_number: int, *, video: bool = True, audio: bool = True
    ) -> None:
        """Tie an input (0 = none) to an output for video, audio or both."""
        self._check_writable()
        self._check_output(output)
        self._check_input(input_number, allow_zero=True)
        video, audio = self._layers(video, audio)
        if not (video or audio):
            raise ValueError("Choose video, audio or both")
        symbol = "!" if video and audio else "%" if video else "$"
        await self._request(f"{input_number}*{output}{symbol}")
        if video:
            self.video[output] = input_number
        if audio:
            self.audio[output] = input_number
        self._notify()

    async def async_set_mute(self, output: int, *, video: bool, muted: bool) -> None:
        """Mute or unmute an output's video or audio."""
        self._check_writable()
        self._check_output(output)
        await self._request(f"{output}*{int(muted)}{'B' if video else 'Z'}")
        (self.video_mute if video else self.audio_mute)[output] = muted
        self._notify()

    async def async_set_volume(self, output: int, value: int) -> None:
        """Set an output's volume (0 to 64)."""
        self._check_writable()
        self._check_output(output)
        if not MIN_VOLUME <= value <= MAX_VOLUME:
            raise ValueError(f"Volume must be {MIN_VOLUME} to {MAX_VOLUME}")
        await self._request(f"{output}*{value}V")
        self.volume[output] = value
        self._notify()

    async def async_set_gain(self, input_number: int, value: int) -> None:
        """Set an input's audio level (-18 to +24 dB)."""
        self._check_writable()
        self._check_input(input_number)
        if not MIN_GAIN <= value <= MAX_GAIN:
            raise ValueError(f"Level must be {MIN_GAIN} to {MAX_GAIN} dB")
        symbol = "G" if value >= 0 else "g"
        await self._request(f"{input_number}*{abs(value)}{symbol}")
        self.gain[input_number] = value
        self._notify()

    async def async_set_lock(self, mode: int) -> None:
        """Set the front-panel lock (0 off, 1 or 2)."""
        self._check_writable()
        if mode not in (0, 1, 2):
            raise ValueError("Lock mode must be 0, 1 or 2")
        await self._request(f"{mode}X")
        self.lock_mode = mode
        self._notify()

    async def async_set_group(self, *, output: bool, number: int, group: int) -> None:
        """Put an input or output in group 1 to 4 (0 = no group). Groups only limit front-panel ties."""
        self._check_writable()
        groups = self.groups_out if output else self.groups_in
        if groups is None or not 1 <= number <= len(groups):
            raise ValueError("Grouping is not available for that number")
        if not 0 <= group <= 4:
            raise ValueError("Group must be 0 to 4")
        updated = list(groups)
        updated[number - 1] = group
        await self._request(
            f"{ESC}{''.join(str(g) for g in updated)}{'O' if output else 'I'}\r"
        )
        if output:
            self.groups_out = updated
        else:
            self.groups_in = updated
        self._notify()

    async def async_set_tie_all(
        self, input_number: int, *, video: bool = True, audio: bool = True
    ) -> None:
        """Tie one input (0 = none) to every output for video, audio or both."""
        self._check_writable()
        self._check_input(input_number, allow_zero=True)
        video, audio = self._layers(video, audio)
        if not (video or audio):
            raise ValueError("Choose video, audio or both")
        symbol = "!" if video and audio else "%" if video else "$"
        await self._request(f"{input_number}*{symbol}")
        for output in range(1, self.outputs + 1):
            if video:
                self.video[output] = input_number
            if audio:
                self.audio[output] = input_number
        self._notify()

    async def async_route(
        self,
        input_number: int,
        outputs: list[int],
        *,
        video: bool = True,
        audio: bool = True,
    ) -> None:
        """Tie one input (0 = none) to a chosen set of outputs, in a single command."""
        self._check_writable()
        self._check_input(input_number, allow_zero=True)
        video, audio = self._layers(video, audio)
        if not (video or audio):
            raise ValueError("Choose video, audio or both")
        chosen = sorted(set(outputs))
        if not chosen:
            raise ValueError("Choose at least one output")
        for output in chosen:
            self._check_output(output)
        if len(chosen) == self.outputs:
            await self.async_set_tie_all(input_number, video=video, audio=audio)
            return
        if len(chosen) == 1:
            await self.async_set_tie(chosen[0], input_number, video=video, audio=audio)
            return
        symbol = "!" if video and audio else "%" if video else "$"
        entries = "".join(f"{input_number}*{o}{symbol}" for o in chosen)
        reply = await self._request(f"{ESC}+Q{entries}\r")
        if reply.strip() != "Qik":
            raise ExtronError(f"Unexpected reply to a multiple tie: {reply.strip()}")
        # The unit only says "Qik" for this, so read the ties back.
        await self._refresh_ties()

    async def async_set_mute_all(self, *, video: bool, muted: bool) -> None:
        """Mute or unmute the video or the audio of every output."""
        self._check_writable()
        await self._request(f"{int(muted)}*{'B' if video else 'Z'}")
        table = self.video_mute if video else self.audio_mute
        for output in range(1, self.outputs + 1):
            table[output] = muted
        self._notify()

    async def async_set_clock(self, when: datetime) -> None:
        """Set the unit's clock (local time, as the unit shows it)."""
        self._check_writable()
        await self._request(f"{ESC}{when:%m/%d/%y %H:%M:%S}CT\r")
        await self._refresh_info()

    async def async_set_timezone(self, minutes: int) -> None:
        """Set the unit's time zone offset from GMT in minutes (-12:00 to +14:00)."""
        self._check_writable()
        if not MIN_OFFSET_MINUTES <= minutes <= MAX_OFFSET_MINUTES:
            raise ValueError("The time zone offset must be between -12:00 and +14:00")
        reply = await self._request(f"{ESC}{format_offset(minutes)}CZ\r")
        if not reply.strip().startswith("Ipz"):
            raise ExtronError(f"Unexpected reply to a time zone change: {reply.strip()}")
        await self._refresh_info()

    async def async_set_dst(self, rule: int) -> None:
        """Set the daylight saving rule: 0 off, 1 North America, 2 Europe, 3 Brazil."""
        self._check_writable()
        if rule not in (0, 1, 2, 3):
            raise ValueError("The daylight saving rule must be 0, 1, 2 or 3")
        reply = await self._request(f"{ESC}{rule}CX\r")
        if reply.strip() != f"Ipx{rule}":
            raise ExtronError(f"Unexpected reply to a daylight saving change: {reply.strip()}")
        await self._refresh_info()

    async def _reset(self, command: str, expected: str) -> None:
        """Send one of the milder reset commands. The unit does not announce these, so read it all again."""
        self._check_writable()
        reply = await self._request(f"{ESC}{command}\r")
        if reply.strip() != expected:
            raise ExtronError(f"Unexpected reply to the reset: {reply.strip()}")
        await self._refresh_core()

    async def async_reset_mutes(self) -> None:
        """Unmute the video and audio of every output (Esc ZZ)."""
        await self._reset("ZZ", "Zpz")

    async def async_reset_input_levels(self) -> None:
        """Set every audio input level back to 0 dB (Esc ZA)."""
        await self._reset("ZA", "Zpa")

    async def async_reset_output_volumes(self) -> None:
        """Set every audio output volume back to the unit's default (Esc ZV)."""
        await self._reset("ZV", "Zpv")

    def _check_preset(self, preset: int) -> None:
        if not 1 <= preset <= self.preset_count:
            raise ValueError(f"Preset {preset} does not exist on this unit")

    async def async_recall_preset(self, preset: int) -> None:
        """Recall a saved preset (ties only)."""
        self._check_writable()
        self._check_preset(preset)
        await self._request(f"{preset}.")
        await self._refresh_ties()

    async def async_save_preset(self, preset: int) -> None:
        """Save the current ties into a preset, overwriting whatever it held."""
        self._check_writable()
        self._check_preset(preset)
        await self._request(f"{preset},")
        await self.async_refresh_presets()

    async def async_clear_preset(self, preset: int) -> None:
        """Erase a preset."""
        self._check_writable()
        self._check_preset(preset)
        await self._request(f"{ESC}{preset}ZG\r")
        await self.async_refresh_presets()

    # -- reads -------------------------------------------------------------------------------

    def _apply_ties(self, kind: int, start: int, line: str) -> None:
        target = self.video if kind == 1 else self.audio
        for index, value in enumerate(parse_tie_list(line)):
            output = start + index
            if value is not None and output <= self.outputs:
                target[output] = value

    async def _refresh_ties(self) -> None:
        wires: list[tuple[int, int, str]] = []
        for kind in (1, 2) if self.audio_present else (1,):
            wires.append((kind, 1, f"{ESC}0*1*{kind}VC\r"))
            if self.outputs > 16:
                wires.append((kind, 17, f"{ESC}0*17*{kind}VC\r"))
        lines = await self._request_many([w for _, _, w in wires], skip_with="E10")
        for (kind, start, _), line in zip(wires, lines, strict=True):
            if error_code(line) is None:
                self._apply_ties(kind, start, line)
        self._notify()

    async def _refresh_core(self) -> None:
        """Read ties, mutes, volumes, levels, the lock and the groups."""
        kinds = (1, 2) if self.audio_present else (1,)
        wires = []
        for kind in kinds:
            wires.append(f"{ESC}0*1*{kind}VC\r")
            if self.outputs > 16:
                wires.append(f"{ESC}0*17*{kind}VC\r")
        tie_count = len(wires)
        wires.append(f"{ESC}VM\r")
        if self.audio_present:
            wires += [f"{o}V" for o in range(1, self.outputs + 1)]
            wires += [f"{i}G" for i in range(1, self.inputs + 1)]
        wires += ["X", f"{ESC}I\r", f"{ESC}O\r"]
        lines = await self._request_many(wires, skip_with="E10")
        pos = 0
        for kind in kinds:
            for start in (1, 17) if self.outputs > 16 else (1,):
                if error_code(lines[pos]) is None:
                    self._apply_ties(kind, start, lines[pos])
                pos += 1
        assert pos == tie_count
        mutes = (
            parse_digits(lines[pos], self.outputs)
            if error_code(lines[pos]) is None
            else None
        )
        pos += 1
        if mutes is not None:
            for output, value in enumerate(mutes, start=1):
                self.video_mute[output] = bool(value & 1)
                if self.audio_present:
                    self.audio_mute[output] = bool(value & 2)
        if self.audio_present:
            for output in range(1, self.outputs + 1):
                line = lines[pos].strip()
                pos += 1
                if line.isdigit():
                    self.volume[output] = int(line)
            for number in range(1, self.inputs + 1):
                value = parse_gain(lines[pos])
                pos += 1
                if value is not None:
                    self.gain[number] = value
        lock = lines[pos].strip()
        pos += 1
        if lock in ("0", "1", "2"):
            self.lock_mode = int(lock)
        self.groups_in = (
            parse_digits(lines[pos], self.inputs)
            if error_code(lines[pos]) is None
            else None
        )
        pos += 1
        self.groups_out = (
            parse_digits(lines[pos], self.outputs)
            if error_code(lines[pos]) is None
            else None
        )
        self._notify()

    async def _read_identity(self) -> None:
        """A serial connection gives no copyright message: ask for the part number and version."""
        part, version = await self._request_many(["N", "Q"], skip_with="E10")
        if re.fullmatch(r"\d{2}-\d{3}-\d{2}", part.strip()):
            self.banner["part_number"] = part.strip()
        if re.fullmatch(r"[\d.]+", version.strip()):
            self.banner["firmware"] = version.strip()

    async def _refresh_info(self) -> None:
        """Read the power and temperature status and the unit's information (all optional)."""
        wires = ["S"] + [f"{ESC}{cmd}\r" for _key, cmd in INFO_COMMANDS]
        lines = await self._request_many(
            wires, settle=frozenset({wires[-1]}), skip_with="E10"
        )
        if error_code(lines[0]) is None:
            self.status = parse_status(lines[0])
        for (key, _cmd), line in zip(INFO_COMMANDS, lines[1:], strict=True):
            value = parse_info(key, line)
            if value is not None:
                self.info[key] = value
        self._notify()

    async def _refresh_names(self) -> None:
        wires = [f"{ESC}{i}NI\r" for i in range(1, self.inputs + 1)]
        wires += [f"{ESC}{o}NO\r" for o in range(1, self.outputs + 1)]
        lines = await self._request_many(wires, skip_with="")
        for index, line in enumerate(lines[: self.inputs], start=1):
            self.in_names[index] = _make_name(line, f"Input {index:02d}")
        for index, line in enumerate(lines[self.inputs :], start=1):
            self.out_names[index] = _make_name(line, f"Output {index:02d}")
        self._notify()

    async def async_refresh_presets(self) -> None:
        """Find out which presets are saved, and their names."""
        lines = await self._request_many(
            [f"{ESC}{p}*1*1VC\r" for p in range(1, MAX_PRESETS + 1)], skip_with="E13"
        )
        found: dict[int, Preset] = {}
        count = 0
        for number, line in enumerate(lines, start=1):
            code = error_code(line)
            if code == 13:
                continue
            count = number
            found[number] = Preset(saved=code is None)
        saved = [n for n, preset in found.items() if preset.saved]
        if saved:
            names = await self._request_many([f"{ESC}{n}NG\r" for n in saved])
            for number, line in zip(saved, names, strict=True):
                found[number].name = (
                    _make_name(line, "") if "unassigned" not in line else ""
                )
        self.presets = found
        self.preset_count = count
        self._notify()

    # -- events -------------------------------------------------------------------------------

    def _dispatch(self, line: str) -> None:
        """Update state from a line the unit volunteered on the event connection."""
        line = line.strip()
        changed = True
        if match := _RE_VMUTE.match(line):
            self.video_mute[int(match.group(1))] = match.group(2) == "1"
        elif match := _RE_AMUTE.match(line):
            self.audio_mute[int(match.group(1))] = match.group(2) == "1"
        elif match := _RE_VMUTE_ALL.match(line):
            self.video_mute = dict.fromkeys(
                range(1, self.outputs + 1), match.group(1) == "1"
            )
        elif match := _RE_AMUTE_ALL.match(line):
            self.audio_mute = dict.fromkeys(
                range(1, self.outputs + 1), match.group(1) == "1"
            )
        elif match := _RE_VOLUME.match(line):
            self.volume[int(match.group(1))] = int(match.group(2))
        elif match := _RE_GAIN.match(line):
            self.gain[int(match.group(1))] = int(match.group(2))
        elif match := _RE_TIE.match(line):
            output, number, kind = (
                int(match.group(1)),
                int(match.group(2)),
                match.group(3),
            )
            if kind in ("Vid", "All", "RGB"):
                self.video[output] = number
            if kind in ("Aud", "All"):
                self.audio[output] = number
        elif match := _RE_TIE_ALL.match(line):
            number, kind = int(match.group(1)), match.group(2)
            for output in range(1, self.outputs + 1):
                if kind in ("Vid", "All", "RGB"):
                    self.video[output] = number
                if kind in ("Aud", "All"):
                    self.audio[output] = number
        elif match := _RE_LOCK.match(line):
            self.lock_mode = int(match.group(1))
        elif match := _RE_NAME_IN.match(line):
            number = int(match.group(1))
            self.in_names[number] = _make_name(match.group(2), f"Input {number:02d}")
        elif match := _RE_NAME_OUT.match(line):
            number = int(match.group(1))
            self.out_names[number] = _make_name(match.group(2), f"Output {number:02d}")
        elif match := _RE_ZONE.match(line):
            offset = normalise_offset(match.group(1))
            if offset is not None:
                self.info["gmt_offset"] = offset
        elif match := _RE_DST.match(line):
            self.info["dst"] = int(match.group(1))
        elif match := _RE_CLOCK.match(line):
            self.info["clock"] = match.group(1)
        elif line == "Qik" or _RE_RECALL.match(line):
            self._spawn(self._refresh_ties())
            changed = False
        elif _RE_PRESET_EVENT.match(line) or _RE_NAME_PRESET.match(line):
            self._spawn(self.async_refresh_presets())
            changed = False
        else:
            changed = False
        if changed:
            self._notify()

    async def _event_loop(self, link: ExtronLink) -> None:
        while True:
            line = await link.read_line(None)
            if line is not None:
                self._dispatch(line)

    # -- background task -------------------------------------------------------------------------

    async def _run(self) -> None:
        """Connect, read, listen and keep alive; reconnect forever if the link drops."""
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except ExtronAuthError as err:
                self._record_error(str(err))
                _LOGGER.warning("Unit %s: %s", self._host, err)
            except Exception as err:  # noqa: BLE001 - whatever goes wrong, start again
                _LOGGER.debug(
                    "Connection to %s:%s lost: %s", self._host, self._port, err
                )
            self._set_available(False)
            await asyncio.sleep(self._reconnect_delay)

    async def _open_links(self) -> tuple[ExtronLink, ExtronLink | None, asyncio.Task]:
        """Open the connection(s) and start listening; returns the command link, the event link and the reader.

        A unit with Ethernet gives a copyright message and gets two connections (one for
        questions, one that only listens). A unit on a serial bridge gives none and has one
        stream, so it gets one connection; that stream is left in the unit's plain mode (or put
        back to it if another controller changed it).
        """
        cmd = ExtronLink(self._host, self._port, self._password)
        evt: ExtronLink | None = None
        try:
            await cmd.open()
            self.banner, self.privilege = dict(cmd.banner), cmd.privilege
            self._single = not cmd.banner
            self._cmd, self._evt = cmd, None
            if self._single:
                await cmd.write(f"{ESC}CV\r")
                mode = await cmd.read_line(2)
                if mode is not None and mode.strip() in ("2", "3"):
                    await cmd.write(f"{ESC}1CV\r")
                    await cmd.read_line(2)
                return cmd, None, asyncio.create_task(self._single_reader(cmd))
            evt = ExtronLink(self._host, self._port, self._password)
            await evt.open()
            self._evt = evt
            # Plain replies on the command connection, a running commentary on the event one.
            await cmd.write(f"{ESC}0CV\r")
            await cmd.read_line(2)
            await evt.write(f"{ESC}3CV\r")
            await evt.read_line(2)
            return cmd, evt, asyncio.create_task(self._event_loop(evt))
        except BaseException:
            self._cmd = self._evt = None
            await cmd.close()
            if evt is not None:
                await evt.close()
            raise

    async def _session(self) -> None:
        links: list[ExtronLink] = []
        reader: asyncio.Task | None = None
        try:
            cmd, evt, reader = await self._open_links()
            links = [link for link in (cmd, evt) if link is not None]
            if not self.banner:
                await self._read_identity()
            await self._refresh_core()
            await self._refresh_names()
            await self.async_refresh_presets()
            await self._refresh_info()
            self._ready.set()
            self._set_available(True)
            loop = asyncio.get_running_loop()
            last_full = last_keepalive = loop.time()
            while True:
                due = (
                    min(
                        last_full + self._refresh_interval,
                        last_keepalive + KEEPALIVE_SECONDS,
                    )
                    - loop.time()
                )
                self._refresh_event.clear()
                waiter = asyncio.create_task(self._refresh_event.wait())
                try:
                    await asyncio.wait(
                        {reader, waiter},
                        timeout=max(0.1, due),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    waiter.cancel()
                if reader.done():
                    reader.result()
                    raise ExtronConnectionError("Connection closed")
                now = loop.time()
                if (
                    self._refresh_event.is_set()
                    or now - last_full >= self._refresh_interval
                ):
                    await self._refresh_core()
                    await self._refresh_info()
                    # A cleared preset is not announced by every unit, so look again.
                    await self.async_refresh_presets()
                    last_full = last_keepalive = loop.time()
                elif now - last_keepalive >= KEEPALIVE_SECONDS:
                    await self._request("Q")
                    if evt is not None:
                        await evt.write("Q")
                    last_keepalive = loop.time()
        finally:
            if reader is not None:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader
            self._cmd = self._evt = None
            self._inbox = None
            for link in links:
                await link.close()
