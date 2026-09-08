"""
External tool manager for Bark Extractor.

Keeps the two external tools the app depends on present and current:

  * yt-dlp – the downloader binary. YouTube extraction rots quickly, so a
    binary pinned in git goes stale within months. Instead of tracking it,
    the app downloads the latest release on startup when the binary is
    missing, and self-updates it overnight every few days.

  * Deno – the JavaScript runtime yt-dlp needs to run YouTube's player code
    (signature / nsig solving). Without one, YouTube downloads fail with
    HTTP 403 because only unsigned legacy formats are offered. Deno is a
    single static binary; it is downloaded into ./bin/ when missing and
    ./bin is prepended to PATH so yt-dlp finds it automatically.

Everything here uses only the Python standard library.

Configuration (via environment / .env):
    YTDLP_AUTO_UPDATE          "true"/"false"  – enable overnight updates (default true)
    YTDLP_UPDATE_INTERVAL_DAYS integer         – days between updates (default 3)
    YTDLP_UPDATE_HOUR          0-23            – local hour of the update window (default 3)
    YTDLP_UPDATE_CHANNEL       stable|nightly|master – release channel (default nightly)
"""

import os
import sys
import stat
import time
import shutil
import zipfile
import platform
import tempfile
import threading
import subprocess
import urllib.request
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
BIN_DIR = BASE_DIR / "bin"
_STAMP_FILE = BIN_DIR / ".ytdlp_last_update"

# Release repos per update channel. Each publishes the same asset names.
_CHANNEL_REPOS = {
    "stable": "yt-dlp/yt-dlp",
    "nightly": "yt-dlp/yt-dlp-nightly-builds",
    "master": "yt-dlp/yt-dlp-master-builds",
}

_DOWNLOAD_TIMEOUT = 300      # seconds for a whole tool download
_VERSION_TIMEOUT = 30        # seconds for a --version probe
_UPDATE_TIMEOUT = 300        # seconds for a self-update run

# Cached versions so /api/version doesn't spawn a subprocess per request.
_versions = {"ytdlp": None, "deno": None}
_update_lock = threading.Lock()


def _default_log(msg: str):
    print(f"  [tools] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _download(url: str, dest: Path, log=_default_log):
    """Download url to dest atomically (tempfile + rename)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=".dl-")
    tmp = Path(tmp_name)
    try:
        log(f"Downloading {url}")
        with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as resp, os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(resp, out)
        tmp.replace(dest)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _make_executable(path: Path):
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _probe_version(cmd: list[str]) -> str | None:
    """Run `<tool> --version`; return the first line, or None on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=_VERSION_TIMEOUT)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _resolve_ytdlp(ytdlp_path: str) -> Path | None:
    """Resolve YTDLP_PATH to an absolute Path (relative paths are relative to the app dir)."""
    if os.path.sep not in ytdlp_path and "/" not in ytdlp_path:
        # Bare command name – resolve via PATH; not managed by us.
        found = shutil.which(ytdlp_path)
        return Path(found) if found else None
    p = Path(ytdlp_path)
    return p if p.is_absolute() else (BASE_DIR / p).resolve()


def _is_managed(ytdlp_path: str) -> bool:
    """We only download/replace the binary when it lives inside the app directory."""
    if os.path.sep not in ytdlp_path and "/" not in ytdlp_path:
        return False
    p = Path(ytdlp_path)
    p = p if p.is_absolute() else (BASE_DIR / p)
    try:
        p.resolve().relative_to(BASE_DIR)
        return True
    except ValueError:
        return False


def _channel_url(channel: str) -> str:
    repo = _CHANNEL_REPOS.get(channel, _CHANNEL_REPOS["nightly"])
    return f"https://github.com/{repo}/releases/latest/download/yt-dlp"


# ---------------------------------------------------------------------------
# yt-dlp
# ---------------------------------------------------------------------------

