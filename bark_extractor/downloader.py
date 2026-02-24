"""
Core download logic for Bark Extractor.
Manages concurrent yt-dlp download jobs with cancellation and progress streaming.
"""

import os
import re
import uuid
import shutil
import signal
import threading
import subprocess
from enum import Enum
from datetime import datetime


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadJob:
    def __init__(self, download_id, url, session_id, quality="0",
                 is_playlist=False, organize_playlist=False, audio_format="mp3"):
        self.download_id = download_id
        self.url = url
        self.session_id = session_id
        self.quality = quality
        self.is_playlist = is_playlist
        self.organize_playlist = organize_playlist
        self.audio_format = audio_format

        self.status = JobStatus.PENDING
        self.progress = 0.0
        self.speed = ""
        self.eta = ""
        self.current_file = ""
        self.log_lines = []
        self.error = ""
        self.started_at = None
        self.finished_at = None
        self.files_downloaded = []

        self._process = None
        self._lock = threading.Lock()
        self._cancelled = threading.Event()

    def to_dict(self):
        return {
            "download_id": self.download_id,
            "url": self.url,
            "session_id": self.session_id,
            "status": self.status,
            "progress": self.progress,
            "speed": self.speed,
            "eta": self.eta,
            "current_file": self.current_file,
            "error": self.error,
            "audio_format": self.audio_format,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "files_downloaded": self.files_downloaded,
        }

    def add_log(self, line):
        with self._lock:
            self.log_lines.append(line)
            # Keep log from growing unbounded
            if len(self.log_lines) > 500:
                self.log_lines = self.log_lines[-500:]

    def cancel(self):
        """Signal cancellation and kill any running subprocess."""
        self._cancelled.set()
        with self._lock:
            if self._process and self._process.poll() is None:
                try:
                    # Kill the whole process group so child ffmpeg is also killed
                    os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, AttributeError):
                    try:
                        self._process.terminate()
                    except Exception:
                        pass

    @property
    def is_cancelled(self):
        return self._cancelled.is_set()


