"""A simulated Extron IN1508, behaving as the real unit did in the probe runs (firmware 2.25)."""

from __future__ import annotations

import asyncio
import re

from custom_components.extron.in1508 import VALID_RATES

# On the real unit tint is refused on a PAL signal, colour exists on inputs 1 to 4 only.
COLOUR_INPUTS = (1, 2, 3, 4)


class Sim1508:
    """One input selected, mutes, freeze, volume, PIP, output format and per-input picture controls."""

    def __init__(self, *, select_delay: float = 0.15) -> None:
        """Set up the unit as it came from the factory."""
        self.select_delay = select_delay
        self.input = 1
        self.pip = 0
        self.pip_input = 0
        self.standard: int | None = 0  # None: the unit sends a dash (an active digital or RGB signal)
        self.silent: set[str] = set()  # commands the unit never answers
        self.odd: dict[str, str] = {}  # commands answered with something unexpected
        self.vmute = 1
        self.amute = 0
        self.freeze = 0
        self.volume = 100
        self.pip_on = 0
        self.pip_size = 0
        self.res, self.rate = 3, 2
        self.picture = {
            n: {"C": 64, "Y": 64, "^": 59 if n <= 4 else 45, "D": 64, "G": 0, "9": 0}
            for n in range(1, 9)
        }
        self.signal_inputs = {3}
        self.log: list[str] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> int:
        """Start listening."""
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        """Stop."""
        for writer in list(self.writers):
            writer.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    def drop_all(self) -> None:
        """Close every connection."""
        for writer in list(self.writers):
            writer.close()

    def say(self, writer: asyncio.StreamWriter, *lines: str) -> None:
        """Send reply lines."""
        for line in lines:
            writer.write((line + "\r\n").encode("latin-1"))

    def announce(self, *lines: str) -> None:
        """Say something to every connection (a front-panel change)."""
        for writer in self.writers:
            self.say(writer, *lines)

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Serve one client (the unit sends nothing on connect)."""
        self.writers.append(writer)
        buffer = ""
        try:
            while data := await reader.read(1024):
                buffer += data.decode("latin-1")
                # The unit acts on each command as soon as it is complete: no terminator.
                while match := re.match(
                    r"\d+\*\d+[=#]|\d+[#!CTYD^GgVBZFHU=/(:;)@]|[A-Z^!=@:;()/%]|.", buffer
                ):
                    buffer = buffer[match.end() :]
                    self.log.append(match.group(0))
                    await self.command(writer, match.group(0))
        except (ConnectionError, OSError):
            pass
        finally:
            if writer in self.writers:
                self.writers.remove(writer)
            writer.close()

    def writes(self) -> list[str]:
        """The logged commands that change something."""
        return [c for c in self.log if not re.fullmatch(r"[A-Z=!^:;()/@]|\d+#|[NQI]", c)]

    # -- commands -----------------------------------------------------------------------------

    def picture_read(self, writer: asyncio.StreamWriter, key: str, tag: str) -> None:
        """Read a picture control of the selected input."""
        if key == "C" and self.input not in COLOUR_INPUTS:
            return self.say(writer, "E10")
        value = self.picture[self.input][key]
        self.say(writer, f"{tag}{value}")
        return None

    async def command(self, writer: asyncio.StreamWriter, text: str) -> None:
        """Answer one command."""
        say = self.say
        if text in self.silent:
            return None
        if text in self.odd:
            return say(writer, self.odd[text])
        if text == "I":
            std = "-" if self.standard is None else self.standard
            return say(writer, f"Chn{self.input} Pch{self.pip_input} Std{std}")
        if text == "N":
            return say(writer, "60-569-01")
        if text == "Q":
            return say(writer, "2.25")
        if text == "!":
            return say(writer, f"Chn{self.input}")
        if m := re.fullmatch(r"(\d+)!", text):
            number = int(m.group(1))
            if not 1 <= number <= 8:
                return say(writer, "E13")
            if number == self.input:
                return say(writer, f"Chn{number}")
            await asyncio.sleep(self.select_delay)
            self.change_input(number)
            return say(writer, f"Chn{number}", "Reconfig")
        if text == "=":
            return say(writer, f"Rte{self.res}*{self.rate}")
        if m := re.fullmatch(r"(\d+)\*(\d+)=", text):
            res, rate = int(m.group(1)), int(m.group(2))
            if res not in VALID_RATES or rate not in VALID_RATES[res]:
                return say(writer, "E13")
            self.res, self.rate = res, rate
            return say(writer, f"Rte{res}*{rate}")
        for key, attr, tag in (("B", "vmute", "Vmt"), ("Z", "amute", "Amt"), ("F", "freeze", "Frz")):
            if text == key:
                return say(writer, f"{tag}{getattr(self, attr)}")
            if m := re.fullmatch(rf"([01]){key}", text):
                setattr(self, attr, int(m.group(1)))
                return say(writer, f"{tag}{m.group(1)}")
        if text == "V":
            return say(writer, f"Vol{self.volume}")
        if m := re.fullmatch(r"(\d+)V", text):
            if int(m.group(1)) > 100:
                return say(writer, "E13")
            self.volume = int(m.group(1))
            return say(writer, f"Vol{self.volume}")
        for key, tag in (("C", "Col"), ("Y", "Brt"), ("^", "Con"), ("D", "Shp")):
            if text == key:
                return self.picture_read(writer, key, tag)
            if m := re.fullmatch(rf"(\d+){re.escape(key)}", text):
                if key == "C" and self.input not in COLOUR_INPUTS:
                    return say(writer, "E10")
                if int(m.group(1)) > 128:
                    return say(writer, "E13")
                self.picture[self.input][key] = int(m.group(1))
                return say(writer, f"{tag}{m.group(1)}")
        if text == "T" or re.fullmatch(r"\d+T", text):
            return say(writer, "E10")  # PAL: tint is refused
        if text == "G":
            return say(writer, f"Aud{self.picture[self.input]['G']:+d}")
        if m := re.fullmatch(r"(\d+)([Gg])", text):
            value = int(m.group(1)) * (1 if m.group(2) == "G" else -1)
            if not -50 <= value <= 9:
                return say(writer, "E13")
            self.picture[self.input]["G"] = value
            return say(writer, f"Aud{value:+d}")
        if text == "9#":
            return say(writer, f"Asp{self.picture[self.input]['9']}")
        if m := re.fullmatch(r"([01])\*9#", text):
            self.picture[self.input]["9"] = int(m.group(1))
            return say(writer, f"Asp{m.group(1)}")
        if text == "5#":
            return say(writer, f"Pip{self.pip_on}")
        if m := re.fullmatch(r"([01])\*5#", text):
            self.pip_on = int(m.group(1))
            return say(writer, f"Pip{self.pip_on}")
        if text == "4#":
            return say(writer, f"Pmd{self.pip_size}")
        if m := re.fullmatch(r"(\d+)\*4#", text):
            if int(m.group(1)) > 5:
                return say(writer, "E13")
            self.pip_size = int(m.group(1))
            return say(writer, f"Pmd{self.pip_size}")
        return say(writer, "E10")

    def change_input(self, number: int) -> None:
        """What the unit does by itself when another input is selected."""
        self.input = number
        self.amute = 0
        self.freeze = 0
        self.vmute = 0 if number in self.signal_inputs else 1

    # -- the front panel -------------------------------------------------------------------------

    def panel_input(self, number: int) -> None:
        """Another input chosen at the front panel."""
        self.change_input(number)
        self.announce(f"Chn{number}", "Reconfig")

    def panel_brightness(self, value: int) -> None:
        """Brightness changed at the front panel (one line per step)."""
        self.picture[self.input]["Y"] = value
        self.announce(f"Brt{value}")
