#!/usr/bin/env python3
"""Pack the metadata area of TRIOPLUS.DAT (everything before the audio slots)
plus optional exported .tlsd files into one small zip for analysis.

Read-only for the inputs.

    python tools/pack_meta.py TRIOPLUS.DAT "Song 03.tlsd" -o trio_meta.zip

Layout found so far: 60 audio slots of 52,920,320 bytes (12 songs x 5 parts,
300 s per track) end exactly at the end of the file, so everything before
`size - 60 * 52,920,320` is song tables and settings. That part is mostly
zeros and compresses to a few hundred KB.
"""

import argparse
import zipfile
from pathlib import Path

SLOT = 52_920_320
SLOTS = 60


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("container")
    ap.add_argument("songs", nargs="*", help="exported .tlsd files to include")
    ap.add_argument("-o", "--output", default="trio_meta.zip")
    args = ap.parse_args()

    size = Path(args.container).stat().st_size
    meta_end = size - SLOTS * SLOT
    if meta_end <= 0 or meta_end > 256 * 2**20:
        meta_end = 64 * 2**20
        print(f"unexpected size, packing the first {meta_end} bytes instead")
    with open(args.container, "rb") as fh:
        meta = fh.read(meta_end)

    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("dat_meta.bin", meta)
        z.writestr("dat_info.txt", f"container size {size}\nmeta bytes 0..{meta_end}\n")
        for s in args.songs:
            z.write(s, arcname=Path(s).name)
    print(f"wrote {args.output} ({Path(args.output).stat().st_size / 2**20:.1f} MB)")


if __name__ == "__main__":
    main()
