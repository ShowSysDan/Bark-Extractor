"""
ffmpeg-based audio operations for Bark Extractor: peak normalization, trim
(with optional fades), format conversion, probing, and waveform peaks.

Every operation writes a new file next to its source and never modifies the
original. Commands are built here; DownloadManager runs them as jobs.

Normalization is two passes: `volumedetect` measures the sample peak, then
`volume` applies the fixed gain that brings that peak to TARGET_PEAK_DB. A
plain gain change keeps the source's dynamics and sample rate (unlike
single-pass loudnorm, which compresses dynamics, targets -24 LUFS rather than
a peak level, and resamples to 192 kHz).

Normalized output is named <base>_normalized<ext>; those files are recognised
by name so they are never normalized a second time.
"""

import os
import re
import array
import threading
import subprocess

NORMALIZED_SUFFIX = "_normalized"
TRIMMED_SUFFIX = "_trimmed"
TARGET_PEAK_DB = -5.0
# volumedetect reports about -91 dB for digital silence; don't boost that.
_SILENCE_DB = -90.0

# Formats the app can write: extension -> (ffmpeg muxer, encoder args).
# The muxer is explicit because outputs are written to a .part temp file,
# so ffmpeg can't infer the container from the name.
FORMATS = {
    ".mp3":  ("mp3",  ["-c:a", "libmp3lame"]),
    ".m4a":  ("ipod", ["-c:a", "aac"]),
    ".opus": ("opus", ["-c:a", "libopus"]),
    ".ogg":  ("ogg",  ["-c:a", "libvorbis"]),
    ".flac": ("flac", ["-c:a", "flac"]),
    ".wav":  ("wav",  ["-c:a", "pcm_s16le"]),
    ".aiff": ("aiff", ["-c:a", "pcm_s16be"]),
}
_LOSSLESS = {".flac", ".wav", ".aiff"}
# Quality used when converting into a lossy format (source bitrate is not a
# useful guide there – e.g. a FLAC source is ~900 kb/s).
_LOSSY_PRESETS = {
    ".mp3":  ["-q:a", "0"],      # LAME V0, ~245 kb/s VBR
    ".m4a":  ["-b:a", "256k"],
    ".opus": ["-b:a", "160k"],
    ".ogg":  ["-q:a", "6"],      # ~192 kb/s VBR
}
# Encoder bitrate ceilings when re-encoding at the source's bitrate.
_BITRATE_CAPS = {".mp3": 320, ".m4a": 320, ".opus": 256, ".ogg": 320}

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_MAX_VOLUME_RE = re.compile(r"max_volume:\s*(-?[\d.]+|-inf) dB")
_STREAM_BITRATE_RE = re.compile(r"Stream #0:\d+.*?: Audio: .*?(\d+) kb/s")
_FORMAT_BITRATE_RE = re.compile(r"Duration:.*?bitrate:\s*(\d+) kb/s")


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

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


def unique_path(path: str, taken=()) -> str:
    """path, or "<base> (2)<ext>", "(3)"… – the first that doesn't exist
    and isn't in taken (outputs other jobs have already claimed)."""
    base, ext = os.path.splitext(path)
    candidate, n = path, 2
    while os.path.exists(candidate) or candidate in taken:
        candidate = f"{base} ({n}){ext}"
        n += 1
    return candidate


def clean_filename(name: str, ext: str) -> str:
    """
    Validate a user-supplied file name and force it to end in ext.
    Raises ValueError with a user-facing message.
    """
    name = (name or "").strip()
    if name.lower().endswith(ext.lower()):
        name = name[: -len(ext)].rstrip()
    if not name:
        raise ValueError("Name cannot be empty")
    if any(c in name for c in '/\\\0') or name.startswith("."):
        raise ValueError("Name cannot contain slashes or start with a dot")
    if len(name) > 200:
        raise ValueError("Name is too long (200 characters max)")
    return name + ext.lower()


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------

def _progress_args() -> list[str]:
    return ["-progress", "pipe:1", "-nostats"]


