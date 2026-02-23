"""
File manager for Bark Extractor.
Handles listing, serving, and deleting downloaded MP3 files.
"""

import os
import uuid
import hashlib
from datetime import datetime


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
                if not fname.lower().endswith(".mp3"):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, self.downloads_dir)
                try:
                    stat = os.stat(fpath)
                    results.append({
                        "id": self._file_id(rel),
                        "name": fname,
                        "path": rel,
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
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _file_id(rel_path: str) -> str:
        """Stable ID derived from relative path."""
        return hashlib.sha1(rel_path.encode()).hexdigest()[:16]

    @staticmethod
    def _human_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"
