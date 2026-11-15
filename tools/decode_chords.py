#!/usr/bin/env python3
"""Experimental: print the chord analysis stored in block A of each part.

    python tools/decode_chords.py SONG.tlsd [--all-sections]

See docs/block_a.md for the layout and what is still unknown.
"""

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trioplus import Song  # noqa: E402

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
SECTION0 = 516
SECTION_SIZE = 3736
NAMES = ["4/4", "3/4", "4/4 half tempo", "3/4 half tempo"]


def u32(b, o):
    return int.from_bytes(b[o:o + 4], "little")


def section(block, s):
    base = SECTION0 + s * SECTION_SIZE
    bars, beats = u32(block, base + 4), u32(block, base + 8)
    tempos = []
    for i in range(min(bars, 32)):
        raw = block[base + 408 + 4 * i:base + 412 + 4 * i]
        if raw != b"\0\0\0\0":
            tempos.append(struct.unpack("<f", raw)[0])
    segs = []
    last = -1
    for i in range(128):
        pos = u32(block, base + 796 + 4 * i)
        if i and pos <= last:
            break
        if pos > bars * beats:
            break
        root, kind = u32(block, base + 1308 + 4 * i), u32(block, base + 1820 + 4 * i)
        segs.append((pos, NOTES[root] if root < 12 else f"?{root}", kind))
        last = pos
    return {"chosen": u32(block, base) == 1, "bars": bars, "beats": beats,
            "tempo": sum(tempos) / len(tempos) if tempos else 0.0, "segments": segs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("songs", nargs="+")
    ap.add_argument("--all-sections", action="store_true")
    args = ap.parse_args()
    for path in args.songs:
        song = Song.load(path)
        print(f"{path}  \"{song.name}\"")
        for p in song.parts:
            if not p.trained:
                continue
            print(f"  part {p.number}")
            for s in range(4):
                sec = section(p.block_a, s)
                if not (sec["chosen"] or args.all_sections):
                    continue
                mark = "*" if sec["chosen"] else " "
                print(f"   {mark} {NAMES[s]:15s} {sec['bars']:3d} bars  ~{sec['tempo']:6.1f} BPM")
                line = "  ".join(f"{pos}:{root}({kind})" for pos, root, kind in sec["segments"])
                print(f"       beat:root(type)  {line}")


if __name__ == "__main__":
    main()
