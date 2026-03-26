import threading
from collections import defaultdict
from datetime import datetime

from watcher.utils.file_classifier import FileClassifier


class Change:
    __slots__ = ("rel_path", "change_type", "timestamp", "domain")

    def __init__(self, rel_path: str, change_type: str, domain: str):
        self.rel_path = rel_path
        self.change_type = change_type
        self.timestamp = datetime.now()
        self.domain = domain


class ChangeTracker:
    """Thread-safe accumulator for file changes between flush intervals."""

    def __init__(self, classifier: FileClassifier):
        self._lock = threading.Lock()
        self._changes: list[Change] = []
        self._classifier = classifier

    def record(self, rel_path: str, change_type: str):
        domain = self._classifier.classify(rel_path)
        with self._lock:
            self._changes.append(Change(rel_path, change_type, domain))

    def flush(self) -> list[Change]:
        """Return all accumulated changes and clear the buffer."""
        with self._lock:
            changes = self._changes
            self._changes = []
        return changes

    def has_changes(self) -> bool:
        with self._lock:
            return len(self._changes) > 0

    @staticmethod
    def group_by_domain(changes: list[Change]) -> dict[str, list[Change]]:
        groups: dict[str, list[Change]] = defaultdict(list)
        for c in changes:
            groups[c.domain].append(c)
        return dict(groups)

    @staticmethod
    def changed_files(changes: list[Change]) -> set[str]:
        return {c.rel_path for c in changes}
