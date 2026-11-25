import pytest

from clipkit.captions import build_ass, chunk_words, clip_words, escape
from clipkit.render import video_filter
from clipkit.util import parse_section, parse_ts, slugify


def words(text, start=0.0, step=0.4):
    return [{"start": start + i * step, "end": start + i * step + 0.3, "word": w} for i, w in enumerate(text.split())]


def test_escape_blocks_override_tags():
    assert escape("a{\\b1}b") == "a(/b1)b"


def test_chunks_break_on_punctuation_and_size():
    chunks = chunk_words(words("một hai ba. bốn năm sáu bảy tám"), max_words=4)
    assert [len(c) for c in chunks] == [3, 4, 1]


def test_ass_has_title_and_one_event_per_word():
    ass = build_ass(words("xin chào thế giới"), duration=3.0, title="Tiêu đề", caption_y=1480)
    assert "Title,,0,0,0,,Tiêu đề" in ass
    assert ass.count(",Caption,,") == 4
    assert "PlayResY: 1920" in ass


def test_clip_words_are_relative_to_clip_start():
    transcript = {"segments": [{"start": 10, "end": 12, "text": "a b", "words": words("a b", start=10)}]}
    out = clip_words(transcript, 10, 12)
    assert out[0]["start"] == pytest.approx(0.0)


@pytest.mark.parametrize("layout", ["blur", "crop", "fit"])
def test_filters_end_in_named_output(layout):
    graph = video_filter(layout, 0.5, "x.ass")
    assert graph.endswith(",ass=x.ass[v]") and "1080" in graph


def test_time_parsing():
    assert parse_ts("1:15") == 75
    assert parse_ts("0:01:15.5") == 75.5
    assert parse_section("10:00-25:00") == (600, 1500)
    with pytest.raises(ValueError):
        parse_section("5:00-1:00")


def test_slugify_folds_vietnamese():
    assert slugify("Đây là Tiêu đề hay!") == "day-la-tieu-de-hay"
