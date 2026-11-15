"""Find .tlsd songs embedded in a larger file, such as TRIOPLUS.DAT or an SD card image.

Every song header seen so far carries the same 48 bytes at offset 1192 (block
sizes and the header length). The scanner looks for that signature, then
checks the 0x0303 marker and the size bookkeeping before accepting a match.
"""

from __future__ import annotations

import mmap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .tlsd import HEADER_SIZE, MARKER, OFF_FILE_SIZE, OFF_MARKER, OFF_PART_LENGTHS, Song, TlsdError

SIG_OFFSET = 1192
SIGNATURE = bytes.fromhex(
    "8800000000000000000400000000000088040000000000000040000000400100"
    "8844010000000000002a000000d20000"
)


def _u32(buf, off: int) -> int:
    return int.from_bytes(buf[off:off + 4], "little")


@dataclass
class Found:
    offset: int  # start of the song inside the container
    size: int  # declared song size
    complete: bool  # the whole song fits inside the container
    valid: bool  # the slice parses as a song
    name: str
    trained_parts: list[int]
    reason: str = ""


def scan(path: str | Path) -> Iterator[tuple[Found, bytes | None]]:
    """Yield every header found, with the song bytes when they are valid."""
    if Path(path).stat().st_size < HEADER_SIZE:
        return
    with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        total = len(mm)
        pos = mm.find(SIGNATURE)
        while pos != -1:
            start = pos - SIG_OFFSET
            if start >= 0 and start + HEADER_SIZE <= total and _u32(mm, start + OFF_MARKER) == MARKER:
                header = mm[start:start + HEADER_SIZE]
                lengths = [_u32(header, o) for o in OFF_PART_LENGTHS]
                size = HEADER_SIZE + sum(lengths)
                raw_name = header[1332:1460].split(b"\x00", 1)[0]
                name = raw_name.decode("ascii", errors="replace")
                trained = [i + 1 for i, n in enumerate(lengths) if n]
                complete = start + size <= total
                reason, data, valid = "", None, False
                if _u32(header, OFF_FILE_SIZE) != size:
                    reason = "file-size field does not match part lengths"
                elif not complete:
                    reason = "song runs past the end of the container"
                else:
                    data = mm[start:start + size]
                    try:
                        Song.from_bytes(data)
                        valid = True
                    except TlsdError as e:
                        reason, data = str(e), None
                yield Found(start, size, complete, valid, name, trained, reason), data
            pos = mm.find(SIGNATURE, pos + 1)
