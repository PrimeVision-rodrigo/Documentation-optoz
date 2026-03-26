import os
from pathlib import Path

from watchdog.events import FileSystemEventHandler

from watcher.change_tracker import ChangeTracker
from watcher.config import Config
from watcher.utils.hash_utils import file_sha256


class ProjectFileHandler(FileSystemEventHandler):
    """Watchdog handler that filters and deduplicates file change events."""

    def __init__(self, config: Config, tracker: ChangeTracker):
        self._config = config
        self._tracker = tracker
        self._hash_cache: dict[str, str | None] = {}

    def on_modified(self, event):
        if event.is_directory:
            return
        self._handle(event.src_path, "modified")

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle(event.src_path, "created")

    def on_deleted(self, event):
        if event.is_directory:
            return
        rel = self._relative_path(event.src_path)
        if rel and not self._should_exclude(rel):
            self._hash_cache.pop(rel, None)
            self._tracker.record(rel, "deleted")

    def _handle(self, abs_path: str, change_type: str):
        rel = self._relative_path(abs_path)
        if not rel or self._should_exclude(rel):
            return

        path = Path(abs_path)
        if not path.is_file():
            return

        new_hash = file_sha256(path)
        old_hash = self._hash_cache.get(rel)

        if new_hash and new_hash != old_hash:
            self._hash_cache[rel] = new_hash
            self._tracker.record(rel, change_type)

    def _relative_path(self, abs_path: str) -> str | None:
        try:
            return os.path.relpath(abs_path, self._config.project_path)
        except ValueError:
            return None

    def _should_exclude(self, rel_path: str) -> bool:
        parts = Path(rel_path).parts
        for exclude in self._config.watch_excludes:
            if exclude in parts or rel_path.endswith(exclude):
                return True
        return False

    def seed_hashes(self):
        """Build initial hash cache for all watched files."""
        for root, dirs, files in os.walk(self._config.project_path):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in self._config.watch_excludes]
            for fname in files:
                abs_path = os.path.join(root, fname)
                rel = self._relative_path(abs_path)
                if rel and not self._should_exclude(rel):
                    self._hash_cache[rel] = file_sha256(Path(abs_path))
