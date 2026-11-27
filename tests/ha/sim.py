"""A simulated Extron MAV Plus 2424, behaving as the real unit did in the probe runs."""

from __future__ import annotations

import asyncio
import re

ESC = "\x1b"
BANNER = (
    "\r\n(c) Copyright 2006, Extron Electronics, Crosspoint-MAV, V2.03, 60-472-01\r\n"
    "Thu, 19 Nov 2026 14:39:51\r\n"
)


class Conn:
    """One client connection."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        """Set up."""
        self.writer = writer
        self.verbose = 0
        self.buffer = ""
        self.authed = True


class Sim:
    """Ties, mutes, volumes, levels, names, lock, groups and 32 presets."""

    def __init__(
        self,
        size: int = 24,
        password: str = "",
        *,
        banner: bool = True,
        short_status: bool = False,
        audio: bool = True,
    ) -> None:
        """Set up the object (``banner`` False and ``short_status`` True behave like the 1616 on a serial bridge)."""
        self.n = size
        self.password = password
        self.banner = banner
        self.short_status = short_status
        self.audio_side = audio
        self.late_zone_reply: str | None = None
        self.before_reply: list[str] = []  # lines to send ahead of the next reply (an announcement in between)
        self.echo_preset_clear = False  # the 2424 does not tell other connections about a clear
        self.video = dict.fromkeys(range(1, size + 1), 0)
        self.audio = dict.fromkeys(range(1, size + 1), 0)
        self.vmute = dict.fromkeys(range(1, size + 1), False)
        self.amute = dict.fromkeys(range(1, size + 1), False)
        self.volume = dict.fromkeys(range(1, size + 1), 64)
        self.gain = dict.fromkeys(range(1, size + 1), 0)
        self.in_names = {i: f"Input #{i:02d}   " for i in range(1, size + 1)}
        self.out_names = {o: f"Output #{o:02d}  " for o in range(1, size + 1)}
        self.lock = 0
        self.groups_in = [0] * size
        self.groups_out = [0] * size
        self.presets: dict[int, dict] = {}
        self.preset_max = 32
        self.conns: list[Conn] = []
        self.log: list[str] = []
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self.clock = "Thu, 19 Nov 2026 16:23:07"
        self.zone = "-12:00"  # as the unit reads it back: 00:00, +01:00, -05:00
        self.dst = 0
        self.mute_replies = False  # for the "unit stops answering" test
        self.video[1] = 1
        self.audio[1] = 1

    def writes(self) -> list[str]:
        """Return the logged commands that change something on the unit (reads and verbose setup excluded)."""
        found = []
        for line in self.log:
            if line.startswith("ESC"):
                body = line[3:]
                if re.fullmatch(r"\d+ZG|\d{4,}[IO]|\+Q.*|[\d/ :]+CT|[+-]?\d+[:.]\d\d[C]Z|[0-3]CX|Z[ZAV]", body):
                    found.append(line)
            elif (
                re.fullmatch(
                    r"\d+\*\d+[!%$&BZVGg]|[01]\*[BZ]|\d+\*[!%$]|\dX|\d+[,.]", line
                )
                and line not in ("0X", "1X", "2X")
                or re.fullmatch(r"\dX", line)
                and line[0] in "012"
            ):
                found.append(line)
        return found

    async def start(self) -> int:
        """Start."""
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        """Stop."""
        for conn in list(self.conns):
            conn.writer.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    def drop_all(self) -> None:
        """Close every connection (as a power cut or the idle timeout would)."""
        for conn in list(self.conns):
            conn.writer.close()

    # -- I/O ------------------------------------------------------------------

    def send(self, conn: Conn, text: str) -> None:
        """Send one reply line."""
        if self.mute_replies:
            return
        for line in self.before_reply:
            conn.writer.write((line + "\r\n").encode("latin-1"))
        self.before_reply = []
        conn.writer.write((text + "\r\n").encode("latin-1"))

    def event(self, text: str, source: Conn | None = None) -> None:
        """Tell every verbose-3 connection (every connection of a serial unit), except the one that caused it."""
        for conn in self.conns:
            wanted = conn.verbose >= 1 if not self.banner else conn.verbose == 3
            if conn is not source and wanted:
                conn.writer.write((text + "\r\n").encode("latin-1"))

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Serve one client."""
        conn = Conn(writer)
        if not self.banner:
            conn.verbose = 1  # a serial unit comes up in mode 1: tagged changes, bare reads, announcements
        self.conns.append(conn)
        if self.banner:
            writer.write(BANNER.encode())
        if self.password:
            conn.authed = False
            writer.write(b"Password:\r\n")
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                conn.buffer += data.decode("latin-1")
                self.process(conn)
        except (ConnectionError, OSError):
            pass
        finally:
            if conn in self.conns:
                self.conns.remove(conn)
            writer.close()

    # -- command parsing -----------------------------------------------------------

    def process(self, conn: Conn) -> None:
        """Parse as many complete commands as the buffer holds."""
        if self.late_zone_reply is not None and conn.buffer:
            # A zone set with a leading plus sign is answered only when the next command arrives.
            reply, self.late_zone_reply = self.late_zone_reply, None
            self.send(conn, reply)
        if not conn.authed:
            while "\r" in conn.buffer:
                line, conn.buffer = conn.buffer.split("\r", 1)
                if line.strip() == self.password:
                    conn.authed = True
                    conn.writer.write(b"Login Administrator\r\n")
                    break
                conn.writer.write(b"Password:\r\n")
            else:
                return
        while conn.buffer:
            if conn.buffer[0] == ESC:
                if "\r" not in conn.buffer:
                    return
                body, conn.buffer = conn.buffer[1:].split("\r", 1)
                self.log.append("ESC" + body)
                self.esc(conn, body)
                continue
            match = re.match(
                r"(\d+)\*(\d+)([!%$&BZVGg])|(\d+)([!%$&BZVGg])|(\d+)([,.X])|(Q|I|X|S|N)|(\d+)\*([!%$BZ])",
                conn.buffer,
            )
            if not match:
                conn.buffer = conn.buffer[1:]
                continue
            text, conn.buffer = match.group(0), conn.buffer[match.end() :]
            self.log.append(text)
            self.plain(conn, text)

    def v3(self, conn: Conn) -> bool:
        """Whether this connection gets tagged replies."""
        return conn.verbose in (2, 3)

    def err(self, conn: Conn, code: int) -> None:
        """Send an error."""
        self.send(conn, f"E{code:02d}")

    def plain(self, conn: Conn, t: str) -> None:
        """Handle a plain (no-escape) command."""
        if t == "Q":
            self.send(conn, "Ver01*2.03" if self.v3(conn) else "2.03")
            return
        if t == "S":
            self.send(
                conn,
                "3.27 5.07 -5.07 14.95 -14.97 98.60"
                if self.short_status
                else "5.06 3.32 2.51 11.15 -5.04 -11.98 11.52 71.60 1081 1095 1 1",
            )
            return
        if t == "N":
            self.send(conn, "60-472-01")
            return
        if t == "I":
            audio = self.n if self.audio_side else 0
            self.send(conn, f"V{self.n}X{self.n} A{audio}X{audio}")
            return
        if t == "X":
            self.send(conn, f"Exe{self.lock}" if self.v3(conn) else str(self.lock))
            return
        if m := re.fullmatch(r"(\d)X", t):
            if int(m.group(1)) > 2:
                return self.err(conn, 13)
            self.lock = int(m.group(1))
            self.send(conn, f"Exe{self.lock}")
            self.event(f"Exe{self.lock}", conn)
            return
        if m := re.fullmatch(r"(\d+)([,.])", t):
            return self.preset(conn, int(m.group(1)), m.group(2))
        if m := re.fullmatch(r"([01])\*([BZ])", t):
            value = m.group(1) == "1"
            table = self.vmute if m.group(2) == "B" else self.amute
            for o in table:
                table[o] = value
            tag = "Vmt" if m.group(2) == "B" else "Amt"
            self.send(conn, f"{tag}{m.group(1)}")
            self.event(f"{tag}{m.group(1)}", conn)
            return
        if m := re.fullmatch(r"(\d+)\*([!%$])", t):
            if int(m.group(1)) > self.n:
                return self.err(conn, 1)
            kind = {"!": "All", "%": "Vid", "$": "Aud"}[m.group(2)]
            for o in range(1, self.n + 1):
                if kind in ("All", "Vid"):
                    self.video[o] = int(m.group(1))
                if kind in ("All", "Aud"):
                    self.audio[o] = int(m.group(1))
            line = f"In{int(m.group(1)):02d} {kind}"
            self.send(conn, line)
            self.event(line, conn)
            return
        if m := re.fullmatch(r"(\d+)\*(\d+)([!%$&])", t):
            return self.tie(conn, int(m.group(1)), int(m.group(2)), m.group(3))
        if m := re.fullmatch(r"(\d+)\*(\d+)([BZVGg])", t):
            return self.set_value(conn, int(m.group(1)), int(m.group(2)), m.group(3))
        if m := re.fullmatch(r"(\d+)([!%$&BZVGg])", t):
            return self.read_value(conn, int(m.group(1)), m.group(2))
        self.err(conn, 10)

    def tie(self, conn: Conn, i: int, o: int, sym: str) -> None:
        """Make a tie."""
        if i > self.n:
            return self.err(conn, 1)
        if not 1 <= o <= self.n:
            return self.err(conn, 12)
        kind = {"!": "All", "%": "Vid", "$": "Aud", "&": "RGB"}[sym]
        if kind in ("All", "Vid", "RGB"):
            self.video[o] = i
        if kind in ("All", "Aud"):
            self.audio[o] = i
        line = f"Out{o:02d} In{i:02d} {kind}"
        self.send(conn, line)
        self.event(line, conn)

    def set_value(self, conn: Conn, a: int, b: int, sym: str) -> None:
        """Mute, volume or level changes (a = output or input, b = value)."""
        if sym in "BZ":
            if not 1 <= a <= self.n:
                return self.err(conn, 12)
            if b not in (0, 1):
                return self.err(conn, 13)
            (self.vmute if sym == "B" else self.amute)[a] = bool(b)
            line = f"{'Vmt' if sym == 'B' else 'Amt'}{a:02d}*{b}"
        elif sym == "V":
            if not 1 <= a <= self.n:
                return self.err(conn, 12)
            if not 0 <= b <= 64:
                return self.err(conn, 13)
            self.volume[a] = b
            line = f"Out{a:02d} Vol{b:02d}"
        else:
            if not 1 <= a <= self.n:
                return self.err(conn, 1)
            if (sym == "G" and b > 24) or (sym == "g" and b > 18):
                return self.err(conn, 13)
            self.gain[a] = b if sym == "G" else -b
            line = f"In{a:02d} Aud{self.gain[a]:+03d}"
        self.send(conn, line)
        self.event(line, conn)

    def read_value(self, conn: Conn, n: int, sym: str) -> None:
        """Read a value."""
        if sym in "%$!&":
            if not 1 <= n <= self.n:
                return self.err(conn, 12)
            v, a = self.video[n], self.audio[n]
            if sym == "!" and v != a:
                return self.err(conn, 13)
            value = a if sym == "$" else v
            kind = {"!": "All", "%": "Vid", "$": "Aud", "&": "RGB"}[sym]
            self.send(
                conn,
                f"Out{n:02d} In{value:02d} {kind}" if self.v3(conn) else f"{value:02d}",
            )
        elif sym in "BZ":
            if not 1 <= n <= self.n:
                return self.err(conn, 12)
            self.send(conn, str(int((self.vmute if sym == "B" else self.amute)[n])))
        elif sym == "V":
            if not 1 <= n <= self.n:
                return self.err(conn, 12)
            self.send(
                conn,
                f"Out{n:02d} Vol{self.volume[n]:02d}"
                if self.v3(conn)
                else f"{self.volume[n]:02d}",
            )
        else:
            if not 1 <= n <= self.n:
                return self.err(conn, 1)
            value = f"{self.gain[n]:+03d}"
            self.send(conn, f"In{n:02d} Aud{value}" if self.v3(conn) else value)

    def preset(self, conn: Conn, p: int, op: str) -> None:
        """Save or recall."""
        if not 1 <= p <= self.preset_max:
            return self.err(conn, 13)
        if op == ",":
            self.presets[p] = {
                "video": dict(self.video),
                "audio": dict(self.audio),
                "name": "",
            }
            self.send(conn, f"Spr{p:02d}")
            self.event(f"Spr{p:02d}", conn)
            return
        if p not in self.presets:
            return self.err(conn, 11)
        self.video = dict(self.presets[p]["video"])
        self.audio = dict(self.presets[p]["audio"])
        self.send(conn, f"Rpr{p:02d}")
        self.event(f"Rpr{p:02d}", conn)

    def tie_list(self, table: dict[int, int], start: int, tag: str) -> str:
        """Sixteen values from ``start``, padded with --."""
        values = [
            f"{table[o]:02d}" if o in table else "--" for o in range(start, start + 16)
        ]
        return " ".join(values) + f" {tag}"

    def esc(self, conn: Conn, body: str) -> None:
        """Handle an escape command."""
        if body == "CN":
            return self.send(conn, "Crosspoint-MAV-00-00-01")
        info = {
            "CI": "192.168.0.254",
            "CS": "255.255.255.0",
            "CG": "0.0.0.0",
            "CH": "00-05-A6-00-00-01",
            "DH": "0",
            "CC": "006",
            "CT": self.clock,
            "CZ": self.zone,
            "CX": str(self.dst),
            "0TC": "00030",
            "1CP": "9600,N,8,1",
        }
        if body in info:
            return self.send(conn, info[body])
        if m := re.fullmatch(r"([+-]?)(\d{1,2})[:.](\d\d)CZ", body):
            hours, mins = int(m.group(2)), int(m.group(3))
            total = (-1 if m.group(1) == "-" else 1) * (hours * 60 + mins)
            if mins > 59 or not -720 <= total <= 840:
                return self.err(conn, 13)
            sign = "-" if total < 0 else "+" if total > 0 else ""
            self.zone = f"{sign}{abs(total) // 60:02d}:{abs(total) % 60:02d}"
            if m.group(1) == "+":
                self.late_zone_reply = f"Ipz{self.zone}"
                return None
            self.send(conn, f"Ipz{self.zone}")
            self.event(f"Ipz{self.zone}", conn)
            return None
        if m := re.fullmatch(r"(\d)CX", body):
            if int(m.group(1)) > 3:
                return self.err(conn, 13)
            self.dst = int(m.group(1))
            self.send(conn, f"Ipx{self.dst}")
            self.event(f"Ipx{self.dst}", conn)
            return
        if body in ("ZZ", "ZA", "ZV"):
            # The unit answers the command and says nothing to other connections.
            for o in range(1, self.n + 1):
                if body == "ZZ":
                    self.vmute[o] = self.amute[o] = False
                elif body == "ZA":
                    self.gain[o] = 0
                else:
                    self.volume[o] = 64
            return self.send(conn, {"ZZ": "Zpz", "ZA": "Zpa", "ZV": "Zpv"}[body])
        if body == "DF":
            conn.writer.write(b"1245184 Bytes Left\r\n\r\n")
            return
        if m := re.fullmatch(r"(\d\d)/(\d\d)/(\d\d) (\d\d:\d\d:\d\d)CT", body):
            if int(m.group(1)) > 12 or int(m.group(2)) > 31:
                return self.err(conn, 13)
            self.clock = f"Thu, {m.group(2)} Nov 20{m.group(3)} {m.group(4)}"
            return self.send(conn, f"Ipt {self.clock}")
        if body.startswith("+Q"):
            for i, o, sym in re.findall(r"(\d+)\*(\d+)([!%$])", body[2:]):
                i, o = int(i), int(o)
                if i > self.n or not 1 <= o <= self.n:
                    continue
                if sym in "!%":
                    self.video[o] = i
                if sym in "!$":
                    self.audio[o] = i
            self.send(conn, "Qik")
            self.event("Qik", conn)
            return
        if body == "CV":
            return self.send(conn, str(conn.verbose))
        if m := re.fullmatch(r"([0-3])CV", body):
            conn.verbose = int(m.group(1))
            return self.send(conn, f"Vrb{conn.verbose}")
        if m := re.fullmatch(r"0\*(1|17)\*([12])VC", body):
            table = self.video if m.group(2) == "1" else self.audio
            return self.send(
                conn,
                self.tie_list(
                    table, int(m.group(1)), "Vid" if m.group(2) == "1" else "Aud"
                ),
            )
        if m := re.fullmatch(r"(\d+)\*1\*([12])VC", body):
            p = int(m.group(1))
            if not 1 <= p <= self.preset_max:
                return self.err(conn, 13)
            if p not in self.presets:
                return self.err(conn, 11)
            table = self.presets[p]["video" if m.group(2) == "1" else "audio"]
            return self.send(
                conn, self.tie_list(table, 1, "Vid" if m.group(2) == "1" else "Aud")
            )
        if body == "VM":
            digits = "".join(
                str(int(self.vmute[o]) + 2 * int(self.amute[o]))
                for o in range(1, self.n + 1)
            )
            return self.send(conn, ("Mut" if self.v3(conn) else "") + digits)
        if m := re.fullmatch(r"(\d+)N([IO])", body):
            n, kind = int(m.group(1)), m.group(2)
            table = self.in_names if kind == "I" else self.out_names
            if n not in table:
                return self.err(conn, 1 if kind == "I" else 12)
            return self.send(conn, table[n])
        if m := re.fullmatch(r"(\d+)NG", body):
            p = int(m.group(1))
            if not 1 <= p <= self.preset_max:
                return self.err(conn, 13)
            if p not in self.presets:
                return self.send(conn, "[unassigned]")
            return self.send(conn, self.presets[p]["name"])
        if m := re.fullmatch(r"(\d+)ZG", body):
            p = int(m.group(1))
            if not 1 <= p <= self.preset_max:
                return self.err(conn, 13)
            self.presets.pop(p, None)
            self.send(conn, f"Zpg{p:02d}")
            if self.echo_preset_clear:
                self.event(f"Zpg{p:02d}", conn)
            return
        if body in ("I", "O"):
            groups = self.groups_in if body == "I" else self.groups_out
            return self.send(conn, "".join(str(g) for g in groups))
        if m := re.fullmatch(r"(\d+)([IO])", body):
            digits = m.group(1)
            if len(digits) != self.n or any(int(d) > 4 for d in digits):
                return self.err(conn, 13)
            groups = [int(d) for d in digits]
            if m.group(2) == "I":
                self.groups_in = groups
            else:
                self.groups_out = groups
            return self.send(conn, ("Gri" if m.group(2) == "I" else "Gro") + digits)
        self.err(conn, 10)

    # -- things that happen at the front panel ------------------------------------------

    def panel_tie(self, o: int, i: int) -> None:
        """A front-panel tie: the unit only says Qik."""
        self.video[o] = i
        self.audio[o] = i
        self.event("Qik")

    def panel_mute(self, o: int, muted: bool, video: bool = True) -> None:
        """A front-panel mute."""
        (self.vmute if video else self.amute)[o] = muted
        self.event(f"{'Vmt' if video else 'Amt'}{o:02d}*{int(muted)}")

    def panel_volume(self, o: int, value: int) -> None:
        """A front-panel volume change."""
        self.volume[o] = value
        self.event(f"Out{o:02d} Vol{value:02d}")

    def panel_gain(self, i: int, value: int) -> None:
        """A front-panel level change."""
        self.gain[i] = value
        self.event(f"In{i:02d} Aud{value:+03d}")

    def panel_lock(self, mode: int) -> None:
        """A front-panel lock change."""
        self.lock = mode
        self.event(f"Exe{mode}")
