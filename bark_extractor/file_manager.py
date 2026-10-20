"""
File manager for Bark Extractor.
Handles listing, serving, and deleting downloaded MP3 files.
"""

import os
import hashlib
from datetime import datetime

from bark_extractor import audio_ops

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".opus", ".flac", ".ogg", ".wav", ".aiff"}


class FileManager:
    def __init__(self, downloads_dir: str):
        self.downloads_dir = downloads_dir
        os.makedirs(downloads_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_files(self) -> list[dict]:
        """
        Recursively list all MP3 files under downloads_dir.
        Returns a list of file-info dicts sorted by modification time (newest first).
        """
        results = []

        for root, dirs, files in os.walk(self.downloads_dir):
            # Skip hidden dirs
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for fname in files:
                if os.path.splitext(fname)[1].lower() not in AUDIO_EXTENSIONS:
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, self.downloads_dir)
                try:
                    stat = os.stat(fpath)
                    results.append({
                        "id": self.file_id(rel),
                        "name": fname,
                        "path": rel,
                        "normalized": audio_ops.is_normalized_name(fname),
                        "size": stat.st_size,
                        "size_human": self._human_size(stat.st_size),
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "modified_human": datetime.fromtimestamp(stat.st_mtime).strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                        "folder": os.path.relpath(root, self.downloads_dir)
                        if root != self.downloads_dir
                        else None,
                    })
                except OSError:
                    pass

        results.sort(key=lambda x: x["modified"], reverse=True)
        return results

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get_file_path(self, file_id: str) -> str | None:
        """Resolve a file_id to an absolute path, or None if not found."""
        for info in self.list_files():
            if info["id"] == file_id:
                return os.path.join(self.downloads_dir, info["path"])
        return None

    def get_file_info(self, file_id: str) -> dict | None:
        for info in self.list_files():
            if info["id"] == file_id:
                return info
        return None

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_file(self, file_id: str) -> bool:
        path = self.get_file_path(file_id)
        if path and os.path.isfile(path):
            os.remove(path)
            # Remove empty parent folders (playlist dirs)
            parent = os.path.dirname(path)
            if parent != self.downloads_dir:
                try:
                    if not os.listdir(parent):
                        os.rmdir(parent)
                except OSError:
                    pass
            return True
        return False

    # ------------------------------------------------------------------
    # Rename
    # ------------------------------------------------------------------

    def rename_file(self, file_id: str, new_name: str) -> dict:
        """
        Rename a file within its folder (the extension is kept). Returns the
        file's new info dict. Raises LookupError if file_id is unknown,
        ValueError for an invalid name, FileExistsError on a collision.
        """
        path = self.get_file_path(file_id)
        if not path:
            raise LookupError(file_id)
        folder, old_name = os.path.split(path)
        ext = os.path.splitext(old_name)[1]
        name = audio_ops.clean_filename(new_name, ext)
        # A normalized copy keeps its marker, so it still can't be
        # normalized again after a rename.
        if audio_ops.is_normalized_name(old_name) and not audio_ops.is_normalized_name(name):
            name = audio_ops.normalized_path_for(name)
        new_path = os.path.join(folder, name)
        if new_path == path:
            return self.get_file_info(file_id)
        # Case-only renames are fine on case-insensitive filesystems too
        if os.path.exists(new_path) and os.path.normcase(new_path) != os.path.normcase(path):
            raise FileExistsError(name)
        os.rename(path, new_path)
        return self.get_file_info(self.file_id(os.path.relpath(new_path, self.downloads_dir)))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def file_id(rel_path: str) -> str:
        """Stable ID derived from relative path."""
        return hashlib.sha1(rel_path.encode()).hexdigest()[:16]

    @staticmethod
    def _human_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"
