from clipkit.select import build_units, hook_features, make_title, pick_segments


def seg(start, words, gap=0.3):
    """Build a Whisper-like segment with evenly spaced words."""
    out, t = [], start
    for w in words.split():
        out.append({"start": t, "end": t + gap, "word": w})
        t += gap + 0.05
    return {"start": start, "end": out[-1]["end"], "text": words, "words": out}


def test_units_split_on_punctuation_and_pauses():
    segs = [seg(0, "Xin chào các bạn. Hôm nay"), seg(5, "chúng ta nói về tiền")]
    units = build_units(segs)
    assert [u.text for u in units] == ["Xin chào các bạn.", "Hôm nay", "chúng ta nói về tiền"]


def test_hook_rewards_questions_and_penalises_connectives():
    good = build_units([seg(0, "Tại sao bạn mãi không giàu?")])
    weak = build_units([seg(0, "Và thế là mình đi về.")])
    assert hook_features(good)[0] > 0
    assert hook_features(weak)[0] < 0


def test_pick_segments_respects_length_and_overlap():
    segs = [seg(i * 6, "Đây là một câu nói khá dài để làm dữ liệu thử nghiệm.") for i in range(40)]
    picks = pick_segments({"segments": segs}, count=3, min_len=20, max_len=40)
    assert 1 <= len(picks) <= 3
    for p in picks:
        assert 19 <= p["end"] - p["start"] <= 41
    spans = sorted((p["start"], p["end"]) for p in picks)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))


def test_pick_prefers_the_hooky_window():
    filler = "ờ thì cái này cũng bình thường thôi mọi người."
    segs = [seg(i * 6, filler) for i in range(30)]
    segs[15] = seg(90, "Bí mật lớn nhất để kiếm tiền là gì?")
    picks = pick_segments({"segments": segs}, count=1, min_len=15, max_len=30)
    assert picks[0]["start"] <= 90.0 <= picks[0]["end"]
    assert picks[0]["title"].startswith("Bí mật")


def test_title_is_trimmed_and_capitalised():
    units = build_units([seg(0, "một câu rất dài " * 10)])
    title = make_title(units, max_chars=30)
    assert len(title) <= 31 and title[0].isupper() and title.endswith("…")


def test_empty_transcript_gives_no_segments():
    assert pick_segments({"segments": []}) == []
