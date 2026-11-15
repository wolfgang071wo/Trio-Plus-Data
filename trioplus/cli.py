"""Non-interactive command line for TRIO+ song files.

    trio info SONG... [--json]
    trio extract SONG... [-o DIR] [--tracks main,overdub] [--trim] [--json]
    trio build -o OUT [--name NAME] PART=SONG:PART ...
    trio card TRIOPLUS.DAT [--extract DIR] [--song N] [--json]
    trio scan IMAGE [--extract DIR] [--json]
    trio rename SONG NAME -o OUT

Nothing is ever written to an input file, and existing outputs are only
replaced with --force.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import wave
from pathlib import Path

from . import __version__
from .tlsd import NUM_PARTS, SAMPLE_RATE, SAMPLE_WIDTH, TRACKS, Song, TlsdError, trim_silence


class CliError(Exception):
    pass


# -- helpers -------------------------------------------------------------

def _check_output(out: Path, inputs: list[Path], force: bool) -> None:
    for src in inputs:
        if out.exists() and src.exists() and out.resolve() == src.resolve():
            raise CliError(f"refusing to overwrite the input file {src}")
    if out.exists() and not force:
        raise CliError(f"{out} exists, use --force to replace it")


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)


def _emit(obj, as_json: bool, text: str) -> None:
    print(json.dumps(obj, indent=2) if as_json else text)


def _part_line(p: dict) -> str:
    if not p["trained"]:
        return f"  part {p['part']}  empty"
    bits = []
    for name in TRACKS:
        t = p["tracks"][name]
        bits.append(f"{name} {t['seconds']:7.2f} s" if t["has_audio"] else f"{name}      -   ")
    return f"  part {p['part']}  " + "   ".join(bits)


# -- commands ------------------------------------------------------------

def cmd_info(args) -> int:
    results = []
    for path in args.songs:
        song = Song.load(path)
        info = {"file": str(path), **song.describe()}
        results.append(info)
        if not args.json:
            print(f"{path}  \"{info['name']}\"  {info['file_bytes']} bytes")
            for p in info["parts"]:
                print(_part_line(p))
    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2))
    return 0


def cmd_extract(args) -> int:
    tracks = [t.strip() for t in args.tracks.split(",") if t.strip()]
    for t in tracks:
        if t not in TRACKS:
            raise CliError(f"unknown track {t!r}, choose from {', '.join(TRACKS)}")
    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for path in args.songs:
        song = Song.load(path)
        entry = {"file": str(path), "name": song.name, "wav": []}
        for part in song.parts:
            pcm_by_track = part.tracks()
            for t in tracks:
                pcm = pcm_by_track[t]
                if not any(pcm):
                    continue
                if args.trim:
                    pcm = trim_silence(pcm)
                out = outdir / f"{path.stem}_part{part.number}_{t}.wav"
                _check_output(out, args.songs, args.force)
                _write_wav(out, pcm)
                entry["wav"].append({
                    "path": str(out), "part": part.number, "track": t,
                    "samples": len(pcm) // SAMPLE_WIDTH,
                    "seconds": round(len(pcm) / SAMPLE_WIDTH / SAMPLE_RATE, 3),
                })
                if not args.json:
                    print(f"wrote {out}")
        if not entry["wav"] and not args.json:
            print(f"{path}: no recorded audio", file=sys.stderr)
        manifest.append(entry)
    if args.json:
        print(json.dumps(manifest, indent=2))
    return 0


_SPEC = re.compile(r"^(?P<dst>[1-5])=(?P<file>.+):(?P<src>[1-5])$")


def cmd_build(args) -> int:
    out = Path(args.output)
    song = Song.empty()
    cache: dict[Path, Song] = {}
    used: set[int] = set()
    inputs = []
    for spec in args.parts:
        m = _SPEC.match(spec)
        if not m:
            raise CliError(f"bad part spec {spec!r}, expected DST=FILE:SRC like 1=song.tlsd:3")
        dst, src = int(m["dst"]), int(m["src"])
        if dst in used:
            raise CliError(f"part {dst} is given twice")
        used.add(dst)
        file = Path(m["file"])
        inputs.append(file)
        if file not in cache:
            cache[file] = Song.load(file)
        source = cache[file].part(src)
        if not source.trained:
            raise CliError(f"{file} part {src} is empty")
        song.set_part(dst, source)

    song.name = args.name if args.name is not None else out.stem
    _check_output(out, inputs, args.force)
    data = song.to_bytes()
    Song.from_bytes(data)  # sanity check before writing
    out.write_bytes(data)
    _emit({"file": str(out), **song.describe()}, args.json, f"wrote {out}")
    return 0


def cmd_scan(args) -> int:
    from .scan import scan

    outdir = Path(args.extract) if args.extract else None
    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)
    found = []
    for n, (hit, data) in enumerate(scan(args.container), start=1):
        entry = {**hit.__dict__}
        if outdir and data is not None:
            safe = re.sub(r"[^A-Za-z0-9 _.@+-]", "_", hit.name).strip() or "song"
            out = outdir / f"{n:02d} {safe}.tlsd"
            _check_output(out, [args.container], args.force)
            out.write_bytes(data)
            entry["written"] = str(out)
        found.append(entry)
        if not args.json:
            state = "ok" if hit.valid else f"skipped: {hit.reason}"
            parts = ",".join(map(str, hit.trained_parts)) or "-"
            line = f"{n:2d}  @{hit.offset:#012x}  {hit.size:>10} B  parts {parts:<9}  \"{hit.name}\"  {state}"
            if "written" in entry:
                line += f"  -> {entry['written']}"
            print(line)
    if args.json:
        print(json.dumps(found, indent=2))
    elif not found:
        print(f"{args.container}: no TRIO+ song headers found", file=sys.stderr)
        return 1
    return 0


def cmd_card(args) -> int:
    from .card import NUM_SONGS, Card

    outdir = Path(args.extract) if args.extract else None
    wanted = set(args.song) if args.song else set(range(1, NUM_SONGS + 1))
    report = {"file": str(args.card), "songs": []}
    with Card(args.card) as card:
        names = card.song_names()
        if outdir:
            outdir.mkdir(parents=True, exist_ok=True)
        for number in range(1, NUM_SONGS + 1):
            slots = card.song_slots(number)
            entry = {
                "song": number,
                "name": names[number - 1],
                "parts": [{"part": n, "slot": s.index, "region_bytes": s.region_bytes,
                           "seconds": round(s.region_bytes / 4 / 44100, 2)}
                          for n, s in enumerate(slots, start=1) if s.present],
            }
            if outdir and entry["parts"] and number in wanted:
                safe = re.sub(r"[^A-Za-z0-9 _.@+-]", "_", entry["name"]).strip() or "Song"
                out = outdir / f"{number:02d} {safe}.tlsd"
                _check_output(out, [args.card], args.force)
                data = card.song(number).to_bytes()
                Song.from_bytes(data)
                out.write_bytes(data)
                entry["written"] = str(out)
            report["songs"].append(entry)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    for e in report["songs"]:
        if e["parts"]:
            parts = ", ".join(f"part {p['part']} {p['seconds']:.1f} s" for p in e["parts"])
        else:
            parts = "empty"
        line = f"  {e['song']:2d}  {e['name']:<16} {parts}"
        if "written" in e:
            line += f"  -> {e['written']}"
        print(line)
    return 0


def cmd_rename(args) -> int:
    song = Song.load(args.song)
    song.name = args.name
    out = Path(args.output)
    _check_output(out, [args.song], args.force)
    song.save(out)
    _emit({"file": str(out), "name": song.name}, args.json, f"wrote {out} as \"{song.name}\"")
    return 0


# -- entry point ---------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="trio", description="Work with DigiTech TRIO+ song files (.tlsd).")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p, json_help="print machine-readable JSON"):
        p.add_argument("--json", action="store_true", help=json_help)
        return p

    p = common(sub.add_parser("info", help="show the parts of one or more songs"))
    p.add_argument("songs", nargs="+", type=Path)
    p.set_defaults(func=cmd_info)

    p = common(sub.add_parser("extract", help="export recorded audio as WAV"))
    p.add_argument("songs", nargs="+", type=Path)
    p.add_argument("-o", "--output", default="Trio_wav_export", help="output folder (default: %(default)s)")
    p.add_argument("--tracks", default="main,overdub", help="comma list of main, overdub (default: both)")
    p.add_argument("--trim", action="store_true", help="strip trailing silence")
    p.add_argument("--force", action="store_true", help="replace existing WAV files")
    p.set_defaults(func=cmd_extract)

    p = common(sub.add_parser(
        "build",
        help="build a new song from parts of existing songs",
        description="Each PART is DST=FILE:SRC, e.g. 1=intro.tlsd:2 puts part 2 of intro.tlsd "
                    "into part 1 of the new song. Parts not listed stay empty, so build also "
                    "covers reordering, copying and deleting parts.",
    ))
    p.add_argument("parts", nargs="+", metavar="PART")
    p.add_argument("-o", "--output", required=True, type=Path)
    p.add_argument("--name", help="song name (default: output file name)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_build)

    p = common(sub.add_parser(
        "scan",
        help="find songs inside TRIOPLUS.DAT or an SD card image",
        description="Search a container file for TRIO+ song headers and optionally write each "
                    "complete song out as a .tlsd file. The container is only read.",
    ))
    p.add_argument("container", type=Path)
    p.add_argument("--extract", metavar="DIR", help="write every valid song to DIR as .tlsd")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = common(sub.add_parser(
        "card",
        help="read recorded parts straight from TRIOPLUS.DAT",
        description="List the 12 songs in a TRIO+ card file and optionally write the "
                    "recorded ones out as .tlsd, like a TRIO Manager export. Read-only.",
    ))
    p.add_argument("card", type=Path)
    p.add_argument("--extract", metavar="DIR", help="write every song with audio to DIR as .tlsd")
    p.add_argument("--song", type=int, action="append", metavar="N",
                   help="only extract song N (1..12), may be repeated")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_card)

    p = common(sub.add_parser("rename", help="write a copy of a song with a new name"))
    p.add_argument("song", type=Path)
    p.add_argument("name")
    p.add_argument("-o", "--output", required=True, type=Path)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_rename)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (CliError, TlsdError, OSError) as e:
        print(f"trio: error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
