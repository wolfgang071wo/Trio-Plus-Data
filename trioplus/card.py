"""Read songs straight from a TRIO+ card container (TRIOPLUS.DAT).

Layout, worked out from a real card (firmware as of 2026) by matching an
exported .tlsd against the container byte for byte:

    0x0000000  "TLSD" magic, card name "Trio+ Card"
    0x1425800  12 song records, 51,712 bytes apart: 0x0303 marker + name
               (the rest is uninitialised memory, also in exported files)
    0x14BD000  120 part records, 83,968 bytes apart:
                 records  0..59  block A of part slot 0..59 (16,384 bytes)
                 records 60..119 block B of part slot 0..59 (10,752 bytes)
    size - 60 * 52,920,320
               60 audio slots of 52,920,320 bytes (1615 chunks, 300 s per
               track); the audio region of a slot starts 32,768 bytes in
               and is stored exactly like in a .tlsd

Block B starts with three u32: 0 = part present / 1 = empty, the region
length in 1 KB units, and the same length again once audio is recorded
(0xFFFFFFFF before). Blocks A and B are copied unchanged into exported files.

Slots are assigned statically: song n (1..12) owns slots (n-1)*5 .. (n-1)*5+4,
one per part. Confirmed on a real card with recordings in songs 1, 2 and 8
(slots 0-4, 5-6 and 35), cross-checked against the pedal and TRIO Manager.

    0x0000C800 / 0x0000C810: small values (seen 02 01 and 07); meaning unknown
"""

from __future__ import annotations

import mmap
from dataclasses import dataclass
from pathlib import Path

from .tlsd import BLOCK_A_SIZE, BLOCK_B_SIZE, NUM_PARTS, Part, Song, TlsdError

MAGIC = b"TLSD"
SONG_TABLE = 0x1425800
SONG_STRIDE = 51_712
NUM_SONGS = 12
RECORD_TABLE = 0x14BD000
RECORD_STRIDE = 83_968
NUM_SLOTS = NUM_SONGS * NUM_PARTS  # 60
AUDIO_SLOT = 52_920_320
AUDIO_LEAD = 32_768


def _u32(buf, off: int) -> int:
    return int.from_bytes(buf[off:off + 4], "little")


@dataclass
class Slot:
    index: int
    present: bool
    region_bytes: int
    block_a: bytes
    block_b: bytes
    audio_offset: int


class Card:
    """A read-only view of TRIOPLUS.DAT."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fh = open(self.path, "rb")
        try:
            self._m = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:
            self._fh.close()
            raise TlsdError(f"{self.path} is empty") from None
        self.size = len(self._m)
        if self._m[:4] != MAGIC:
            self.close()
            raise TlsdError(f"{self.path} does not start with 'TLSD', not a TRIO+ card file")
        self.audio_base = self.size - NUM_SLOTS * AUDIO_SLOT
        if self.audio_base < RECORD_TABLE + 2 * NUM_SLOTS * RECORD_STRIDE:
            self.close()
            raise TlsdError(f"{self.path} is too small for 60 audio slots")

    def close(self) -> None:
        self._m.close()
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- tables ---------------------------------------------------------

    def song_names(self) -> list[str]:
        names = []
        for i in range(NUM_SONGS):
            rec = SONG_TABLE + i * SONG_STRIDE
            if _u32(self._m, rec) != 0x0303:
                names.append("")
                continue
            raw = self._m[rec + 4:rec + 132].split(b"\x00", 1)[0]
            names.append(raw.decode("ascii", errors="replace"))
        return names

    def slot(self, index: int) -> Slot:
        if not 0 <= index < NUM_SLOTS:
            raise TlsdError(f"slot must be 0..{NUM_SLOTS - 1}")
        a = RECORD_TABLE + index * RECORD_STRIDE
        b = RECORD_TABLE + (NUM_SLOTS + index) * RECORD_STRIDE
        block_b = self._m[b:b + BLOCK_B_SIZE]
        present = _u32(block_b, 0) == 0
        return Slot(
            index=index,
            present=present,
            region_bytes=_u32(block_b, 4) * 1024 if present else 0,
            block_a=self._m[a:a + BLOCK_A_SIZE],
            block_b=block_b,
            audio_offset=self.audio_base + index * AUDIO_SLOT + AUDIO_LEAD,
        )

    def slots(self) -> list[Slot]:
        return [self.slot(i) for i in range(NUM_SLOTS)]

    def part_from_slot(self, s: Slot, number: int) -> Part:
        if s.region_bytes > AUDIO_SLOT - AUDIO_LEAD:
            raise TlsdError(f"slot {s.index}: region of {s.region_bytes} bytes does not fit a slot")
        region = self._m[s.audio_offset:s.audio_offset + s.region_bytes]
        return Part(number, region, s.block_a, s.block_b)

    # -- songs ----------------------------------------------------------

    def song_slots(self, number: int) -> list[Slot]:
        """The five part slots of song `number` (1..12)."""
        if not 1 <= number <= NUM_SONGS:
            raise TlsdError(f"song must be 1..{NUM_SONGS}")
        first = (number - 1) * NUM_PARTS
        return [self.slot(first + i) for i in range(NUM_PARTS)]

    def song(self, number: int) -> Song:
        """Song `number` (1..12) as a .tlsd, like a TRIO Manager export."""
        song = Song.empty()
        for n, s in enumerate(self.song_slots(number), start=1):
            if s.present:
                song.set_part(n, self.part_from_slot(s, n))
        name = self.song_names()[number - 1] or f"Song {number:02d}"
        try:
            song.name = name
        except TlsdError:
            song.name = f"Song {number:02d}"
        return song
