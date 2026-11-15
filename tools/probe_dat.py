#!/usr/bin/env python3
"""Map the layout of a TRIO+ card container (TRIOPLUS.DAT).

Read-only. Writes a plain-text report that can be shared for analysis.

    python tools/probe_dat.py TRIOPLUS.DAT -o report.txt
    python tools/probe_dat.py TRIOPLUS.DAT --compare "Song 01.tlsd" -o report.txt

With --compare, pieces of an exported .tlsd (header blocks and the first
audio chunks of every track) are searched for in the container, which shows
where the TRIO Manager takes each part of the song from.
"""

import argparse
import mmap
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trioplus.tlsd import (  # noqa: E402
    BLOCK_A_OFFSET, BLOCK_A_SIZE, BLOCK_B_OFFSET, BLOCK_B_SIZE, CHUNK_SIZE, HEADER_SIZE, Song,
)

MB = 1 << 20
SLOT_MARKER = re.compile(re.escape(bytes.fromhex("03030000")) + b"[ -~]{2}")


def u32(buf, off):
    return int.from_bytes(buf[off:off + 4], "little")


def runs(buf, gap=64):
    """Coalesced (start, end) ranges of non-zero bytes."""
    out = []
    for m in re.finditer(rb"[^\x00]+", buf):
        s, e = m.span()
        if out and s - out[-1][1] <= gap:
            out[-1][1] = e
        else:
            out.append([s, e])
    return out


def hexdump(buf, base, limit=256):
    lines = []
    for i in range(0, min(len(buf), limit), 32):
        lines.append(f"    {base + i:#010x}  {buf[i:i + 32].hex()}")
    return lines


def interesting(segment):
    """A segment worth searching for: not empty and not a single repeated byte."""
    return len(set(segment)) > 8


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("container")
    ap.add_argument("--compare", help="an exported .tlsd of a song that is on this card")
    ap.add_argument("-o", "--output", default="probe_report.txt")
    ap.add_argument("--header-window", type=int, default=128, help="MB searched for header pieces (default 128)")
    args = ap.parse_args()

    out = []
    log = out.append
    t0 = time.time()
    with open(args.container, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as m:
        size = len(m)
        log(f"container  {args.container}")
        log(f"size       {size} bytes ({size / MB:.1f} MB)")

        # 1. container header
        log("\n== container header: non-zero ranges in the first 1 MB ==")
        head = m[:MB]
        for s, e in runs(head)[:40]:
            log(f"  {s:#010x}-{e:#010x} ({e - s} B)")
            out.extend(hexdump(head[s:e], s, 96))

        # 2. song slots
        log("\n== song slots (0x0303 marker + name, slot start = marker - 1328) ==")
        first_mb = m[:args.header_window * MB]
        slots = [x.start() - 1328 for x in SLOT_MARKER.finditer(first_mb)]
        for n, s in enumerate(slots, 1):
            name = first_mb[s + 1332:s + 1460].split(b"\x00", 1)[0].decode("ascii", "replace")
            step = slots[n] - s if n < len(slots) else None
            log(f"  slot {n:2d} at {s:#010x} ({s})  name {name!r}  next slot +{step}")
        if slots:
            stride = slots[1] - slots[0] if len(slots) > 1 else 65536
            for n, s in enumerate(slots, 1):
                rec = m[max(0, s - 4096):s + stride]
                base = max(0, s - 4096)
                log(f"\n  slot {n} non-zero ranges (from slot-4096 to next slot):")
                for a, b in runs(rec)[:30]:
                    log(f"    {base + a:#010x}-{base + b:#010x} ({b - a} B), rel {base + a - s:+d}")
                    if n <= 2:
                        out.extend("  " + line for line in hexdump(rec[a:b], base + a, 160))

        # 3. coarse occupancy map
        log("\n== occupancy map, 1 MB blocks (# = contains data, . = all zero) ==")
        row = []
        for i in range(0, size, MB):
            row.append("#" if any(m[i:i + MB]) else ".")
        for i in range(0, len(row), 100):
            log(f"  {i:5d} MB  {''.join(row[i:i + 100])}")
        used = [i for i, c in enumerate(row) if c == "#"]
        log(f"  blocks with data: {len(used)} of {len(row)}")

        # 4. comparison with an exported song
        if args.compare:
            song = Song.load(args.compare)
            raw = Path(args.compare).read_bytes()
            log(f"\n== compare with {args.compare} ({len(raw)} bytes, name {song.name!r}) ==")
            for p in song.parts:
                if p.trained:
                    log(f"  part {p.number}: region {len(p.region)} B")

            log(f"\n  header pieces (512 B) searched in the first {args.header_window} MB:")
            window = m[:args.header_window * MB]
            regions = [("global", 0, BLOCK_A_OFFSET)]
            regions += [(f"A{i + 1}", BLOCK_A_OFFSET + i * BLOCK_A_SIZE, BLOCK_A_SIZE) for i in range(5)]
            regions += [(f"B{i + 1}", BLOCK_B_OFFSET + i * BLOCK_B_SIZE, BLOCK_B_SIZE) for i in range(5)]
            for label, off, length in regions:
                found = []
                for k in range(off, off + length - 511, 512):
                    seg = raw[k:k + 512]
                    if interesting(seg):
                        hit = window.find(seg)
                        found.append(f"{k - off:+d}->{hit:#x}" if hit >= 0 else f"{k - off:+d}->-")
                if found:
                    log(f"    {label:6s} " + " ".join(found[:24]) + (" ..." if len(found) > 24 else ""))

            log("\n  audio: first non-zero 32 KB chunks of each track, searched in the whole file:")
            for p in song.parts:
                if not p.trained:
                    continue
                for track, pcm in p.tracks().items():
                    chunks = [pcm[i:i + CHUNK_SIZE] for i in range(0, len(pcm), CHUNK_SIZE)]
                    nz = [i for i, c in enumerate(chunks) if len(c) == CHUNK_SIZE and any(c)]
                    for idx in nz[:3]:
                        hit = m.find(chunks[idx])
                        log(f"    part {p.number} {track:7s} chunk {idx:3d} -> "
                            + (f"{hit:#x} ({hit})" if hit >= 0 else "not found"))
            region_hits = []
            for p in song.parts:
                if p.trained:
                    head32 = p.region[:CHUNK_SIZE * 2]
                    for i in range(0, len(head32), CHUNK_SIZE):
                        c = head32[i:i + CHUNK_SIZE]
                        if any(c):
                            region_hits.append((p.number, i, m.find(c)))
            log("\n  region chunks as stored in the .tlsd (interleaved):")
            for num, i, hit in region_hits:
                log(f"    part {num} region+{i:#x} -> " + (f"{hit:#x}" if hit >= 0 else "not found"))

    log(f"\ndone in {time.time() - t0:.1f} s")
    Path(args.output).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"report written to {args.output}")


if __name__ == "__main__":
    main()
