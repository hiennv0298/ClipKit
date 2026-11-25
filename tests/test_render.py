from clipkit.render import encoder_args, gop_size, video_filter
from clipkit.util import Cancelled, check_cancel, request_cancel, reset_cancel


class _FF:
    ffmpeg = "ffmpeg"


def test_gop_is_half_the_frame_rate():
    assert gop_size("30") == 15
    assert gop_size("25") == 12 or gop_size("25") == 13
    assert gop_size("30000/1001") == 15
    assert gop_size("60") == 30


def test_filter_keeps_source_fps_and_burns_captions():
    graph = video_filter("blur", 0.5, "clip.ass", "25")
    assert ",fps=25," in graph and graph.endswith(",ass=clip.ass[v]")


def test_cpu_encoder_follows_youtube_recommendations():
    args = encoder_args(_FF(), "cpu", "30")
    for flag, value in (("-profile:v", "high"), ("-bf", "2"), ("-g", "15")):
        assert args[args.index(flag) + 1] == value
    assert "+cgop" in args


def test_cancel_flag():
    reset_cancel()
    check_cancel()
    request_cancel()
    try:
        check_cancel()
    except Cancelled:
        pass
    else:
        raise AssertionError("expected Cancelled")
    finally:
        reset_cancel()
