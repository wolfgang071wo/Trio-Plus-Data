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


@pytest.mark.skipif(sys.platform == "win32", reason="needs a sparse 3 GB file")
def test_card_container(tmp_path):
    from trioplus import card as C

    src = Song.load(SONG_05)
    size = C.RECORD_TABLE + 2 * C.NUM_SLOTS * C.RECORD_STRIDE + 4096 + C.NUM_SLOTS * C.AUDIO_SLOT
    path = tmp_path / "TRIOPLUS.DAT"
    with open(path, "wb") as f:
        f.truncate(size)
        f.write(b"TLSD\x00\x00\x00\x00Trio+ Card")
        for i in range(C.NUM_SONGS):
            f.seek(C.SONG_TABLE + i * C.SONG_STRIDE)
            f.write((0x0303).to_bytes(4, "little") + f"Song {i + 1:02d}".encode())
        empty = Song.empty().part(5)
        base = size - C.NUM_SLOTS * C.AUDIO_SLOT
        for j in range(C.NUM_SLOTS):
            p = src.parts[j - 5] if 5 <= j < 10 else empty  # second group holds the song
            f.seek(C.RECORD_TABLE + j * C.RECORD_STRIDE)
            f.write(p.block_a)
            f.seek(C.RECORD_TABLE + (C.NUM_SLOTS + j) * C.RECORD_STRIDE)
            f.write(p.block_b)
            if p.trained:
                f.seek(base + j * C.AUDIO_SLOT + C.AUDIO_LEAD)
                f.write(p.region)

    out = tmp_path / "out"
    assert main(["card", "--extract", str(out), str(path)]) == 0
    got = Song.load(out / "02 Song 02.tlsd")
    assert got.name == "Song 02"
    for a, b in zip(src.parts, got.parts):
        assert a.trained == b.trained
        if a.trained:
            assert (a.region, a.block_a, a.block_b) == (b.region, b.block_a, b.block_b)
    with C.Card(path) as card:
        assert card.song_names()[2] == "Song 03"
        assert [s.index for s in card.slots() if s.present] == [5, 6, 7, 8]


def _write_wav(path, seconds, rate=44100, channels=1, width=2, freq=220.0):
    import math
    import struct
    frames = bytearray()
    for i in range(int(seconds * rate)):
        v = int(0.5 * 32767 * math.sin(2 * math.pi * freq * i / rate))
        if width == 2:
            s = struct.pack("<h", v)
        else:  # 24 bit
            s = (v << 8).to_bytes(3, "little", signed=True)
        frames += s * channels
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def test_import_wav_into_trained_part(tmp_path):
    from trioplus.wavein import import_wav, read_wav_mono16

    src = ROOT / "tlsd_sequence_in_a" / "01.tlsd"  # trained, no audio
    wav = tmp_path / "in.wav"
    _write_wav(wav, 2.0, rate=48000, channels=2, width=3)
    pcm = read_wav_mono16(wav)
    assert abs(len(pcm) / 2 - 2.0 * 44100) < 2

    song = Song.load(src)
    part = song.part(1)
    assert part.layers == 0 and part.current_track is None
    new, info = import_wav(part, pcm, "replace")
    assert new.layers == 1 and new.current_track == "main"
    assert int.from_bytes(new.block_b[8:12], "little") == int.from_bytes(new.block_b[4:8], "little")
    assert int.from_bytes(new.block_a[16256:16260], "little") == 1
    assert len(new.region) == len(part.region)
    tr = new.tracks()
    assert tr["main"][:len(pcm)] == pcm[:len(tr["main"])]
    assert not any(tr["overdub"])
    song.set_part(1, new)
    again = Song.from_bytes(song.to_bytes()).part(1)
    assert again.tracks()["main"] == tr["main"]


def test_import_wav_mix_uses_current_track(tmp_path):
    from trioplus.wavein import import_wav, read_wav_mono16

    wav = tmp_path / "pad.wav"
    _write_wav(wav, 1.0)
    part = Song.load(SONG_05).part(4)  # 4 layers -> overdub track is current
    new, info = import_wav(part, read_wav_mono16(wav), "mix", gain=0.3)
    assert info["track"] == "overdub" and new.layers == 4
    assert new.tracks()["main"] == part.tracks()["main"]
    assert new.tracks()["overdub"] != part.tracks()["overdub"]


