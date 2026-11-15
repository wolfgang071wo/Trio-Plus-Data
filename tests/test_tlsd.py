import json
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "The_Trio_Tool"))

from trioplus import Song, TlsdError, trim_silence  # noqa: E402
from trioplus.cli import main  # noqa: E402
from trioplus.tlsd import HEADER_SIZE  # noqa: E402

SAMPLES = sorted(ROOT.glob("tlsd*/*.tlsd"))
SONG_01 = ROOT / "tlsd_sample_data" / "01.tlsd"
SONG_05 = ROOT / "tlsd_sample_data" / "05.tlsd"


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_roundtrip_is_byte_identical(path):
    data = path.read_bytes()
    assert Song.from_bytes(data).to_bytes() == data


def test_template_is_empty():
    song = Song.empty()
    assert song.name == "Template"
    assert not any(p.trained for p in song.parts)


def test_part_detection_matches_original_tool():
    from resource_files import trio_rsc  # original parser

    for path in SAMPLES:
        data = path.read_bytes()
        theirs = trio_rsc.getPartInfo(data)
        ours = Song.from_bytes(data).parts
        for t, o in zip(theirs, ours):
            assert bool(t.get_trained()) == o.trained, path
            assert t.has_audio() == o.track_has_audio("main"), path
            assert t.has_overdub() == o.track_has_audio("overdub"), path


def test_audio_matches_original_tool():
    from resource_files import trio_rsc

    data = SONG_05.read_bytes()
    theirs = trio_rsc.getPartInfo(data)
    song = Song.from_bytes(data)
    for t, o in zip(theirs, song.parts):
        if not t.has_audio():
            continue
        main_chunks, *od = trio_rsc.formAudioParts([t], data)
        ref_main = b"".join(main_chunks)
        assert trim_silence(o.tracks()["main"]) == trim_silence(ref_main)
        if od:
            assert trim_silence(o.tracks()["overdub"]) == trim_silence(b"".join(od[0]))


def test_rebuilding_a_song_from_its_own_parts():
    original = Song.load(SONG_01)
    rebuilt = Song.empty()
    for p in original.parts:
        if p.trained:
            rebuilt.set_part(p.number, p)
    rebuilt.name = original.name
    a, b = original.to_bytes(), rebuilt.to_bytes()
    assert len(a) == len(b)
    assert a[HEADER_SIZE:] == b[HEADER_SIZE:]  # audio
    reread = Song.from_bytes(b)
    for p, q in zip(original.parts, reread.parts):
        if p.trained:  # settings of every recorded part survive unchanged
            assert (p.block_a, p.block_b) == (q.block_a, q.block_b)


def test_reorder_parts():
    src = Song.load(SONG_05)
    new = Song.empty()
    new.set_part(1, src.part(4))
    new.set_part(2, src.part(1))
    out = Song.from_bytes(new.to_bytes())
    assert out.part(1).region == src.part(4).region
    assert out.part(2).region == src.part(1).region
    assert not out.part(3).trained
    assert len(out.to_bytes()) == HEADER_SIZE + len(src.part(4).region) + len(src.part(1).region)


def test_clear_part():
    song = Song.load(SONG_01)
    song.clear_part(2)
    out = Song.from_bytes(song.to_bytes())
    assert out.part(1).trained and not out.part(2).trained


def test_name_validation():
    song = Song.empty()
    song.name = "Riff in A"
    assert Song.from_bytes(song.to_bytes()).name == "Riff in A"
    with pytest.raises(TlsdError):
        song.name = "x" * 128
    with pytest.raises(TlsdError):
        song.name = "Größe"


def test_rejects_garbage():
    with pytest.raises(TlsdError):
        Song.from_bytes(b"\x00" * 100)
    data = bytearray(SONG_01.read_bytes())
    with pytest.raises(TlsdError):
        Song.from_bytes(bytes(data[:-10]))


# -- command line --------------------------------------------------------

def test_cli_info_json(capsys):
    assert main(["info", "--json", str(SONG_05)]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["name"] == "Song 05"
    assert [p["trained"] for p in info["parts"]] == [True, True, True, True, False]


def test_cli_extract(tmp_path, capsys):
    assert main(["extract", "--json", "-o", str(tmp_path), str(SONG_05)]) == 0
    manifest = json.loads(capsys.readouterr().out)
    wavs = manifest[0]["wav"]
    assert len(wavs) == 7  # 4 main + 3 overdub
    with wave.open(wavs[0]["path"]) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 44100)
        assert w.getnframes() == wavs[0]["samples"]
    # second run without --force must refuse
    assert main(["extract", "-o", str(tmp_path), str(SONG_05)]) == 2


def test_cli_build_and_rename(tmp_path):
    out = tmp_path / "new.tlsd"
    assert main(["build", "-o", str(out), f"1={SONG_05}:3", f"2={SONG_01}:1"]) == 0
    song = Song.load(out)
    assert song.name == "new"
    assert song.part(1).region == Song.load(SONG_05).part(3).region
    renamed = tmp_path / "renamed.tlsd"
    assert main(["rename", str(out), "Chorus", "-o", str(renamed)]) == 0
    assert Song.load(renamed).name == "Chorus"
    # never in place, empty sources and duplicate targets are errors
    assert main(["rename", str(out), "X", "-o", str(out), "--force"]) == 2
    assert main(["build", "-o", str(tmp_path / "e.tlsd"), f"1={SONG_01}:5"]) == 2
    assert main(["build", "-o", str(tmp_path / "d.tlsd"), f"1={SONG_01}:1", f"1={SONG_01}:2"]) == 2


def test_cli_scan_container(tmp_path, capsys):
    a, b = SONG_01.read_bytes(), SONG_05.read_bytes()
    container = tmp_path / "TRIOPLUS.DAT"
    container.write_bytes(b"\xff" * 4096 + a + b"\x00" * 50000 + b + b[:200000])
    out = tmp_path / "songs"
    assert main(["scan", "--json", "--extract", str(out), str(container)]) == 0
    hits = json.loads(capsys.readouterr().out)
    assert [h["valid"] for h in hits] == [True, True, False]
    assert hits[0]["offset"] == 4096
    written = sorted(out.glob("*.tlsd"))
    assert [p.read_bytes() for p in written] == [a, b]


def test_cli_scan_nothing_found(tmp_path):
    empty = tmp_path / "x.dat"
    empty.write_bytes(b"\x00" * 200000)
    assert main(["scan", str(empty)]) == 1
