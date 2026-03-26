import yaml
from pathlib import Path


class Config:
    def __init__(self, config_path: str = "/app/config.yaml"):
        with open(config_path) as f:
            self._data = yaml.safe_load(f)

    @property
    def project_path(self) -> Path:
        return Path(self._data["project_path"])

    @property
    def output_path(self) -> Path:
        return Path(self._data["output_path"])

    @property
    def flush_interval(self) -> int:
        return self._data.get("flush_interval_seconds", 900)

    @property
    def poll_interval(self) -> int:
        return self._data.get("poll_interval_seconds", 3)

    @property
    def watch_excludes(self) -> list[str]:
        return self._data.get("watch_excludes", [])

    @property
    def domain_rules(self) -> dict[str, str]:
        return self._data.get("domain_rules", {})

    @property
    def architecture_patterns(self) -> list[str]:
        return self._data.get("architecture_patterns", [])

    @property
    def dataflow_patterns(self) -> list[str]:
        return self._data.get("dataflow_patterns", [])

    @property
    def audit_patterns(self) -> list[str]:
        return self._data.get("audit_patterns", [])

    @property
    def visual_patterns(self) -> list[str]:
        return self._data.get("visual_patterns", [])
