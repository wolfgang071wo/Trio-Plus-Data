"""Read and write DigiTech TRIO+ song files (.tlsd).

Layout of a .tlsd file (all integers little-endian):

    0x00000  global header           2,352 bytes
    0x00930  5 x part block A       16,384 bytes each  (looper/DSP state)
    0x14930  5 x part block B       10,752 bytes each  (learned band part)
    0x21B30  audio area             sum of the 5 part lengths

The audio area holds one region per trained part, in part order. A region is
split into 32 KB chunks: odd chunks (1, 3, 5, ...) carry the first recording,
even chunks (0, 2, 4, ...) the overdub slot. Audio is 16-bit signed mono PCM
at 44.1 kHz.

The offsets come from the community notes in this repository
(findings.txt, Trio+Addresses.xlsx) and were checked by round-tripping every
sample file byte for byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

HEADER_SIZE = 138_032
CHUNK_SIZE = 32_768
NUM_PARTS = 5
SAMPLE_RATE = 44_100
SAMPLE_WIDTH = 2  # bytes

BLOCK_A_OFFSET = 2_352
BLOCK_A_SIZE = 16_384
BLOCK_B_OFFSET = BLOCK_A_OFFSET + NUM_PARTS * BLOCK_A_SIZE  # 84,272
BLOCK_B_SIZE = 10_752
assert BLOCK_B_OFFSET + NUM_PARTS * BLOCK_B_SIZE == HEADER_SIZE

OFF_FILE_SIZE = 544  # u32: total file size
OFF_SIZE_MINUS_1192 = (632, 1320)  # u32: file size - 1192
OFF_PART_ENDS = (1248, 1256, 1264, 1272)  # u32: end of part n - 1192
OFF_PART_LENGTHS = (1300, 1304, 1308, 1312, 1316)  # u32: region length in bytes
OFF_MARKER = 1328  # u32: 0x00000303
OFF_NAME = 1332
NAME_FIELD = 128
SIZE_BIAS = 1192
MARKER = 0x0303

# Band settings inside block A, as mapped by freimann/trio-updater
# (docs/sdcard.md). Provisional: not yet verified one setting at a time.
BAND_FIELDS = {
    "bass_mode": 15_560,  # 0 active, 1 simple root, 3 root riff
    "genre": 15_564,
    "style": 16_160,
    "alt_time": 16_244,
}

TRACKS = ("main", "overdub")

_TEMPLATE = Path(__file__).with_name("data") / "template_empty_song.tlsd"


class TlsdError(ValueError):
    """The data is not a .tlsd file this library understands."""


def _u32(data: bytes | bytearray, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


def _put_u32(data: bytearray, offset: int, value: int) -> None:
    data[offset:offset + 4] = value.to_bytes(4, "little")


def split_tracks(region: bytes) -> dict[str, bytes]:
    """Split an audio region into its two interleaved tracks."""
    chunks = [region[i:i + CHUNK_SIZE] for i in range(0, len(region), CHUNK_SIZE)]
    return {
        "main": b"".join(chunks[1::2]),
        "overdub": b"".join(chunks[0::2]),
    }


def trim_silence(pcm: bytes) -> bytes:
    """Strip trailing all-zero samples."""
    end = len(pcm) - (len(pcm) % SAMPLE_WIDTH)
    while end >= SAMPLE_WIDTH and pcm[end - SAMPLE_WIDTH:end] == b"\x00\x00":
        end -= SAMPLE_WIDTH
    return pcm[:end]


@dataclass
class Part:
    """One of the five song parts."""

    number: int  # 1..5
    region: bytes = b""  # raw audio area, empty if the part is not trained
    block_a: bytes = b""
    block_b: bytes = b""

    @property
    def trained(self) -> bool:
        return len(self.region) > 0

    def tracks(self) -> dict[str, bytes]:
        return split_tracks(self.region) if self.trained else {t: b"" for t in TRACKS}

    def track_has_audio(self, track: str) -> bool:
        return any(self.tracks()[track])

    def band(self) -> dict[str, int]:
        return {key: _u32(self.block_a, off) for key, off in BAND_FIELDS.items()}

    def describe(self) -> dict:
        tracks = self.tracks()
        return {
            "part": self.number,
            "trained": self.trained,
            "region_bytes": len(self.region),
            "tracks": {
                name: {
                    "has_audio": any(pcm),
                    "samples": len(pcm) // SAMPLE_WIDTH,
                    "seconds": round(len(pcm) / SAMPLE_WIDTH / SAMPLE_RATE, 3),
                }
                for name, pcm in tracks.items()
            },
            "band_provisional": self.band(),
        }


@dataclass
class Song:
    """A parsed .tlsd song: global header plus five parts."""

    header: bytearray
    parts: list[Part] = field(default_factory=list)

    # -- construction ---------------------------------------------------

    @classmethod
    def from_bytes(cls, data: bytes) -> "Song":
        if len(data) < HEADER_SIZE:
            raise TlsdError(f"file too short ({len(data)} bytes, header needs {HEADER_SIZE})")
        if _u32(data, OFF_MARKER) != MARKER:
            raise TlsdError("marker 0x0303 not found at offset 1328")
        lengths = [_u32(data, off) for off in OFF_PART_LENGTHS]
        if HEADER_SIZE + sum(lengths) != len(data):
            raise TlsdError(
                f"part lengths ({sum(lengths)} bytes) do not match the audio area "
                f"({len(data) - HEADER_SIZE} bytes)"
            )
        parts = []
        pos = HEADER_SIZE
        for i, length in enumerate(lengths):
            a = BLOCK_A_OFFSET + i * BLOCK_A_SIZE
            b = BLOCK_B_OFFSET + i * BLOCK_B_SIZE
            parts.append(Part(
                number=i + 1,
                region=bytes(data[pos:pos + length]),
                block_a=bytes(data[a:a + BLOCK_A_SIZE]),
                block_b=bytes(data[b:b + BLOCK_B_SIZE]),
            ))
            pos += length
        return cls(header=bytearray(data[:HEADER_SIZE]), parts=parts)

    @classmethod
    def load(cls, path: str | Path) -> "Song":
        return cls.from_bytes(Path(path).read_bytes())

    @classmethod
    def empty(cls, template: str | Path | None = None) -> "Song":
        """A song with five untrained parts, based on the bundled template."""
        return cls.load(template or _TEMPLATE)

    # -- properties -----------------------------------------------------

    @property
    def name(self) -> str:
        raw = bytes(self.header[OFF_NAME:OFF_NAME + NAME_FIELD]).split(b"\x00", 1)[0]
        return raw.decode("ascii", errors="replace")

    @name.setter
    def name(self, value: str) -> None:
        try:
            raw = value.encode("ascii")
        except UnicodeEncodeError:
            raise TlsdError("song name must be printable ASCII") from None
        if len(raw) >= NAME_FIELD:
            raise TlsdError(f"song name longer than {NAME_FIELD - 1} characters")
        if any(b < 0x20 or b > 0x7E for b in raw):
            raise TlsdError("song name must be printable ASCII")
        self.header[OFF_NAME:OFF_NAME + NAME_FIELD] = raw.ljust(NAME_FIELD, b"\x00")

    def part(self, number: int) -> Part:
        if not 1 <= number <= NUM_PARTS:
            raise TlsdError(f"part number must be 1..{NUM_PARTS}, got {number}")
        return self.parts[number - 1]

    def describe(self) -> dict:
        return {
            "name": self.name,
            "file_bytes": HEADER_SIZE + sum(len(p.region) for p in self.parts),
            "parts": [p.describe() for p in self.parts],
        }

    # -- editing --------------------------------------------------------

    def set_part(self, number: int, source: Part) -> None:
        """Copy a part (audio and settings) from any song into slot `number`."""
        self.part(number)  # range check
        self.parts[number - 1] = Part(number, source.region, source.block_a, source.block_b)

    def clear_part(self, number: int, template: "Song | None" = None) -> None:
        empty = (template or Song.empty()).part(number)
        self.set_part(number, empty)

    # -- serialisation --------------------------------------------------

    def to_bytes(self) -> bytes:
        header = bytearray(self.header)
        for i, p in enumerate(self.parts):
            if len(p.block_a) != BLOCK_A_SIZE or len(p.block_b) != BLOCK_B_SIZE:
                raise TlsdError(f"part {p.number}: settings blocks have the wrong size")
            a =BLOCK_A_OFFSET + i * BLOCK_A_SIZE
            header[a:a + BLOCK_A_SIZE] = p.block_a
            b = BLOCK_B_OFFSET + i * BLOCK_B_SIZE
            header[b:b + BLOCK_B_SIZE] = p.block_b
            _put_u32(header, OFF_PART_LENGTHS[i], len(p.region))

        end = HEADER_SIZE
        for i, off in enumerate(OFF_PART_ENDS):
            end += len(self.parts[i].region)
            _put_u32(header, off, end - SIZE_BIAS)

        size = HEADER_SIZE + sum(len(p.region) for p in self.parts)
        _put_u32(header, OFF_FILE_SIZE, size)
        for off in OFF_SIZE_MINUS_1192:
            _put_u32(header, off, size - SIZE_BIAS)

        return bytes(header) + b"".join(p.region for p in self.parts)

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(self.to_bytes())


def load_all(paths: Iterable[str | Path]) -> list[tuple[Path, Song]]:
    return [(Path(p), Song.load(p)) for p in paths]
