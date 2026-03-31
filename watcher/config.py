import argparse
import os
import sys
import yaml
from pathlib import Path


class Config:
    def __init__(self, config_path: str | None = None, cli_args: list[str] | None = None):
        # Parse CLI arguments
        parser = argparse.ArgumentParser(description="Documentation Watcher")
        parser.add_argument("project", nargs="?", help="Path to project directory")
        parser.add_argument("--output", "-o", help="Output directory for generated docs")
        parser.add_argument("--config", "-c", help="Path to config.yaml")
        parser.add_argument("--name", "-n", help="Project name override")
        parser.add_argument("--port", "-p", type=int, default=None, help="Port to serve the dashboard on (e.g. 8080)")
        args = parser.parse_args(cli_args if cli_args is not None else sys.argv[1:])

        # Determine config file path
        if args.config:
            config_file = args.config
        elif config_path:
            config_file = config_path
        else:
            config_file = os.environ.get("DOC_WATCHER_CONFIG", "/app/config.yaml")

        # Load config file (optional — all fields have defaults)
        if os.path.isfile(config_file):
            with open(config_file) as f:
                self._data = yaml.safe_load(f) or {}
        else:
            self._data = {}

        # Store CLI overrides
        self._cli_project = args.project
        self._cli_output = args.output
        self._cli_name = args.name
        self._cli_port = args.port

        # Profile will be set by main.py after detection
        self._profile = None

    def apply_profile(self, profile):
        """Apply auto-detected profile, filling in missing config values."""
        self._profile = profile

    @property
    def profile(self):
        return self._profile

    @property
    def project_path(self) -> Path:
        # CLI > env var > config.yaml > current directory
        if self._cli_project:
            return Path(self._cli_project).resolve()
        env = os.environ.get("DOC_WATCHER_PROJECT_PATH")
        if env:
            return Path(env).resolve()
        if "project_path" in self._data:
            return Path(self._data["project_path"])
        return Path.cwd()

    @property
    def output_path(self) -> Path:
        if self._cli_output:
            return Path(self._cli_output).resolve()
        env = os.environ.get("DOC_WATCHER_OUTPUT_PATH")
        if env:
            return Path(env).resolve()
        if "output_path" in self._data:
            return Path(self._data["output_path"])
        return self.project_path / "docs" / "generated"

    @property
    def project_name(self) -> str:
        if self._cli_name:
            return self._cli_name
        if self._data.get("project_name"):
            return self._data["project_name"]
        if self._profile:
            return self._profile.name
        return self.project_path.name

    @property
    def port(self) -> int | None:
        if self._cli_port is not None:
            return self._cli_port
        env = os.environ.get("DOC_WATCHER_PORT")
        if env:
            return int(env)
        return self._data.get("port", None)

    @property
    def flush_interval(self) -> int:
        return self._data.get("flush_interval_seconds", 900)

    @property
    def poll_interval(self) -> int:
        return self._data.get("poll_interval_seconds", 3)

    @property
    def watch_excludes(self) -> list[str]:
        return self._data.get("watch_excludes", [
            "__pycache__", ".git", ".venv", "venv", "node_modules",
            "*.log", "*.pyc", ".DS_Store",
        ])

    @property
    def domain_rules(self) -> dict[str, str]:
        rules = self._data.get("domain_rules", {})
        if not rules and self._profile:
            return self._profile.domain_rules
        return rules

    @property
    def architecture_patterns(self) -> list[str]:
        patterns = self._data.get("architecture_patterns", [])
        if not patterns and self._profile:
            return self._profile.architecture_patterns
        return patterns

    @property
    def dataflow_patterns(self) -> list[str]:
        patterns = self._data.get("dataflow_patterns", [])
        if not patterns and self._profile:
            return self._profile.dataflow_patterns
        return patterns

    @property
    def audit_patterns(self) -> list[str]:
        patterns = self._data.get("audit_patterns", [])
        if not patterns and self._profile:
            return self._profile.event_patterns
        return patterns

    @property
    def visual_patterns(self) -> list[str]:
        patterns = self._data.get("visual_patterns", [])
        if not patterns and self._profile:
            return self._profile.visual_patterns
        return patterns
