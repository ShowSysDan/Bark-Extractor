# Bark Extractor 🐾

A yt-dlp powered MP3 downloader with a dark web UI. Download audio from YouTube and other supported sites, manage your library, and share access across multiple users — all from a browser on port **5100**.

## Features

- Paste any YouTube (or yt-dlp-supported) URL and extract MP3 in one click
- Concurrent downloads — multiple users can queue jobs simultaneously
- Live progress streaming via SSE (speed, ETA, current file)
- Stop button cancels a running or pending download and cleans up partial files
- Persistent MP3 library shared across all users — newest files listed first
- Checkbox selection for bulk download or bulk delete
- Dark, dog-themed UI

## Prerequisites

| Tool | Install |
|------|---------|
| Python 3.9+ | [python.org](https://www.python.org/downloads/) |
| FFmpeg | `sudo apt install ffmpeg` / `brew install ffmpeg` |
| curl | usually pre-installed |

FFmpeg must be on your `PATH` (or set `FFMPEG_PATH` in `.env`).

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
4. Ensure the bundled `yt-dlp` binary is executable (downloads it if missing)
5. Create `downloads/` and `sessions/` directories
6. Copy `.env.example` → `.env`

Then start the server:

```bash
source .venv/bin/activate
python app.py
```

Open **http://localhost:5100** in your browser.

## Configuration

All settings live in `.env` (created from `.env.example` by `setup.sh`):

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `5100` | HTTP port to listen on |
| `DOWNLOADS_DIR` | `./downloads` | Where MP3s are saved |
| `SESSIONS_DIR` | `./sessions` | Flask session storage |
| `FFMPEG_PATH` | `ffmpeg` | Path to ffmpeg binary |
| `YTDLP_PATH` | `./yt-dlp` | Path to yt-dlp binary |
| `SECRET_KEY` | random | Flask session secret |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode |

## Project Structure

```
Bark-Extractor/
├── app.py                   # Flask app, routes, SSE logic
├── bark_extractor/
│   ├── downloader.py        # DownloadManager, job lifecycle, yt-dlp subprocess
│   └── file_manager.py      # MP3 listing, serving, deletion
├── templates/
│   └── index.html           # Single-page UI
├── static/
│   ├── css/style.css        # Dark theme
│   └── js/app.js            # Frontend logic (SSE, file table, forms)
├── yt-dlp                   # Bundled Linux yt-dlp binary
├── yt-dlp.exe               # Bundled Windows yt-dlp binary
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
