from pathlib import Path


class FileClassifier:
    """Maps file paths to logical domain names based on config rules."""

    def __init__(self, domain_rules: dict[str, str]):
        self._rules = domain_rules

    def classify(self, rel_path: str) -> str:
        """Return domain name for a relative file path."""
        for pattern, domain in self._rules.items():
            if rel_path.startswith(pattern) or rel_path == pattern:
                return domain
        # Fallback based on extension
        ext = Path(rel_path).suffix
        ext_map = {
            ".py": "Python",
            ".tsx": "Frontend",
            ".ts": "Frontend",
            ".md": "Documentation",
            ".yml": "Configuration",
            ".yaml": "Configuration",
        }
        return ext_map.get(ext, "Other")

    def matches_patterns(self, rel_path: str, patterns: list[str]) -> bool:
        """Check if a file path matches any of the given patterns."""
        for pattern in patterns:
            if rel_path.startswith(pattern) or rel_path == pattern:
                return True
        return False
