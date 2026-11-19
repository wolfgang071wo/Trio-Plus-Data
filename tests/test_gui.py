import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trioplus import Song  # noqa: E402
from trioplus.gui.server import make_server  # noqa: E402

SONG_05 = ROOT / "tlsd_sample_data" / "05.tlsd"


@pytest.fixture()
def server(tmp_path):
    srv, lib, token = make_server("127.0.0.1", 18765, tmp_path / "export")
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, body=None, token_ok=True):
        url = base + path + ("&" if "?" in path else "?") + ("t=" + token if token_ok else "")
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if r.headers.get_content_type() == "application/json" else raw

    yield call, tmp_path
    srv.shutdown()
    srv.server_close()


def test_page_and_token(server):
    call, _ = server
    assert b"TRIO Tool" in call("/")
    with pytest.raises(urllib.error.HTTPError) as e:
        call("/api/state", token_ok=False)
    assert e.value.code == 403


def test_open_play_export_build(server):
    call, tmp = server
    st = call("/api/open", {"path": str(SONG_05)})["state"]
    song = st["songs"][0]
    assert song["name"] == "Song 05" and song["recorded"]
    sid = song["id"]
    peaks = call(f"/api/peaks?song={sid}&part=1&track=main&n=50")
    assert len(peaks["peaks"]) == 50 and peaks["seconds"] > 20
    wav = call(f"/api/wav?song={sid}&part=1&track=overdub")
    assert wav[:4] == b"RIFF"
    lay = call(f"/api/wav?song={sid}&part=4&track=layer")
    assert lay[:4] == b"RIFF"
    files = call("/api/export", {"song": sid, "what": "wav"})["files"]
    assert len(files) == 7
    tl = call("/api/export", {"song": sid, "what": "tlsd"})["files"][0]
    assert Song.load(tl).to_bytes() == Song.load(SONG_05).to_bytes()
    r = call("/api/build", {"name": "Neu", "parts": [{"song": sid, "part": 4}, None, {"song": sid, "part": 1}]})
    built = Song.load(r["file"])
    assert built.name == "Neu" and built.part(1).region == Song.load(SONG_05).part(4).region
    assert not built.part(2).trained
    assert len(r["state"]["sources"]) == 2


def test_errors_are_reported(server):
    call, tmp = server
    with pytest.raises(urllib.error.HTTPError) as e:
        call("/api/open", {"path": str(tmp / "missing.tlsd")})
    assert e.value.code == 400


def test_opening_again_rereads_the_file(server, tmp_path):
    import shutil
    call, _ = server
    f = tmp_path / "song.tlsd"
    shutil.copy(ROOT / "tlsd_sample_data" / "01.tlsd", f)
    st = call("/api/open", {"path": str(f)})["state"]
    assert [p["trained"] for p in st["songs"][0]["parts"]][:3] == [True, True, False]
    shutil.copy(SONG_05, f)  # the file changes on disk
    st = call("/api/open", {"path": str(f)})["state"]
    assert len(st["sources"]) == 1
    assert [p["trained"] for p in st["songs"][0]["parts"]][:4] == [True, True, True, True]


def test_english(server, tmp_path):
    call, _ = server
    from trioplus.i18n import tr
    assert tr("Part 3: keine Akkorde gefunden", "en") == "Part 3: no chords found"
    assert tr("Wähle mindestens einen Part", "de") == "Wähle mindestens einen Part"
    with pytest.raises(urllib.error.HTTPError) as e:
        call("/api/open?lang=en", {"path": str(tmp_path / "nope.tlsd")})
    assert "does not exist" in json.loads(e.value.read())["error"]
    sid = call("/api/open", {"path": str(SONG_05)})["state"]["songs"][0]["id"]
    folder = Path(call("/api/export?lang=en", {"song": sid, "what": "daw"})["files"][0])
    assert sorted(p.name for p in folder.iterdir()) == ["Guitar.wav", "README.txt", "Song (fixed tempo).mid", "Song.mid", "Song.musicxml"]
    assert "song package" in (folder / "README.txt").read_text(encoding="utf-8")


def test_rename(server, tmp_path):
    call, _ = server
    f = tmp_path / "copy.tlsd"
    f.write_bytes(SONG_05.read_bytes())
    call("/api/open", {"path": str(SONG_05)})
    st = call("/api/open", {"path": str(f)})["state"]
    sid = [x for x in st["songs"] if x["file"] == "copy.tlsd"][0]["id"]
    r = call("/api/rename", {"song": sid, "name": "Mein Blues"})
    assert Song.load(f).name == "Mein Blues"
    assert [s["name"] for s in r["state"]["sources"]] == ["05.tlsd", "copy.tlsd"]  # order kept
    with pytest.raises(urllib.error.HTTPError) as e:
        call("/api/rename", {"song": f"{r['opened'][0]}:0", "name": "Gr\u00fc\u00dfe"})
    assert "Umlaute" in json.loads(e.value.read())["error"]


@pytest.mark.skipif(sys.platform == "win32", reason="needs a sparse 3 GB file")
def test_rename_on_card(server, tmp_path):
    from test_tlsd import make_card
    from trioplus.card import Card
    call, _ = server
    path, _ = make_card(tmp_path)
    first = call("/api/open", {"path": str(path)})["state"]["sources"][0]["changes"]
    assert first["first"]
    r = call("/api/rename", {"song": "s1:2", "name": "Blues in A"})
    src = r["state"]["sources"][0]
    assert src["changes"]["first"]  # not reported as "nothing saved"
    assert [s["name"] for s in r["state"]["songs"]][:2] == ["Song 01", "Blues in A"]
    with Card(path) as c:
        assert c.song_names()[1] == "Blues in A"
