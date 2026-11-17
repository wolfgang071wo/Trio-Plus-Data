"""The pedal's analysis of a trained part (block A), see docs/block_a.md."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .tlsd import SAMPLE_RATE, Part, TlsdError

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
SECTION0 = 516
SECTION_SIZE = 3736
SECTION_NAMES = ["4/4", "3/4", "4/4 half tempo", "3/4 half tempo"]

# chord-type code -> (suffix, intervals); "?" marks probable meanings
KINDS = {
    0: ("", (0, 4, 7)),
    2: ("maj7", (0, 4, 7, 11)),
    4: ("m", (0, 3, 7)),
    6: ("m7", (0, 3, 7, 10)),
    8: ("dim", (0, 3, 6)),
    10: ("7", (0, 4, 7, 10)),
    14: ("sus4", (0, 5, 7)),
    15: ("5", (0, 7, 12)),
}
CERTAIN = {0, 2, 4, 6, 14, 15}


def _u32(b: bytes, o: int) -> int:
    return int.from_bytes(b[o:o + 4], "little")


@dataclass
class Chord:
    beat: int  # start, in beats from the beginning of the part
    root: int  # pitch class 0 = C
    kind: int  # raw code

    @property
    def name(self) -> str:
        suffix = KINDS[self.kind][0] if self.kind in KINDS else ""
        return NOTES[self.root] + suffix + ("" if self.kind in CERTAIN else "?")

    @property
    def intervals(self) -> tuple[int, ...]:
        return KINDS.get(self.kind, KINDS[0])[1]


@dataclass
class Analysis:
    section: int
    chosen: bool
    bars: int
    beats_per_bar: int
    pedal_tempo: float  # the pedal's own estimate
    chords: list[Chord]

    @property
    def beats(self) -> int:
        return self.bars * self.beats_per_bar

    def tempo_for(self, seconds: float) -> float:
        """Tempo that makes `beats` fill a loop of `seconds`."""
        return self.beats * 60.0 / seconds if seconds > 0 else self.pedal_tempo


def analyse(part: Part, section: int | None = None) -> Analysis:
    if not part.trained:
        raise TlsdError(f"Part {part.number} ist nicht gelernt")
    a = part.block_a
    if section is None:
        flagged = [s for s in range(4) if _u32(a, SECTION0 + s * SECTION_SIZE) == 1]
        section = flagged[0] if flagged else 0
    if not 0 <= section <= 3:
        raise TlsdError("section must be 0..3")
    base = SECTION0 + section * SECTION_SIZE
    bars, bpb = _u32(a, base + 4), _u32(a, base + 8)
    if not (1 <= bars <= 256 and bpb in (3, 4)):
        raise TlsdError(f"Part {part.number}: keine lesbare Analyse in Abschnitt {section}")
    tempos = []
    for i in range(min(bars, 32)):
        raw = a[base + 408 + 4 * i:base + 412 + 4 * i]
        if raw != b"\0\0\0\0":
            t = struct.unpack("<f", raw)[0]
            if 20 < t < 400:
                tempos.append(t)
    chords: list[Chord] = []
    last = -1
    for i in range(128):
        pos = _u32(a, base + 796 + 4 * i)
        if (i and pos <= last) or pos >= bars * bpb:
            break
        root = _u32(a, base + 1308 + 4 * i)
        kind = _u32(a, base + 1820 + 4 * i)
        if root < 12:
            if not chords or (chords[-1].root, chords[-1].kind) != (root, kind):
                chords.append(Chord(pos, root, kind))
        last = pos
    if not chords:
        raise TlsdError(f"Part {part.number}: keine Akkorde gefunden")
    return Analysis(section, _u32(a, base) == 1, bars, bpb,
                    sum(tempos) / len(tempos) if tempos else 120.0, chords)


def part_seconds(part: Part) -> float:
    """Loop length: the main track, or the declared region if nothing is recorded."""
    main = part.tracks()["main"]
    return (len(main) if main else len(part.region) // 2) / 2 / SAMPLE_RATE
