from abc import ABC, abstractmethod
from pathlib import Path

from watcher.change_tracker import Change
from watcher.config import Config


class BaseGenerator(ABC):
    """Abstract base for all document generators."""

    def __init__(self, config: Config):
        self.config = config
        self.project = config.project_path
        self.output_dir = config.output_path

    @property
    @abstractmethod
    def filename(self) -> str:
        """Output filename e.g. '01_DEVELOPMENT_LOG.md'."""

    @property
    @abstractmethod
    def trigger_patterns(self) -> list[str]:
        """File path patterns that trigger this generator."""

    @abstractmethod
    def initial_scan(self) -> str:
        """Generate full document from current project state."""

    @abstractmethod
    def update(self, changes: list[Change]) -> str | None:
        """Regenerate or return None if no relevant changes."""

    def should_update(self, changed_files: set[str]) -> bool:
        """Check if any changed file matches this generator's triggers."""
        for f in changed_files:
            for pattern in self.trigger_patterns:
                if f.startswith(pattern) or f == pattern:
                    return True
        # Dev log always updates on any change
        return False

    def write(self, content: str):
        """Write content to output file."""
        out = self.output_dir / self.filename
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")

    def _read_file(self, rel_path: str) -> str:
        """Read a file from the project."""
        path = self.project / rel_path
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _file_exists(self, rel_path: str) -> bool:
        return (self.project / rel_path).is_file()

    def _list_files(self, rel_dir: str, suffix: str = "") -> list[str]:
        """List files in a project subdirectory."""
        d = self.project / rel_dir
        if not d.is_dir():
            return []
        files = []
        for p in sorted(d.iterdir()):
            if p.is_file() and (not suffix or p.suffix == suffix):
                files.append(str(p.relative_to(self.project)))
        return files