class DownloadManager:
    """
    Manages all download jobs across all sessions.
    Downloads run in background threads; progress can be streamed via SSE.
    """

    def __init__(self, downloads_dir, ffmpeg_path="ffmpeg", ytdlp_path="yt-dlp"):
        self.downloads_dir = downloads_dir
        # Resolve bare command names (e.g. "ffmpeg") to full paths so yt-dlp
        # can locate the binary via --ffmpeg-location instead of treating the
        # name as a relative filesystem path.
        resolved = shutil.which(ffmpeg_path)
        self.ffmpeg_path = resolved if resolved else ffmpeg_path
        self.ytdlp_path = ytdlp_path

        self._jobs: dict[str, DownloadJob] = {}
        self._lock = threading.Lock()

        os.makedirs(downloads_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_job(self, url, session_id, quality="0",
                   is_playlist=False, organize_playlist=False,
                   audio_format="mp3") -> DownloadJob:
        download_id = str(uuid.uuid4())
        job = DownloadJob(
            download_id=download_id,
            url=url,
            session_id=session_id,
            quality=quality,
            is_playlist=is_playlist,
            organize_playlist=organize_playlist,
            audio_format=audio_format,
        )
        with self._lock:
            self._jobs[download_id] = job

        thread = threading.Thread(
            target=self._run_job, args=(job,), daemon=True
        )
        thread.start()
        return job

    def get_job(self, download_id) -> DownloadJob | None:
        return self._jobs.get(download_id)

    def cancel_job(self, download_id) -> bool:
        job = self._jobs.get(download_id)
        if job and job.status in (JobStatus.PENDING, JobStatus.RUNNING):
            job.cancel()
            return True
        return False

    def get_active_jobs(self):
        """Return all jobs that are pending or running."""
        with self._lock:
            return [
                j.to_dict() for j in self._jobs.values()
                if j.status in (JobStatus.PENDING, JobStatus.RUNNING)
            ]

    def get_all_jobs(self):
        with self._lock:
            return [j.to_dict() for j in self._jobs.values()]

    def get_session_jobs(self, session_id):
        with self._lock:
            return [
                j.to_dict() for j in self._jobs.values()
                if j.session_id == session_id
            ]

    def cleanup_finished_jobs(self, max_age_seconds=3600):
        """Remove completed/failed/cancelled jobs older than max_age_seconds."""
        cutoff = datetime.now().timestamp() - max_age_seconds
        with self._lock:
            to_remove = [
                jid for jid, j in self._jobs.items()
                if j.status not in (JobStatus.PENDING, JobStatus.RUNNING)
                and j.finished_at
                and j.finished_at.timestamp() < cutoff
            ]
            for jid in to_remove:
                del self._jobs[jid]

    # ------------------------------------------------------------------
    # Internal – runs in a background thread
    # ------------------------------------------------------------------

    def _run_job(self, job: DownloadJob):
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now()

        try:
            cmd = self._build_command(job)
            job.add_log(f"Starting download: {job.url}")
            job.add_log(f"Command: {' '.join(cmd)}")
            job.add_log("-" * 60)

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
                start_new_session=True,  # create new process group for clean kill
            )

            with job._lock:
                job._process = process

            # If cancelled while we were starting the process, kill it now
            if job.is_cancelled:
                job.cancel()

            for line in process.stdout:
                line = line.rstrip()
                if not line:
                    continue

                if job.is_cancelled:
                    break

                job.add_log(line)
                self._parse_progress(job, line)

            process.wait()

            if job.is_cancelled:
                job.status = JobStatus.CANCELLED
                job.add_log("Download cancelled by user.")
                self._cleanup_partial_files(job)
            elif process.returncode == 0:
                job.status = JobStatus.COMPLETED
                job.add_log("-" * 60)
                job.add_log("Download completed successfully!")
                self._collect_downloaded_files(job)
            else:
                job.status = JobStatus.FAILED
                job.error = f"yt-dlp exited with code {process.returncode}"
                job.add_log("-" * 60)
                job.add_log(f"Download failed (exit code {process.returncode})")

        except FileNotFoundError as e:
            job.status = JobStatus.FAILED
            job.error = f"Tool not found: {e}. Is yt-dlp installed?"
            job.add_log(f"ERROR: {job.error}")
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.add_log(f"ERROR: {e}")
        finally:
            job.finished_at = datetime.now()
            job.progress = 100.0 if job.status == JobStatus.COMPLETED else job.progress

    def _build_command(self, job: DownloadJob) -> list[str]:
        quality = job.quality.split()[0] if job.quality else "0"

        if job.organize_playlist and job.is_playlist:
            output_template = os.path.join(
                self.downloads_dir,
                "%(playlist)s",
                "%(playlist_index)02d - %(title)s.%(ext)s",
            )
        else:
            output_template = os.path.join(
                self.downloads_dir, "%(title)s.%(ext)s"
            )

        cmd = [
            self.ytdlp_path,
            "-x",
            "--audio-format", job.audio_format,
            "--audio-quality", quality,
            "--no-check-certificate",
            "--ffmpeg-location", self.ffmpeg_path,
            "--newline",                    # force one line per progress update
            "--progress",
        ]

        if not job.is_playlist:
            cmd += ["--no-playlist"]

        cmd += ["-o", output_template, job.url]
        return cmd

    # ------------------------------------------------------------------
    # Progress parsing
    # ------------------------------------------------------------------

    # [download]  42.3% of ~  5.00MiB at  1.23MiB/s ETA 00:03
    _PROGRESS_RE = re.compile(
        r"\[download\]\s+([\d.]+)%\s+of[^a-zA-Z]*"
        r"(?:at\s+([\d.]+\s*\S+/s))?"
        r"(?:\s+ETA\s+(\S+))?"
    )
    # [download] Destination: /path/to/file.mp3
    _DEST_RE = re.compile(r"\[download\]\s+Destination:\s+(.+)")
    # [ffmpeg] Destination: /path/to/file.mp3
    _FFMPEG_DEST_RE = re.compile(r"\[ffmpeg\]\s+Destination:\s+(.+)")

    def _parse_progress(self, job: DownloadJob, line: str):
        m = self._PROGRESS_RE.search(line)
        if m:
            job.progress = float(m.group(1))
            if m.group(2):
                job.speed = m.group(2)
            if m.group(3):
                job.eta = m.group(3)
            return

        m = self._DEST_RE.search(line)
        if m:
            job.current_file = os.path.basename(m.group(1).strip())
            return

        m = self._FFMPEG_DEST_RE.search(line)
        if m:
            job.current_file = os.path.basename(m.group(1).strip())
            return

    # ------------------------------------------------------------------
    # Post-processing helpers
    # ------------------------------------------------------------------

    def _collect_downloaded_files(self, job: DownloadJob):
        """Walk downloads_dir and find MP3s created/modified after job started."""
        if not job.started_at:
            return
        cutoff = job.started_at.timestamp() - 5  # 5 s buffer

        for root, _, files in os.walk(self.downloads_dir):
            for fname in files:
                if not fname.lower().endswith(f".{job.audio_format}"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    if os.path.getmtime(fpath) >= cutoff:
                        rel = os.path.relpath(fpath, self.downloads_dir)
                        if rel not in job.files_downloaded:
                            job.files_downloaded.append(rel)
                except OSError:
                    pass

    def _cleanup_partial_files(self, job: DownloadJob):
        """Remove .part and .ytdl temp files left by cancelled downloads."""
        for root, _, files in os.walk(self.downloads_dir):
            for fname in files:
                if fname.endswith(".part") or fname.endswith(".ytdl"):
                    try:
                        os.remove(os.path.join(root, fname))
                    except OSError:
                        pass
