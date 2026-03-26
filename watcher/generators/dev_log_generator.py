import subprocess
from datetime import datetime

from watcher.change_tracker import Change, ChangeTracker
from watcher.config import Config
from watcher.generators.base_generator import BaseGenerator
from watcher.utils import markdown_writer as md


class DevLogGenerator(BaseGenerator):
    """Doc 1: Development Logs & Progress — tracks all changes over time."""

    def __init__(self, config: Config):
        super().__init__(config)
        self._entries: list[str] = []
        self._domain_heatmap: dict[str, int] = {}

    @property
    def filename(self) -> str:
        return "01_DEVELOPMENT_LOG.md"

    @property
    def trigger_patterns(self) -> list[str]:
        return []  # Triggers on ALL changes

    def should_update(self, changed_files: set[str]) -> bool:
        return len(changed_files) > 0  # Always update

    def initial_scan(self) -> str:
        git_log = self._get_git_log()
        file_stats = self._get_file_stats()

        content = md.heading(1, "Development Log & Progress")
        content += f"> Auto-generated on {md.timestamp()} | Optoz AI Documentation Watcher\n\n"
        content += md.divider()

        content += md.heading(2, "Git History")
        if git_log:
            content += md.code_block(git_log, "")
        else:
            content += "_No git history available._\n\n"

        content += md.heading(2, "Project File Statistics")
        content += md.table(
            ["Category", "Count"],
            [[cat, str(count)] for cat, count in file_stats.items()],
        )
        content += md.bar_chart_text(file_stats)

        # Domain size breakdown
        domain_stats = self._get_domain_stats()
        if domain_stats:
            content += md.heading(2, "Code Distribution by Domain")
            content += md.bar_chart_text(domain_stats)

        content += md.heading(2, "Change Log")
        content += f"_Watcher started at {md.timestamp()}. Changes will be logged below._\n\n"

        return content

    def update(self, changes: list[Change]) -> str | None:
        if not changes:
            return None

        # Build new entry
        groups = ChangeTracker.group_by_domain(changes)
        entry = md.heading(3, f"Changes at {md.timestamp()}")
        entry += f"**{len(changes)} file(s) changed**\n\n"

        # Domain change frequency chart
        domain_counts = {d: len(cs) for d, cs in sorted(groups.items())}
        entry += md.bar_chart_text(domain_counts)

        rows = []
        for domain, domain_changes in sorted(groups.items()):
            files = sorted(set(c.rel_path for c in domain_changes))
            for f in files:
                types = set(c.change_type for c in domain_changes if c.rel_path == f)
                rows.append([domain, f, ", ".join(types)])

        entry += md.table(["Domain", "File", "Change Type"], rows)

        # Accumulate domain heatmap data
        for domain, count in domain_counts.items():
            self._domain_heatmap[domain] = self._domain_heatmap.get(domain, 0) + count

        self._entries.append(entry)

        # Rebuild full doc
        git_log = self._get_git_log()
        file_stats = self._get_file_stats()

        content = md.heading(1, "Development Log & Progress")
        content += f"> Auto-generated on {md.timestamp()} | Optoz AI Documentation Watcher\n\n"
        content += md.divider()

        content += md.heading(2, "Git History")
        if git_log:
            content += md.code_block(git_log, "")
        else:
            content += "_No git history available._\n\n"

        content += md.heading(2, "Project File Statistics")
        content += md.table(
            ["Category", "Count"],
            [[cat, str(count)] for cat, count in file_stats.items()],
        )
        content += md.bar_chart_text(file_stats)

        # Cumulative change heatmap
        if self._domain_heatmap:
            content += md.heading(2, "Change Heatmap (Cumulative)")
            content += "_Shows which domains are getting the most edits across all sessions_\n\n"
            sorted_heatmap = dict(sorted(self._domain_heatmap.items(), key=lambda x: -x[1]))
            content += md.bar_chart_text(sorted_heatmap)

        content += md.heading(2, "Change Log")
        content += f"_Total sessions recorded: {len(self._entries)}_\n\n"
        # Most recent first
        for entry_text in reversed(self._entries):
            content += entry_text
            content += md.divider()

        return content

    def _get_git_log(self) -> str:
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "--no-color", "-30"],
                cwd=str(self.project),
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    def _get_file_stats(self) -> dict[str, int]:
        stats: dict[str, int] = {}
        suffix_map = {
            ".py": "Python files",
            ".tsx": "React TSX files",
            ".ts": "TypeScript files",
            ".md": "Markdown docs",
            ".yml": "YAML configs",
            ".yaml": "YAML configs",
        }
        import os

        for root, dirs, files in os.walk(self.project):
            dirs[:] = [d for d in dirs if d not in self.config.watch_excludes]
            for f in files:
                ext = "." + f.rsplit(".", 1)[-1] if "." in f else ""
                category = suffix_map.get(ext, None)
                if category:
                    stats[category] = stats.get(category, 0) + 1

        return dict(sorted(stats.items()))

    def _get_domain_stats(self) -> dict[str, int]:
        """Count lines of code per domain."""
        import os
        from watcher.utils.file_classifier import FileClassifier

        classifier = FileClassifier(self.config.domain_rules)
        domain_lines: dict[str, int] = {}

        for root, dirs, files in os.walk(self.project):
            dirs[:] = [d for d in dirs if d not in self.config.watch_excludes]
            for fname in files:
                if not any(fname.endswith(ext) for ext in (".py", ".tsx", ".ts")):
                    continue
                abs_path = os.path.join(root, fname)
                try:
                    rel = os.path.relpath(abs_path, self.project)
                except ValueError:
                    continue
                domain = classifier.classify(rel)
                try:
                    with open(abs_path, encoding="utf-8", errors="replace") as f:
                        line_count = sum(1 for _ in f)
                    domain_lines[domain] = domain_lines.get(domain, 0) + line_count
                except OSError:
                    pass

        return dict(sorted(domain_lines.items(), key=lambda x: -x[1]))