def ensure_ytdlp(ytdlp_path: str, channel: str = "nightly", log=_default_log) -> str | None:
    """
    Make sure yt-dlp exists and runs. Downloads the latest release when the
    configured binary is missing (only for paths inside the app directory).
    Returns the version string, or None if unavailable.
    """
    target = _resolve_ytdlp(ytdlp_path)

    if target and target.is_file():
        try:
            _make_executable(target)
        except OSError:
            pass
        version = _probe_version([str(target), "--version"])
        if version:
            _versions["ytdlp"] = version
            log(f"yt-dlp {version} at {target}")
            return version
        log(f"yt-dlp at {target} is present but not runnable – re-downloading")

    if not _is_managed(ytdlp_path):
        log(f"yt-dlp not found at {ytdlp_path!r} and path is not app-managed – "
            "install it manually or set YTDLP_PATH to a path inside the app directory")
        return None

    target = (BASE_DIR / ytdlp_path).resolve() if not Path(ytdlp_path).is_absolute() else Path(ytdlp_path)
    _download(_channel_url(channel), target, log)
    _make_executable(target)
    version = _probe_version([str(target), "--version"])
    _versions["ytdlp"] = version
    _write_stamp()
    log(f"yt-dlp downloaded: {version or '(version check failed)'}")
    return version


def update_ytdlp(ytdlp_path: str, channel: str = "nightly", log=_default_log) -> bool:
    """
    Update yt-dlp: try the binary's own self-updater first, fall back to a
    fresh download of the latest release. Returns True on success.
    Safe while downloads are running – replacement is an atomic rename, and
    an in-flight yt-dlp process keeps its already-open copy.
    """
    with _update_lock:
        target = _resolve_ytdlp(ytdlp_path)
        if target and target.is_file():
            try:
                r = subprocess.run(
                    [str(target), "--update-to", channel],
                    capture_output=True, text=True, timeout=_UPDATE_TIMEOUT,
                )
                if r.returncode == 0:
                    version = _probe_version([str(target), "--version"])
                    _versions["ytdlp"] = version
                    _write_stamp()
                    log(f"yt-dlp self-update OK ({channel}): now {version}")
                    return True
                log("yt-dlp self-update failed: "
                    + (r.stderr or r.stdout).strip().splitlines()[-1][:200])
            except (OSError, subprocess.SubprocessError) as exc:
                log(f"yt-dlp self-update error: {exc}")

        if not _is_managed(ytdlp_path):
            log("yt-dlp path is not app-managed – skipping forced re-download")
            return False

        try:
            version = ensure_ytdlp_fresh(ytdlp_path, channel, log)
            return version is not None
        except Exception as exc:
            log(f"yt-dlp re-download failed: {exc}")
            return False


def ensure_ytdlp_fresh(ytdlp_path: str, channel: str, log=_default_log) -> str | None:
    """Force-download the latest release over the existing binary."""
    target = Path(ytdlp_path)
    target = target if target.is_absolute() else (BASE_DIR / target).resolve()
    _download(_channel_url(channel), target, log)
    _make_executable(target)
    version = _probe_version([str(target), "--version"])
    _versions["ytdlp"] = version
    _write_stamp()
    log(f"yt-dlp downloaded: {version or '(version check failed)'}")
    return version


def ytdlp_version() -> str | None:
    return _versions["ytdlp"]


# ---------------------------------------------------------------------------
# Deno (JavaScript runtime for yt-dlp's YouTube extractor)
# ---------------------------------------------------------------------------

def _deno_target_triple() -> str | None:
    machine = platform.machine().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64",
            "aarch64": "aarch64", "arm64": "aarch64"}.get(machine)
    if not arch:
        return None
    if sys.platform.startswith("linux"):
        return f"{arch}-unknown-linux-gnu"
    if sys.platform == "darwin":
        return f"{arch}-apple-darwin"
    return None


def ensure_deno(log=_default_log) -> str | None:
    """
    Make sure a Deno runtime is available on PATH for yt-dlp. Prefers a
    system-wide install; otherwise downloads the static binary into ./bin/
    and prepends ./bin to PATH. Returns the version string, or None.
    """
    deno_name = "deno"
    local = BIN_DIR / deno_name

    system = shutil.which("deno")
    if system:
        version = _probe_version([system, "--version"])
        _versions["deno"] = version
        log(f"Deno found on PATH: {version} ({system})")
        _prepend_bin_to_path()
        return version

    if local.is_file():
        version = _probe_version([str(local), "--version"])
        if version:
            _versions["deno"] = version
            _prepend_bin_to_path()
            log(f"Deno {version} at {local}")
            return version
        log("Local Deno is present but not runnable – re-downloading")

    triple = _deno_target_triple()
    if not triple:
        log(f"No Deno build available for this platform ({sys.platform}/{platform.machine()}) – "
            "install Deno manually and put it on PATH")
        return None

    url = f"https://github.com/denoland/deno/releases/latest/download/deno-{triple}.zip"
    zip_path = BIN_DIR / f".deno-{triple}.zip"
    try:
        _download(url, zip_path, log)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extract(deno_name, path=str(BIN_DIR))
        _make_executable(local)
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass

    version = _probe_version([str(local), "--version"])
    _versions["deno"] = version
    _prepend_bin_to_path()
    log(f"Deno installed to {local}: {version or '(version check failed)'}")
    return version


