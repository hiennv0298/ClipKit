"""Step 2: speech to text with faster-whisper (word timestamps) + loudness profile."""
from __future__ import annotations

import os
import time
import warnings
import wave
from pathlib import Path

import numpy as np

from clipkit.deps import FFmpeg, cuda_devices
from clipkit.util import Cancelled, ClipKitError, app_home, check_cancel, fmt_ts, log, run, write_json

SAMPLE_RATE = 16000
ENERGY_STEP = 0.5  # seconds per loudness sample


def extract_audio(ff: FFmpeg, video: Path, wav: Path, section: tuple[float, float] | None) -> None:
    cmd = [ff.ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if section:
        cmd += ["-ss", f"{section[0]:.3f}", "-to", f"{section[1]:.3f}"]
    cmd += ["-i", video, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", wav]
    run(cmd)


def load_wav(wav: Path) -> np.ndarray:
    """16 kHz mono PCM -> float32 in [-1, 1] (what Whisper expects)."""
    with wave.open(str(wav), "rb") as wf:
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def energy_profile(wav: Path) -> list[float]:
    """Loudness in dBFS for every ENERGY_STEP seconds of audio."""
    samples = load_wav(wav)
    step = int(SAMPLE_RATE * ENERGY_STEP)
    n = len(samples) // step
    if n == 0:
        return []
    frames = samples[: n * step].reshape(n, step)
    rms = np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-6
    return [round(float(x), 2) for x in 20 * np.log10(rms)]


def _load_model(name: str, device: str):
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    warnings.filterwarnings("ignore", module="huggingface_hub")
    from faster_whisper import WhisperModel

    compute = "float16" if device == "cuda" else "int8"
    models = app_home() / "whisper-models"
    log(f"Loading Whisper '{name}' on {device.upper()} (the model is downloaded on first use)...")
    return WhisperModel(name, device=device, compute_type=compute, download_root=str(models))


def _run_whisper(model, wav: Path, language: str | None, total: float, offset: float) -> tuple[list[dict], str]:
    # Pass samples, not the path: faster-whisper's own decoder depends on PyAV,
    # whose newer releases changed av.open() and break it.
    segments_iter, info = model.transcribe(
        load_wav(wav),
        language=language or None,
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=False,
    )
    out: list[dict] = []
    last_report = time.monotonic()
    for seg in segments_iter:
        check_cancel()
        words = [
            {"start": round(w.start + offset, 3), "end": round(w.end + offset, 3), "word": w.word.strip()}
            for w in (seg.words or []) if w.word.strip()
        ]
        out.append({
            "start": round(seg.start + offset, 3),
            "end": round(seg.end + offset, 3),
            "text": seg.text.strip(),
            "words": words,
        })
        if time.monotonic() - last_report > 15 and total:
            last_report = time.monotonic()
            log(f"  transcribed {min(100, int(seg.end * 100 / total))}% ({fmt_ts(seg.end)} / {fmt_ts(total)})")
    return out, info.language


def cuda_marker() -> Path:
    """Exists when Whisper on CUDA failed before (usually missing cuBLAS/cuDNN)."""
    return app_home() / "whisper-cuda-unavailable"


def transcribe(wav: Path, model_name: str, language: str | None, device: str, offset: float = 0.0) -> dict:
    if device == "auto":
        device = "cuda" if cuda_devices() > 0 and not cuda_marker().exists() else "cpu"
    total = _wav_duration(wav)
    started = time.monotonic()
    try:
        segments, lang = _run_whisper(_load_model(model_name, device), wav, language, total, offset)
    except Cancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - CUDA libs missing shows up here at runtime
        if device != "cuda":
            raise ClipKitError(f"Whisper failed: {exc}") from exc
        log(f"  Whisper could not run on the GPU ({exc.__class__.__name__}: CUDA libraries missing?), switching to CPU...")
        cuda_marker().write_text(str(exc)[:500], encoding="utf-8")
        device = "cpu"
        segments, lang = _run_whisper(_load_model(model_name, device), wav, language, total, offset)
    took = time.monotonic() - started
    log(f"  Transcript done: {len(segments)} segments, language '{lang}', took {fmt_ts(took)}.")
    return {"language": lang, "model": model_name, "device": device, "segments": segments}


def write_srt(segments: list[dict], path: Path, offset: float = 0.0) -> None:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines += [str(i), f"{_srt_time(seg['start'] - offset)} --> {_srt_time(seg['end'] - offset)}", seg["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def _srt_time(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _wav_duration(wav: Path) -> float:
    with wave.open(str(wav), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


def save_transcript(workdir: Path, data: dict, energy: list[float], energy_offset: float) -> None:
    data = dict(data, energy={"step": ENERGY_STEP, "offset": energy_offset, "db": energy})
    write_json(workdir / "transcript.json", data)
    write_srt(data["segments"], workdir / "transcript.srt")
