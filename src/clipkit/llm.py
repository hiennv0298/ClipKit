"""Optional: let an OpenAI-compatible model pick the moments (--selector llm).

Off by default because it costs tokens. Configure with environment variables:
  CLIPKIT_LLM_BASE_URL  e.g. https://ark.ap-southeast.bytepluses.com/api/v3
  CLIPKIT_LLM_API_KEY   your key
  CLIPKIT_LLM_MODEL     e.g. deepseek-v4-1-flash-260910
One request per video: the transcript is sent once, compactly, and the answer is
capped by CLIPKIT_LLM_MAX_TOKENS (default 2000).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from clipkit.select import build_units, to_segment
from clipkit.util import ClipKitError, log

PROMPT = """Bạn là biên tập viên video ngắn. Dưới đây là bản ghi một video, mỗi dòng là [giây_bắt_đầu] câu nói.
Chọn {count} đoạn hay nhất để làm video ngắn dọc, mỗi đoạn dài {min_len:.0f}-{max_len:.0f} giây, không chồng lấn,
mỗi đoạn phải là một ý trọn vẹn và câu đầu tiên phải gây tò mò.
Trả về DUY NHẤT một mảng JSON, không giải thích:
[{{"start": <giây bắt đầu, lấy đúng số trong ngoặc>, "end": <giây kết thúc>, "title": "<tiêu đề hấp dẫn, cùng ngôn ngữ với video, tối đa 70 ký tự>"}}]

BẢN GHI:
{transcript}"""


def _config() -> tuple[str, str, str]:
    base = os.environ.get("CLIPKIT_LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("CLIPKIT_LLM_API_KEY", "")
    model = os.environ.get("CLIPKIT_LLM_MODEL", "")
    if not (base and key and model):
        raise ClipKitError("--selector llm cần đặt CLIPKIT_LLM_BASE_URL, CLIPKIT_LLM_API_KEY và CLIPKIT_LLM_MODEL.")
    return base, key, model


def pick_segments_llm(transcript: dict, count: int, min_len: float, max_len: float) -> list[dict]:
    base, key, model = _config()
    units = build_units(transcript.get("segments", []))
    if not units:
        return []
    lines = "\n".join(f"[{u.start:.1f}] {u.text}" for u in units)
    body = {
        "model": model,
        "temperature": 0.3,
        "max_tokens": int(os.environ.get("CLIPKIT_LLM_MAX_TOKENS", "2000")),
        "messages": [{"role": "user", "content": PROMPT.format(
            count=count, min_len=min_len, max_len=max_len, transcript=lines)}],
    }
    req = urllib.request.Request(
        f"{base}/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    log(f"Đang hỏi {model} chọn đoạn…")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ClipKitError(f"LLM trả lỗi HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
    except OSError as exc:
        raise ClipKitError(f"Không gọi được LLM: {exc}") from exc
    usage = data.get("usage") or {}
    if usage:
        log(f"  Token đã dùng: {usage.get('prompt_tokens')} vào + {usage.get('completion_tokens')} ra")
    content = data["choices"][0]["message"].get("content") or ""
    match = re.search(r"\[.*\]", content, re.S)
    if not match:
        raise ClipKitError("LLM không trả về JSON hợp lệ.")
    picks = json.loads(match.group(0))

    out: list[dict] = []
    for item in picks:
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        window = [u for u in units if u.start >= start - 1.0 and u.end <= end + 1.5] or \
                 [u for u in units if u.end > start and u.start < end]
        if not window:
            continue
        s, e = window[0].start, window[-1].end
        if e - s < min_len * 0.6 or e - s > max_len * 1.3:
            continue
        if any(not (e <= o["start"] or s >= o["end"]) for o in out):
            continue
        out.append(to_segment(len(out) + 1, s, e, window, title=str(item.get("title") or "") or None,
                              reasons=["chọn bởi LLM"]))
    return out[:count]
