# Bark Extractor 🐾

**Version 1.1.0**

A yt-dlp powered MP3 downloader with a dark web UI. Download audio from YouTube and other supported sites, manage your library, and share access across multiple users — all from a browser on port **5100**.

## Features

- Paste any YouTube (or yt-dlp-supported) URL and extract MP3 in one click
- Concurrent downloads — multiple users can queue jobs simultaneously
- Live progress streaming via SSE (speed, ETA, current file)
- Stop button cancels a running or pending download and cleans up partial files
- Persistent MP3 library shared across all users — newest files listed first
- Checkbox selection for bulk download or bulk delete
- Runs as a systemd service — starts on boot, restarts on failure
- **Self-maintaining yt-dlp** — downloaded automatically when missing and
  self-updated overnight every few days, so YouTube extraction doesn't rot
- **Bundled JavaScript runtime** — Deno is installed automatically into the
  project's `bin/` folder (yt-dlp needs it to solve YouTube's player
  challenges; without it YouTube downloads fail with HTTP 403)
- Dark, dog-themed UI

## Prerequisites

| Tool | Install |
|------|---------|
| Python 3.9+ | [python.org](https://www.python.org/downloads/) |
| FFmpeg | `sudo apt install ffmpeg` / `brew install ffmpeg` |
| curl | usually pre-installed |

FFmpeg must be on your `PATH` (or set `FFMPEG_PATH` in `.env`).

`yt-dlp` and `Deno` are **not** prerequisites — the app manages both itself
(see [External tools & auto-update](#external-tools--auto-update)). If a
system-wide `deno` is already on `PATH` it is used instead of the local copy.

## Quick Start

```bash
git clone https://github.com/ShowSysDan/Bark-Extractor.git
cd Bark-Extractor
bash setup.sh
```

`setup.sh` will:
1. Install FFmpeg (Debian/Ubuntu/macOS/Fedora/Arch)
2. Create a Python virtual environment in `.venv/`
3. Install Python dependencies
4. Download `yt-dlp` and the Deno JavaScript runtime (into the project folder)
5. Create `downloads/` and `sessions/` directories
6. Copy `.env.example` → `.env` (only if `.env` doesn't already exist —
   existing settings are never overwritten)
7. Install, enable, and start a **systemd service** (`bark-extractor`)

After setup completes, open **http://localhost:5100** in your browser.

## Updating the App

```bash
cd Bark-Extractor
git pull
sudo systemctl restart bark-extractor
```

That's it. Every startup re-checks all dependencies: directories are created,
FFmpeg presence is verified, and `yt-dlp`/`Deno` are downloaded if missing.
Your `.env` (including syslog settings saved from the web UI) is gitignored
and never touched by an update.

> **One-time note for upgrading to v1.1.0:** `yt-dlp` used to be tracked in
> git and is now managed by the app instead. If you previously ran
> `./yt-dlp -U` on your server, the tracked file was modified and `git pull`
> will refuse the merge. Fix with:
> ```bash
> git checkout -- yt-dlp yt-dlp.exe 2>/dev/null; git pull
> sudo systemctl restart bark-extractor   # re-downloads the latest yt-dlp
> ```

## External Tools & Auto-Update

YouTube changes constantly, and two things broke the old setup: the `yt-dlp`
binary pinned in git went stale, and modern yt-dlp needs a JavaScript runtime
to run YouTube's player code (without one, only unsigned legacy formats are
offered and YouTube rejects them with HTTP 403).

The app now manages both tools itself:

- **On every startup** — if `yt-dlp` (at `YTDLP_PATH`) or Deno is missing,
  the latest release is downloaded automatically. Deno lives in the project's
  `bin/` folder (nothing is installed system-wide) and is put on the app's
  `PATH` so yt-dlp finds it.
- **Overnight auto-update** — a background scheduler self-updates `yt-dlp`
  every `YTDLP_UPDATE_INTERVAL_DAYS` days (default 3) during the
  `YTDLP_UPDATE_HOUR` local-time window (default 3 a.m.). If the box was off
  during the window, the update runs shortly after the next startup instead.
- **Release channel** — defaults to `nightly`, which is where YouTube fixes
  land first (and what the yt-dlp project recommends). Switch to `stable`
  with `YTDLP_UPDATE_CHANNEL=stable` in `.env`.
- Updates are safe while downloads are running (the binary is replaced
  atomically; in-flight downloads keep their already-running copy), and a
  failed self-update falls back to a fresh download of the latest release.

Check what's live at any time: `curl http://localhost:5100/api/version`
returns the app, yt-dlp, and Deno versions, and the web UI footer shows the
app version.

## Service Management

```bash
# Check status
sudo systemctl status bark-extractor

# View live logs
sudo journalctl -u bark-extractor -f

# Restart after config change
sudo systemctl restart bark-extractor

# Stop the service
sudo systemctl stop bark-extractor

# Disable auto-start on boot
sudo systemctl disable bark-extractor
```

### Manual Start (without systemd)

```bash
source .venv/bin/activate
./BarkExtractor
```

## Configuration

All settings live in `.env` (created from `.env.example` by `setup.sh`):

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `5100` | HTTP port to listen on |
| `DOWNLOADS_DIR` | `./downloads` | Where MP3s are saved |
| `SESSIONS_DIR` | `./sessions` | Flask session storage |
| `FFMPEG_PATH` | `ffmpeg` | Path to ffmpeg binary |
| `YTDLP_PATH` | `./yt-dlp` | Path to yt-dlp binary (auto-downloaded when missing) |
| `YTDLP_AUTO_UPDATE` | `true` | Self-update yt-dlp overnight |
| `YTDLP_UPDATE_INTERVAL_DAYS` | `3` | Days between yt-dlp updates |
| `YTDLP_UPDATE_HOUR` | `3` | Local hour (0–23) of the update window |
| `YTDLP_UPDATE_CHANNEL` | `nightly` | yt-dlp channel: `nightly`, `stable`, `master` |
| `SECRET_KEY` | random | Flask session secret |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode |

New variables are optional — an existing `.env` without them keeps working
with the defaults above. On startup the app appends any missing auto-update
keys to `.env` with their default values so they're easy to find and edit on
the server; existing keys, values, and comments are never modified. Syslog
settings saved from the web UI are stored in `.env` and are preserved across
updates.

## Project Structure

```
Bark-Extractor/
├── BarkExtractor            # Main executable (Flask app, routes, SSE logic)
├── bark_extractor/
│   ├── downloader.py        # DownloadManager, job lifecycle, yt-dlp subprocess
│   ├── file_manager.py      # MP3 listing, serving, deletion
│   ├── syslog_sender.py     # Optional UDP syslog integration
│   └── tool_manager.py      # yt-dlp/Deno download, startup checks, auto-update
├── templates/
│   └── index.html           # Single-page UI
├── static/
│   ├── css/style.css        # Dark theme
│   └── js/app.js            # Frontend logic (SSE, file table, forms)
├── bark-extractor.service   # systemd unit file (template)
├── bin/                     # Deno runtime (auto-downloaded, gitignored)
├── yt-dlp                   # yt-dlp binary (auto-downloaded, gitignored)
├── setup.sh                 # One-shot install script
├── requirements.txt         # Python dependencies
└── .env.example             # Environment variable template
```

## Usage

1. Paste a YouTube URL into the **YouTube URL** field
2. Choose audio quality (0 = best, 9 = lowest)
3. Optionally enable **Download entire playlist** and folder organisation
4. Click **Extract MP3** — the download card appears with live progress
5. Click **Stop** at any time to cancel and clean up partial files
6. Completed files appear in the **Downloaded MP3s** table
7. Click the download icon to save a file to your computer, or select multiple files and use **Download Selected**

## Changelog

### 1.1.0 (2026-09-08)
- **Fixed YouTube downloads failing with HTTP 403** — two causes: the yt-dlp
  binary tracked in git had gone stale, and no JavaScript runtime was
  available for yt-dlp to solve YouTube's player challenges
- yt-dlp and Deno are no longer tracked in git; both are downloaded
  automatically on startup when missing (`setup.sh` also primes them)
- Removed the bundled Windows binary (`yt-dlp.exe`) and Windows-specific
  code — the app targets Linux (and macOS for development)
- yt-dlp now self-updates overnight (default: every 3 days around 3 a.m.,
  nightly channel) — configurable via `YTDLP_AUTO_UPDATE`,
  `YTDLP_UPDATE_INTERVAL_DAYS`, `YTDLP_UPDATE_HOUR`, `YTDLP_UPDATE_CHANNEL`
- Startup now verifies all dependencies (directories, FFmpeg, yt-dlp, Deno),
  so `git pull` + service restart is a complete update
- Memory-leak fixes: finished download jobs are now purged from memory an
  hour after completion (previously they accumulated forever), finished jobs
  release their subprocess handles immediately, and abandoned SSE stream
  registrations are cleaned up
- Added `/api/version` endpoint and version display in the UI footer
- Missing auto-update settings are appended to `.env` on startup (defaults
  only; existing entries are never touched)

### 1.0.0
- Initial release: MP3 extraction with live progress, shared library,
  playlist support, audio normalization, syslog logging, systemd service
