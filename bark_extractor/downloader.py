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

from bark_extractor import normalizer


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadJob:
    def __init__(self, download_id, url, session_id, quality="0",
                 is_playlist=False, organize_playlist=False, audio_format="mp3",
                 kind="download", source_path=None, title=None):
        self.download_id = download_id
        self.kind = kind                # "download" or "normalize"
        self.url = url
        self.source_path = source_path  # normalize jobs: absolute input path
        self.title = title or url
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
        self.stage = ""
        self.result_file = None         # normalize jobs: output path relative to downloads_dir
        self.log_lines = []
        self.error = ""
        self.started_at = None
        self.finished_at = None
        self.files_downloaded = []

        self._process = None
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._done = threading.Event()
        self._destinations = []         # yt-dlp download targets (for cancel cleanup)
        self._outputs = []              # final audio files reported by yt-dlp

    def to_dict(self):
        return {
            "download_id": self.download_id,
            "kind": self.kind,
            "title": self.title,
            "url": self.url,
            "session_id": self.session_id,
            "status": self.status,
            "progress": self.progress,
            "speed": self.speed,
            "eta": self.eta,
            "current_file": self.current_file,
            "stage": self.stage,
            "result_file": self.result_file,
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

    @property
    def is_finished(self):
        return self.status not in (JobStatus.PENDING, JobStatus.RUNNING)

    def wait(self, timeout=None) -> bool:
        """Block until the job has finished; returns False on timeout."""
        return self._done.wait(timeout)


class DownloadManager:
    """
    Manages all download jobs across all sessions.
    Downloads run in background threads; progress can be streamed via SSE.
    """

    def __init__(self, downloads_dir, ffmpeg_path="ffmpeg", ytdlp_path="yt-dlp",
                 max_concurrent_normalize=2):
        self.downloads_dir = downloads_dir
        # Resolve bare command names (e.g. "ffmpeg") to full paths so yt-dlp
        # can locate the binary via --ffmpeg-location instead of treating the
        # name as a relative filesystem path.
        resolved = shutil.which(ffmpeg_path)
        self.ffmpeg_path = resolved if resolved else ffmpeg_path
        self.ytdlp_path = ytdlp_path

        self._jobs: dict[str, DownloadJob] = {}
        self._lock = threading.Lock()
        # Active normalize jobs keyed by source path, so repeat requests for
        # the same file join the running job instead of racing it.
        self._normalizing: dict[str, DownloadJob] = {}
        self._normalize_slots = threading.Semaphore(max(1, max_concurrent_normalize))

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

    def create_normalize_job(self, source_path, session_id) -> tuple[DownloadJob, bool]:
        """
        Start normalizing source_path, or return the job already doing so.
        Returns (job, created). Raises ValueError for a file that is itself
        a normalized output.
        """
        if normalizer.is_normalized_name(source_path):
            raise ValueError("File is already a normalized copy")
        with self._lock:
            existing = self._normalizing.get(source_path)
            if existing and not existing.is_finished:
                return existing, False
            job = DownloadJob(
                download_id=str(uuid.uuid4()),
                url="",
                session_id=session_id,
                audio_format=os.path.splitext(source_path)[1].lstrip(".").lower(),
                kind="normalize",
                source_path=source_path,
                title=os.path.basename(source_path),
            )
            self._jobs[job.download_id] = job
            self._normalizing[source_path] = job

        threading.Thread(target=self._run_job, args=(job,), daemon=True).start()
        return job, True

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
        try:
            if job.kind == "normalize":
                self._run_normalize(job)
            else:
                self._run_download(job)
        finally:
            job.finished_at = datetime.now()
            job.progress = 100.0 if job.status == JobStatus.COMPLETED else job.progress
            # Release the subprocess handle (and its pipes) so finished
            # jobs don't pin OS resources while they linger for status reads.
            with job._lock:
                if job._process is not None:
                    for pipe in (job._process.stdout, job._process.stderr):
                        try:
                            if pipe:
                                pipe.close()
                        except OSError:
                            pass
                    job._process = None
            if job.kind == "normalize":
                with self._lock:
                    if self._normalizing.get(job.source_path) is job:
                        del self._normalizing[job.source_path]
            job._done.set()

    def _run_download(self, job: DownloadJob):
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

    # ------------------------------------------------------------------
    # Normalization – runs in a background thread
    # ------------------------------------------------------------------

    def _run_normalize(self, job: DownloadJob):
        src = job.source_path
        dst = normalizer.normalized_path_for(src)
        tmp = dst + ".part"

        job.stage = "Queued"
        job.add_log(f"Normalize: {src}")
        # Wait for a free slot so a batch request doesn't spawn dozens of
        # ffmpeg processes at once; stay cancellable while queued.
        while not self._normalize_slots.acquire(timeout=0.5):
            if job.is_cancelled:
                job.status = JobStatus.CANCELLED
                job.stage = ""
                job.add_log("Normalization cancelled by user.")
                return

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now()
        try:
            if job.is_cancelled:
                raise _Cancelled()

            job.stage = "Analyzing peak level"
            job.add_log("Pass 1/2: measuring peak level")
            rc, err = self._run_ffmpeg(
                job, normalizer.analyze_command(self.ffmpeg_path, src), 0, 50)
            if job.is_cancelled:
                raise _Cancelled()
            if rc != 0:
                raise RuntimeError(f"ffmpeg analysis failed (exit code {rc})", err)
            gain = normalizer.parse_gain(err)
            if gain is None:
                raise RuntimeError("Could not measure the peak level", err)
            bitrate = normalizer.parse_bitrate(err)
            job.add_log(f"Applying {gain:+.2f} dB of gain "
                        f"(target peak {normalizer.TARGET_PEAK_DB:g} dB)")

            job.stage = "Applying gain"
            rc, err = self._run_ffmpeg(
                job,
                normalizer.apply_command(self.ffmpeg_path, src, tmp, gain, bitrate),
                50, 100, normalizer.parse_duration(err))
            if job.is_cancelled:
                raise _Cancelled()
            if rc != 0:
                raise RuntimeError(f"ffmpeg failed (exit code {rc})", err)

            # Publish atomically: the library never lists a half-written
            # file, and a failed run never leaves one behind as "cached".
            os.replace(tmp, dst)
            job.result_file = os.path.relpath(dst, self.downloads_dir)
            job.files_downloaded = [job.result_file]
            job.current_file = os.path.basename(dst)
            job.status = JobStatus.COMPLETED
            job.add_log(f"Saved {job.result_file}")
        except _Cancelled:
            job.status = JobStatus.CANCELLED
            job.add_log("Normalization cancelled by user.")
        except FileNotFoundError as e:
            job.status = JobStatus.FAILED
            job.error = f"Tool not found: {e}. Is ffmpeg installed?"
            job.add_log(f"ERROR: {job.error}")
        except RuntimeError as e:
            job.status = JobStatus.FAILED
            job.error = str(e.args[0])
            job.add_log(f"ERROR: {job.error}")
            if len(e.args) > 1:
                for line in str(e.args[1]).strip().splitlines()[-15:]:
                    job.add_log(line)
        except Exception as e:
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.add_log(f"ERROR: {e}")
        finally:
            job.stage = ""
            self._normalize_slots.release()
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _run_ffmpeg(self, job: DownloadJob, cmd, pct_from, pct_to, duration=None):
        """
        Run one ffmpeg pass, mapping its -progress output onto
        pct_from..pct_to of the job's progress. Returns (returncode, stderr).
        """
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1,
            start_new_session=True,  # new process group for clean kill
        )
        with job._lock:
            job._process = process
        if job.is_cancelled:
            job.cancel()

        # stderr carries the input header (duration, bitrate) and the
        # volumedetect result; drain it in parallel so neither pipe blocks.
        err_lines: list[str] = []

        def drain_stderr():
            for line in process.stderr:
                err_lines.append(line)

        reader = threading.Thread(target=drain_stderr, daemon=True)
        reader.start()

        for line in process.stdout:
            key, _, value = line.strip().partition("=")
            if key != "out_time_us" or not value.isdigit():
                continue
            if duration is None:
                duration = normalizer.parse_duration("".join(err_lines))
            if duration:
                frac = min(1.0, int(value) / 1_000_000 / duration)
                job.progress = round(pct_from + (pct_to - pct_from) * frac, 1)

        process.wait()
        reader.join(timeout=5)
        for pipe in (process.stdout, process.stderr):
            try:
                pipe.close()
            except OSError:
                pass
        with job._lock:
            if job._process is process:
                job._process = None
        if not job.is_cancelled:
            job.progress = float(pct_to) if process.returncode == 0 else job.progress
        return process.returncode, "".join(err_lines)

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
    # [ExtractAudio] Destination: /path/to/file.mp3
    _AUDIO_DEST_RE = re.compile(r"\[ExtractAudio\]\s+Destination:\s+(.+)")
    # [ExtractAudio] Not converting audio /path/to/file.m4a; file is already in target format m4a
    _AUDIO_KEPT_RE = re.compile(r"\[ExtractAudio\]\s+Not converting audio\s+(.+?);")

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
            dest = m.group(1).strip()
            job._destinations.append(dest)
            job.current_file = os.path.basename(dest)
            return

        m = self._AUDIO_DEST_RE.search(line) or self._AUDIO_KEPT_RE.search(line)
        if m:
            out = m.group(1).strip()
            job._outputs.append(out)
            job.current_file = os.path.basename(out)
            return

    # ------------------------------------------------------------------
    # Post-processing helpers
    # ------------------------------------------------------------------

    def _collect_downloaded_files(self, job: DownloadJob):
        """Record the audio files this job produced (paths relative to downloads_dir)."""
        # Prefer the files yt-dlp reported writing – a directory scan would
        # also pick up other users' concurrent downloads and normalized copies.
        for out in job._outputs:
            if os.path.isfile(out):
                rel = os.path.relpath(out, self.downloads_dir)
                if rel not in job.files_downloaded:
                    job.files_downloaded.append(rel)
        if job.files_downloaded or not job.started_at:
            return

        # Fallback: files of the target format modified since the job started.
        cutoff = job.started_at.timestamp() - 5  # 5 s buffer
        for root, _, files in os.walk(self.downloads_dir):
            for fname in files:
                if not fname.lower().endswith(f".{job.audio_format}"):
                    continue
                if normalizer.is_normalized_name(fname):
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
        """Remove the .part / .ytdl temp files this cancelled job left behind.
        Only this job's own destinations are touched – other downloads may be
        running concurrently in the same directory."""
        for dest in job._destinations:
            folder, prefix = os.path.split(dest)
            try:
                names = os.listdir(folder or ".")
            except OSError:
                continue
            for fname in names:
                if not fname.startswith(prefix):
                    continue
                rest = fname[len(prefix):]
                if rest.startswith(".part") or rest == ".ytdl":
                    try:
                        os.remove(os.path.join(folder, fname))
                    except OSError:
                        pass


class _Cancelled(Exception):
    """Internal: unwinds a normalize job after the user cancelled it."""
