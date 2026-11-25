"""Step 3: pick the best moments from the transcript — no API, no tokens.

Every window of whole sentences between --min and --max seconds is scored on:
  * speech density (words per second) and how much of the window is speech,
  * loudness relative to the whole video (energetic moments are louder),
  * a hook in the first sentences (question, "secret", "mistake", numbers…),
  * starting cleanly (not on "và / nhưng / so / but…") and ending on a full stop.
The best non-overlapping windows win. Results go to segments.json, which you can
edit by hand and re-render with `clipkit render`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

HOOK_WORDS = [
    # Vietnamese
    "tại sao", "vì sao", "bí mật", "sự thật", "sai lầm", "quan trọng nhất", "đừng", "không ai",
    "ít ai", "điều gì", "làm sao", "làm thế nào", "bài học", "thất bại", "thành công", "tiền",
    "triệu", "tỷ", "thực ra", "thật ra", "hóa ra", "bạn có biết", "câu chuyện", "duy nhất",
    "sốc", "nguy hiểm", "cách để", "bí quyết",
    # English
    "why", "secret", "truth", "mistake", "never", "nobody", "most important", "how to", "lesson",
    "fail", "money", "million", "billion", "actually", "the reason", "you know what", "the key",
]
WEAK_STARTS = [
    "và", "nhưng", "thì", "mà", "rồi", "nên", "cho nên", "vì vậy", "à", "ừ", "ờ", "ừm", "thế",
    "vậy", "vậy thì", "còn", "tức là", "kiểu", "xong", "đấy", "and", "but", "so", "then", "because", "um", "uh", "or",
    "also", "like",
]
SENTENCE_END = re.compile(r"[.!?…。！？]['\")\]]*$")


@dataclass
class Unit:
    start: float
    end: float
    words: list[dict] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w["word"] for w in self.words).strip()

    @property
    def n_words(self) -> int:
        return len(self.words)

    @property
    def speech(self) -> float:
        return sum(max(0.0, w["end"] - w["start"]) for w in self.words)


def segment_words(seg: dict) -> list[dict]:
    """Word list of a segment; spread the text evenly when Whisper gave no words."""
    if seg.get("words"):
        return seg["words"]
    tokens = seg.get("text", "").split()
    if not tokens:
        return []
    span = (seg["end"] - seg["start"]) / len(tokens)
    return [{"start": seg["start"] + i * span, "end": seg["start"] + (i + 1) * span, "word": t}
            for i, t in enumerate(tokens)]


def build_units(segments: list[dict], pause: float = 0.7, max_unit: float = 12.0) -> list[Unit]:
    """Split the transcript into sentence-like units (punctuation or pauses)."""
    units: list[Unit] = []
    cur: list[dict] = []

    def flush() -> None:
        if cur:
            units.append(Unit(cur[0]["start"], cur[-1]["end"], list(cur)))
            cur.clear()

    for seg in segments:
        for w in segment_words(seg):
            if cur and (w["start"] - cur[-1]["end"] > pause or cur[-1]["end"] - cur[0]["start"] > max_unit):
                flush()
            cur.append(w)
            if SENTENCE_END.search(w["word"]):
                flush()
    flush()
    return units


def _norm(text: str) -> str:
    return " " + re.sub(r"[^\w\s]", " ", text.lower()) + " "


def hook_features(units: list[Unit]) -> tuple[float, list[str]]:
    first = units[0].text
    opening = _norm(" ".join(u.text for u in units[:2]))
    score, reasons = 0.0, []
    if "?" in first:
        score += 0.8
        reasons.append("opens with a question")
    hits = [w for w in HOOK_WORDS if f" {w} " in opening]
    if hits:
        score += min(1.0, 0.5 * len(hits))
        reasons.append("keywords: " + ", ".join(hits[:3]))
    if re.search(r"\d", first):
        score += 0.3
        reasons.append("has a number")
    start_norm = _norm(first).strip()
    if any(start_norm == w or start_norm.startswith(w + " ") for w in WEAK_STARTS):
        score -= 1.0
        reasons.append("starts with a connective (penalty)")
    if units[0].n_words <= 2:
        score -= 0.3
    return score, reasons


class _Energy:
    def __init__(self, energy: dict | None):
        energy = energy or {}
        self.step = float(energy.get("step") or 0.5)
        self.offset = float(energy.get("offset") or 0.0)
        self.db = np.asarray(energy.get("db") or [], dtype=np.float32)
        self.median = float(np.median(self.db)) if self.db.size else 0.0

    def stats(self, start: float, end: float) -> tuple[float, float]:
        if not self.db.size:
            return 0.0, 0.0
        a = max(0, int((start - self.offset) / self.step))
        b = min(self.db.size, max(a + 1, int((end - self.offset) / self.step)))
        chunk = self.db[a:b]
        return (float(chunk.mean()) - self.median, float(chunk.std())) if chunk.size else (0.0, 0.0)


def _zscore(values: list[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    std = arr.std()
    return (arr - arr.mean()) / std if std > 1e-9 else np.zeros_like(arr)


def pick_segments(transcript: dict, count: int = 5, min_len: float = 20.0, max_len: float = 60.0,
                  gap: float = 1.0) -> list[dict]:
    units = build_units(transcript.get("segments", []))
    if not units:
        return []
    energy = _Energy(transcript.get("energy"))
    target = (min_len + max_len) / 2

    cands: list[dict] = []
    for lo in (min_len, min_len / 2):  # relax once for very short videos
        for i in range(len(units)):
            words = 0
            speech = 0.0
            for j in range(i, len(units)):
                start, end = units[i].start, units[j].end
                dur = end - start
                if dur > max_len:
                    break
                words += units[j].n_words
                speech += units[j].speech
                if dur < lo:
                    continue
                e_mean, e_std = energy.stats(start, end)
                cands.append({"i": i, "j": j, "start": start, "end": end, "dur": dur,
                              "density": words / dur, "coverage": min(1.0, speech / dur),
                              "e_mean": e_mean, "e_std": e_std})
        if cands:
            break
    if not cands:
        return []

    z_density = _zscore([c["density"] for c in cands])
    z_cover = _zscore([c["coverage"] for c in cands])
    z_energy = _zscore([c["e_mean"] for c in cands])
    z_dyn = _zscore([c["e_std"] for c in cands])
    for k, c in enumerate(cands):
        window = units[c["i"]: c["j"] + 1]
        hook, reasons = hook_features(window)
        ends_clean = bool(SENTENCE_END.search(window[-1].words[-1]["word"])) if window[-1].words else False
        score = (1.0 * z_density[k] + 0.5 * z_cover[k] + 0.8 * z_energy[k] + 0.3 * z_dyn[k]
                 + hook + (0.4 if ends_clean else -0.4)
                 - 0.6 * abs(c["dur"] - target) / max(1.0, max_len - min_len))
        if z_density[k] > 1:
            reasons.append("fast, dense speech")
        if z_energy[k] > 1:
            reasons.append("louder than average")
        c.update(score=float(score), reasons=reasons, units=window)

    chosen: list[dict] = []
    for c in sorted(cands, key=lambda c: c["score"], reverse=True):
        if all(c["end"] + gap <= o["start"] or c["start"] >= o["end"] + gap for o in chosen):
            chosen.append(c)
            if len(chosen) >= count:
                break

    return [to_segment(rank, c["start"], c["end"], c["units"], score=c["score"], reasons=c["reasons"])
            for rank, c in enumerate(chosen, 1)]


def make_title(units: list[Unit], max_chars: int = 70) -> str:
    text = units[0].text if units else ""
    if len(text) < 25 and len(units) > 1:
        text = f"{text} {units[1].text}"
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    text = text.rstrip(",;:-–")
    return text[:1].upper() + text[1:]


def to_segment(rank: int, start: float, end: float, units: list[Unit], score: float | None = None,
               reasons: list[str] | None = None, title: str | None = None) -> dict:
    return {
        "rank": rank,
        "start": round(max(0.0, start - 0.15), 2),
        "end": round(end + 0.35, 2),
        "title": title or make_title(units),
        "score": round(score, 3) if score is not None else None,
        "reasons": reasons or [],
        "text": " ".join(u.text for u in units),
        "enabled": True,
    }


def units_between(transcript: dict, start: float, end: float) -> list[Unit]:
    return [u for u in build_units(transcript.get("segments", [])) if u.end > start and u.start < end]
