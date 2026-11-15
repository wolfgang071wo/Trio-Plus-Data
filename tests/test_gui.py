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
