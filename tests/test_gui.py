import json

import pytest

from clipkit import gui
from clipkit.util import ClipKitError


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPKIT_HOME", str(tmp_path / "home"))
    return tmp_path


def test_settings_round_trip_and_types(tmp_path):
    s = gui.save_settings({"clips": "3", "min_len": "15", "captions": 0, "language": " vi ", "bogus": 1})
    assert s["clips"] == 3 and s["min_len"] == 15.0 and s["captions"] is False and s["language"] == "vi"
    assert "bogus" not in s
    assert gui.load_settings()["clips"] == 3


def test_bad_number_is_rejected():
    with pytest.raises(ClipKitError):
        gui.save_settings({"clips": "many"})


def test_options_validation():
    s = gui.load_settings()
    assert gui.options_from(s).clips == 5
    with pytest.raises(ClipKitError):
        gui.options_from(dict(s, min_len=60, max_len=30))
    with pytest.raises(ClipKitError):
        gui.options_from(dict(s, max_len=400))
    with pytest.raises(ClipKitError):
        gui.options_from(dict(s, section="5:00-1:00"))


def test_clean_segments_keeps_known_fields():
    segs = gui.clean_segments([{"start": "1.234", "end": 30, "title": " Hi ", "enabled": 0, "evil": "x"}])
    assert segs == [{"start": 1.23, "end": 30.0, "title": "Hi", "enabled": False, "rank": 1}]
    with pytest.raises(ClipKitError):
        gui.clean_segments([{"start": 10, "end": 10.5}])


def test_project_dir_rejects_paths(tmp_path):
    gui.save_settings({"out": str(tmp_path / "out")})
    proj = tmp_path / "out" / "video-abc"
    proj.mkdir(parents=True)
    (proj / "info.json").write_text(json.dumps({"title": "Video"}), encoding="utf-8")
    assert gui.project_dir("video-abc") == proj
    for bad in ("", "..", "../x", "a\\b", "C:x"):
        with pytest.raises(ClipKitError):
            gui.project_dir(bad)
    assert [p["id"] for p in gui.list_projects()] == ["video-abc"]


def test_progress_parsing():
    st = gui.AppState()
    st.on_log("[2/4] Transcribing with Whisper")
    st.on_log("  transcribed 50% (1:00 / 2:00)")
    assert st.step == 2 and st.progress == 15 + 55 * 0.5
    st.on_log("[4/4] Rendering clips")
    st.on_log("      Clip 2/4: 0:10-0:40 -> 02_x.mp4")
    assert st.progress == pytest.approx(75 + 25 * 0.25, abs=0.1)