def encode_args(ext: str, src_bitrate_kbps: int | None = None) -> list[str]:
    """Encoder args for writing ext. With a source bitrate, lossy formats are
    re-encoded at (up to) that bitrate; otherwise a quality preset is used."""
    ext = ext.lower()
    muxer, codec = FORMATS.get(ext, FORMATS[".mp3"])
    args = list(codec)
    if ext not in _LOSSLESS:
        if src_bitrate_kbps:
            cap = _BITRATE_CAPS.get(ext, 320)
            args += ["-b:a", f"{min(src_bitrate_kbps, cap)}k"]
        else:
            args += _LOSSY_PRESETS.get(ext, [])
    return args + ["-f", muxer]


def analyze_command(ffmpeg: str, src: str) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-i", src,
        "-vn", "-af", "volumedetect", "-f", "null", "-",
        *_progress_args(),
    ]


def apply_gain_command(ffmpeg: str, src: str, tmp_dst: str, gain_db: float,
                       bitrate_kbps: int | None) -> list[str]:
    ext = os.path.splitext(src)[1]
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", src,
        "-vn", "-af", f"volume={gain_db:.2f}dB",
        *encode_args(ext, bitrate_kbps), tmp_dst, *_progress_args(),
    ]


def trim_command(ffmpeg: str, src: str, tmp_dst: str, start: float, end: float,
                 fade_in: float, fade_out: float, bitrate_kbps: int | None) -> list[str]:
    length = end - start
    filters = [f"atrim=start={start:.3f}:end={end:.3f}", "asetpts=PTS-STARTPTS"]
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0:
        filters.append(f"afade=t=out:st={max(0.0, length - fade_out):.3f}:d={fade_out:.3f}")
    ext = os.path.splitext(src)[1]
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", src,
        "-vn", "-af", ",".join(filters),
        *encode_args(ext, bitrate_kbps), tmp_dst, *_progress_args(),
    ]


def convert_command(ffmpeg: str, src: str, tmp_dst: str, target_ext: str) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", src,
        "-vn", *encode_args(target_ext), tmp_dst, *_progress_args(),
    ]


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

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


def probe(ffmpeg: str, src: str) -> tuple[float | None, int | None]:
    """(duration seconds, bitrate kb/s) read from ffmpeg's input header."""
    try:
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-i", src],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    return parse_duration(r.stderr), parse_bitrate(r.stderr)


# ---------------------------------------------------------------------------
# Waveform peaks
# ---------------------------------------------------------------------------

_PEAK_RATE = 4000          # decode rate for the waveform envelope
_peaks_cache: dict[tuple, dict] = {}
_peaks_lock = threading.Lock()
_PEAKS_CACHE_MAX = 64


def waveform_peaks(ffmpeg: str, src: str, buckets: int = 1000) -> dict:
    """
    {"duration": seconds, "peaks": [0..1 per bucket]} – the max absolute
    sample level in each of `buckets` equal slices of the file. Cached per
    (path, mtime, buckets).
    """
    key = (src, os.path.getmtime(src), buckets)
    with _peaks_lock:
        if key in _peaks_cache:
            return _peaks_cache[key]

    r = subprocess.run(
        [ffmpeg, "-v", "error", "-nostdin", "-i", src, "-vn",
         "-ac", "1", "-ar", str(_PEAK_RATE), "-f", "s16le", "-"],
        capture_output=True, timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace").strip()[-300:] or "ffmpeg failed")

    samples = array.array("h")
    samples.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    total = len(samples)
    peaks = []
    if total:
        step = total / buckets
        for i in range(buckets):
            chunk = samples[int(i * step): max(int((i + 1) * step), int(i * step) + 1)]
            if chunk:
                peaks.append(round(max(max(chunk), -min(chunk)) / 32768, 3))
            else:
                peaks.append(0.0)
    result = {"duration": total / _PEAK_RATE, "peaks": peaks}

    with _peaks_lock:
        if len(_peaks_cache) >= _PEAKS_CACHE_MAX:
            _peaks_cache.pop(next(iter(_peaks_cache)))
        _peaks_cache[key] = result
    return result
