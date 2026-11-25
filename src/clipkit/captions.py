"""Step 4a: burned-in captions (ASS) with the spoken word highlighted, plus a title."""
from __future__ import annotations

import re
from pathlib import Path

from clipkit.select import SENTENCE_END, segment_words

WIDTH, HEIGHT = 1080, 1920
HIGHLIGHT = "&H0000E5FF&"  # ASS colours are &HAABBGGRR -> warm yellow


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape(text: str) -> str:
    """Neutralise characters that ASS would treat as override tags."""
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def clip_words(transcript: dict, start: float, end: float) -> list[dict]:
    words = []
    for seg in transcript.get("segments", []):
        if seg["end"] < start or seg["start"] > end:
            continue
        for w in segment_words(seg):
            if w["start"] >= start - 0.05 and w["end"] <= end + 0.05:
                words.append({"start": w["start"] - start, "end": w["end"] - start, "word": w["word"]})
    return words


def chunk_words(words: list[dict], max_words: int = 4, max_secs: float = 1.8) -> list[list[dict]]:
    """Group words into short caption lines that break at punctuation and pauses."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (len(cur) >= max_words or w["end"] - cur[0]["start"] > max_secs
                    or w["start"] - cur[-1]["end"] > 0.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if SENTENCE_END.search(w["word"]) or w["word"].endswith(","):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[dict], duration: float, title: str | None, caption_y: int,
              font: str = "Arial", uppercase: bool = False) -> str:
    margin_v = HEIGHT - caption_y
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},74,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,70,70,{margin_v},1
Style: Title,{font},62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,3,18,0,8,80,80,230,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    if title:
        events.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(duration)},Title,,0,0,0,,{escape(title)}")

    chunks = chunk_words(words)
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else duration
        chunk_end = min(next_start, chunk[-1]["end"] + 0.4, duration)
        tokens = [escape(w["word"].upper() if uppercase else w["word"]) for w in chunk]
        for wi, w in enumerate(chunk):
            t0 = w["start"] if wi else max(0.0, w["start"] - 0.05)
            t1 = chunk[wi + 1]["start"] if wi + 1 < len(chunk) else chunk_end
            if t1 <= t0:
                continue
            text = " ".join(f"{{\\c{HIGHLIGHT}}}{tok}{{\\c&H00FFFFFF&}}" if k == wi else tok
                            for k, tok in enumerate(tokens))
            events.append(f"Dialogue: 0,{_ass_time(t0)},{_ass_time(t1)},Caption,,0,0,0,,{text}")
    return header + "\n".join(events) + "\n"


def build_srt(words: list[dict]) -> str:
    from clipkit.transcribe import _srt_time

    lines = []
    for i, chunk in enumerate(chunk_words(words, max_words=8, max_secs=3.5), 1):
        text = re.sub(r"\s+", " ", " ".join(w["word"] for w in chunk)).strip()
        lines += [str(i), f"{_srt_time(chunk[0]['start'])} --> {_srt_time(chunk[-1]['end'])}", text, ""]
    return "\n".join(lines)


def write_caption_files(transcript: dict, start: float, end: float, title: str | None,
                        ass_path: Path, srt_path: Path, caption_y: int, captions: bool = True,
                        uppercase: bool = False) -> None:
    words = clip_words(transcript, start, end)
    ass = build_ass(words if captions else [], end - start, title, caption_y, uppercase=uppercase)
    ass_path.write_text(ass, encoding="utf-8")
    srt_path.write_text(build_srt(words), encoding="utf-8")