def test_cli_import_wav(tmp_path):
    wav = tmp_path / "in.wav"
    _write_wav(wav, 30.0)
    out = tmp_path / "with_wav.tlsd"
    src = ROOT / "tlsd_sequence_in_a" / "02.tlsd"
    assert main(["import-wav", str(src), "1", str(wav), "-o", str(out)]) == 0
    p = Song.load(out).part(1)
    assert p.current_track == "main" and p.track_has_audio("main")
    assert main(["import-wav", str(src), "3", str(wav), "-o", str(tmp_path / "x.tlsd")]) == 2  # part 3 not trained


def test_last_layer_is_newer_minus_older():
    from trioplus.layers import last_layer

    part = Song.load(SONG_05).part(4)  # 4 layers: overdub is the newer mix
    r = last_layer(part)
    assert (r.newer, r.older, r.undone) == ("overdub", "main", False)
    assert 0.9 < r.gain < 1.1 and r.lag == 0
    assert len(r.pcm) == len(part.tracks()["overdub"])
    assert r.level_ratio < 0.8
    with pytest.raises(TlsdError):
        last_layer(Song.load(SONG_05).part(2))  # single layer


def test_cli_extract_layer(tmp_path):
    assert main(["extract", "--tracks", "layer", "-o", str(tmp_path), str(SONG_05)]) == 0
    assert sorted(p.name for p in tmp_path.glob("*.wav")) == [
        "05_part1_layer.wav", "05_part3_layer.wav", "05_part4_layer.wav"]


def test_analysis_reads_roots_of_test_series():
    from trioplus.analysis import analyse

    for f, root in [("tlsd-without-audio/Song C1 @68.6.tlsd", 0), ("tlsd-without-audio/Song D1 @70.0.tlsd", 2),
                    ("tlsd_sequence_in_a/01.tlsd", 9)]:
        an = analyse(Song.load(ROOT / f).part(1))
        assert an.chords[0].root == root
        assert an.beats_per_bar in (3, 4) and an.bars >= 1


def test_cli_midi(tmp_path):
    assert main(["midi", "-o", str(tmp_path), str(SONG_05)]) == 0
    files = sorted(tmp_path.glob("*.mid"))
    assert len(files) == 4
    data = files[0].read_bytes()
    assert data[:4] == b"MThd" and data.count(b"MTrk") == 3
    try:
        import mido
    except ImportError:
        return
    m = mido.MidiFile(files[0])
    part = Song.load(SONG_05).part(1)
    assert abs(m.length - len(part.tracks()["main"]) / 2 / 44100) < 0.05
    assert any(msg.type == "note_on" for msg in m.tracks[2])


def test_smooth_folds_transitional_codes():
    from trioplus.analysis import Chord, smooth

    seq = [Chord(0, 5, 0), Chord(1, 5, 1), Chord(2, 5, 0), Chord(8, 7, 0)]
    assert [(c.beat, c.root, c.kind) for c in smooth(seq)] == [(0, 5, 0), (8, 7, 0)]
    seq = [Chord(0, 9, 0), Chord(4, 2, 20), Chord(5, 2, 0), Chord(8, 4, 0)]
    assert [(c.beat, c.root, c.kind) for c in smooth(seq)] == [(0, 9, 0), (4, 2, 0), (8, 4, 0)]
    long_unknown = [Chord(0, 2, 20), Chord(8, 4, 0)]
    assert smooth(long_unknown)[0].kind == 20


def test_card_history(tmp_path):
    from trioplus.history import compare

    base = {"time": 1.0, "selected": 6, "slots": [{"h": "x", "present": False}] * 60}
    same = compare(base, dict(base, time=2.0))
    assert same["nothing"]
    slots = list(base["slots"])
    slots[26] = {"h": "y", "present": True}
    rep = compare(base, {"time": 2.0, "selected": 7, "slots": slots})
    assert rep["added"] == [{"song": 6, "part": 2}] and not rep["nothing"] and rep["selected_now"] == 7
    assert compare(None, base)["first"]
