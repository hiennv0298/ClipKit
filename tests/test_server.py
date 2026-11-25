"""Talk to the real app server over HTTP (Recycle Bin calls are faked)."""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from clipkit import gui, updates


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPKIT_HOME", str(tmp_path / "home"))
    gui.save_settings({"out": str(tmp_path / "out")})
    proj = tmp_path / "out" / "video-abc"
    (proj / "clips").mkdir(parents=True)
    (proj / "info.json").write_text(json.dumps({"title": "Video", "duration": 100}), encoding="utf-8")
    segs = [{"rank": 1, "start": 0, "end": 20, "title": "One"}, {"rank": 2, "start": 30, "end": 50, "title": "Two"}]
    (proj / "segments.json").write_text(json.dumps({"params": {}, "segments": segs}), encoding="utf-8")
    (proj / "clips" / "02_two.mp4").write_bytes(b"x")
    trashed = []
    monkeypatch.setattr(gui, "move_to_trash", lambda p: trashed.append(p.name))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", proj, trashed
    srv.shutdown()


def post(base, path, body, header=True):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"X-ClipKit": "1"} if header else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}") if e.code != 403 else {}


def test_remove_moment_keeps_the_rest_and_trashes_its_clip(server):
    base, proj, trashed = server
    rest = [{"rank": 1, "start": 0, "end": 20, "title": "One"}]
    code, _ = post(base, "/api/moments/remove", {"id": "video-abc", "segments": rest, "files": ["02_two.mp4"]})
    assert code == 200
    assert trashed == ["02_two.mp4", "02_two.srt", "02_two.jpg"]
    assert [s["title"] for s in json.loads((proj / "segments.json").read_text())["segments"]] == ["One"]


def test_remove_rejects_paths_outside_clips(server):
    base, _, trashed = server
    code, body = post(base, "/api/moments/remove", {"id": "video-abc", "segments": [], "files": ["../info.json"]})
    assert code == 400 and not trashed


def test_delete_project_and_header_check(server):
    base, _, trashed = server
    assert post(base, "/api/project/delete", {"id": "video-abc"}, header=False)[0] == 403
    assert post(base, "/api/project/delete", {"id": "video-abc"})[0] == 200
    assert trashed == ["video-abc"]
    assert post(base, "/api/project/delete", {"id": "../.."})[0] == 400


def test_project_list_reports_size(server):
    base, _, _ = server
    with urllib.request.urlopen(base + "/api/projects", timeout=5) as r:
        projects = json.loads(r.read())
    assert projects[0]["id"] == "video-abc" and projects[0]["size"] > 0


def test_relaunch_note_is_read_once_and_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPKIT_HOME", str(tmp_path / "home"))
    path = updates.app_home() / updates.RELAUNCH_FILE
    path.write_text(json.dumps({"port": 51234, "at": time.time()}), encoding="utf-8")
    assert updates.take_relaunch()["port"] == 51234
    assert updates.take_relaunch() is None and not path.exists()
    path.write_text(json.dumps({"port": 51234, "at": time.time() - 3600}), encoding="utf-8")
    assert updates.take_relaunch() is None
