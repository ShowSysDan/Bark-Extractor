"""
Peak normalization helpers for Bark Extractor.

Normalization is two ffmpeg passes: `volumedetect` measures the sample peak,
then `volume` applies the fixed gain that brings that peak to TARGET_PEAK_DB.
A plain gain change keeps the source's dynamics and sample rate (unlike
single-pass loudnorm, which compresses dynamics, targets -24 LUFS rather than
a peak level, and resamples to 192 kHz).

Output is written next to the original as <base>_normalized<ext>. Those files
are recognised by name so they are never normalized a second time.
"""

import os
import re

NORMALIZED_SUFFIX = "_normalized"
TARGET_PEAK_DB = -5.0
# volumedetect reports about -91 dB for digital silence; don't boost that.
_SILENCE_DB = -90.0

# ffmpeg muxer per extension – the temp file ends in .part, so ffmpeg can't
# infer the container from the name.
_MUXERS = {
    ".mp3":  "mp3",
    ".m4a":  "ipod",
    ".opus": "opus",
    ".flac": "flac",
    ".ogg":  "ogg",
    ".wav":  "wav",
}
_LOSSLESS = {".flac", ".wav"}

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_MAX_VOLUME_RE = re.compile(r"max_volume:\s*(-?[\d.]+|-inf) dB")
_STREAM_BITRATE_RE = re.compile(r"Stream #0:\d+.*?: Audio: .*?(\d+) kb/s")
_FORMAT_BITRATE_RE = re.compile(r"Duration:.*?bitrate:\s*(\d+) kb/s")


def is_normalized_name(path: str) -> bool:
    """True when path is a normalized output (<base>_normalized<ext>)."""
    base = os.path.splitext(os.path.basename(path))[0]
    return base.endswith(NORMALIZED_SUFFIX)


def normalized_path_for(path: str) -> str:
    base, ext = os.path.splitext(path)
    return f"{base}{NORMALIZED_SUFFIX}{ext}"


def normalized_is_current(src: str) -> bool:
    """True when the normalized copy of src exists and is newer than src."""
    dst = normalized_path_for(src)
    try:
        return os.path.getmtime(dst) >= os.path.getmtime(src)
    except OSError:
        return False


def analyze_command(ffmpeg: str, src: str) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-i", src,
        "-vn", "-af", "volumedetect", "-f", "null", "-",
        "-progress", "pipe:1", "-nostats",
    ]


def apply_command(ffmpeg: str, src: str, tmp_dst: str, gain_db: float,
                  bitrate_kbps: int | None) -> list[str]:
    ext = os.path.splitext(src)[1].lower()
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", src,
        "-vn", "-af", f"volume={gain_db:.2f}dB",
    ]
    # Re-encode lossy formats at the source bitrate instead of ffmpeg's
    # (often much lower) encoder default.
    if bitrate_kbps and ext not in _LOSSLESS:
        cmd += ["-b:a", f"{bitrate_kbps}k"]
    cmd += ["-f", _MUXERS.get(ext, "mp3"), tmp_dst, "-progress", "pipe:1", "-nostats"]
    return cmd


def parse_duration(stderr: str) -> float | None:
    m = _DURATION_RE.search(stderr)
    if not m:
        return None
    h, mnt, s = m.groups()
    secs = int(h) * 3600 + int(mnt) * 60 + float(s)
    return secs or None


def parse_bitrate(stderr: str) -> int | None:
    m = _STREAM_BITRATE_RE.search(stderr) or _FORMAT_BITRATE_RE.search(stderr)
    return int(m.group(1)) if m else None


def parse_gain(stderr: str) -> float | None:
    """Gain in dB that moves the measured peak to TARGET_PEAK_DB, or None
    when volumedetect produced no measurement."""
    m = _MAX_VOLUME_RE.search(stderr)
    if not m:
        return None
    if m.group(1) == "-inf":
        return 0.0
    peak = float(m.group(1))
    if peak <= _SILENCE_DB:
        return 0.0
    return TARGET_PEAK_DB - peak