def deno_version() -> str | None:
    return _versions["deno"]


def _prepend_bin_to_path():
    """Put ./bin first on PATH so yt-dlp subprocesses can find Deno."""
    bin_str = str(BIN_DIR)
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if bin_str not in parts:
        os.environ["PATH"] = bin_str + os.pathsep + os.environ.get("PATH", "")


# ---------------------------------------------------------------------------
# Overnight auto-update scheduler
# ---------------------------------------------------------------------------

def _read_stamp() -> float:
    try:
        return float(_STAMP_FILE.read_text().strip())
    except (OSError, ValueError):
        return 0.0


def _write_stamp():
    try:
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        _STAMP_FILE.write_text(str(time.time()))
    except OSError:
        pass


def start_auto_updater(ytdlp_path: str, interval_days: int, update_hour: int,
                       channel: str = "nightly", log=_default_log) -> threading.Thread:
    """
    Background thread: once the last successful update is older than
    interval_days, run the update during the update_hour local-time window.
    Checks every 20 minutes, so the hour window is never missed.
    """
    interval_secs = max(1, interval_days) * 86400

    def loop():
        while True:
            try:
                due = (time.time() - _read_stamp()) >= interval_secs
                if due and datetime.now().hour == update_hour:
                    log(f"Scheduled yt-dlp update starting (channel={channel})")
                    update_ytdlp(ytdlp_path, channel, log)
            except Exception as exc:
                log(f"Auto-updater error: {exc}")
            time.sleep(1200)

    t = threading.Thread(target=loop, daemon=True, name="ytdlp-auto-updater")
    t.start()
    return t


# ---------------------------------------------------------------------------
# Bootstrap – called on app startup and from setup.sh
# ---------------------------------------------------------------------------

def bootstrap(ytdlp_path: str = "./yt-dlp", start_updater: bool = True, log=_default_log):
    """
    Ensure all external tools are present and runnable, then (optionally)
    start the overnight auto-updater. Never raises: the web UI should come
    up even when the network is down – failures are logged and retried on
    the next restart or scheduled update.
    """
    channel = os.getenv("YTDLP_UPDATE_CHANNEL", "nightly").strip().lower()
    if channel not in _CHANNEL_REPOS:
        log(f"Unknown YTDLP_UPDATE_CHANNEL {channel!r} – using 'nightly'")
        channel = "nightly"

    auto_update = os.getenv("YTDLP_AUTO_UPDATE", "true").strip().lower() != "false"
    try:
        interval_days = max(1, int(os.getenv("YTDLP_UPDATE_INTERVAL_DAYS", "3")))
    except ValueError:
        interval_days = 3
    try:
        update_hour = min(23, max(0, int(os.getenv("YTDLP_UPDATE_HOUR", "3"))))
    except ValueError:
        update_hour = 3

    try:
        ensure_ytdlp(ytdlp_path, channel, log)
    except Exception as exc:
        log(f"Could not ensure yt-dlp: {exc}")

    try:
        ensure_deno(log)
    except Exception as exc:
        log(f"Could not ensure Deno: {exc}")

    if not start_updater:
        return

    if auto_update:
        # If we're overdue (e.g. first start after this feature, or the box
        # was off during the window), update right away in the background
        # instead of waiting for the next overnight window.
        if (time.time() - _read_stamp()) >= interval_days * 86400:
            threading.Thread(
                target=update_ytdlp, args=(ytdlp_path, channel, log),
                daemon=True, name="ytdlp-startup-update",
            ).start()
        start_auto_updater(ytdlp_path, interval_days, update_hour, channel, log)
        log(f"Auto-update enabled: every {interval_days} day(s) around "
            f"{update_hour:02d}:00 local time (channel={channel})")
    else:
        log("Auto-update disabled (YTDLP_AUTO_UPDATE=false)")


if __name__ == "__main__":
    # `python -m bark_extractor.tool_manager` – used by setup.sh to install
    # the tools without starting the web app.
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except Exception:
        pass
    bootstrap(os.getenv("YTDLP_PATH", "./yt-dlp"), start_updater=False)
