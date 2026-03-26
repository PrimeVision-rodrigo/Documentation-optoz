"""
Dashboard Generator — Produces a self-contained interactive HTML dashboard.

Collects all parsed data from the project and renders it as a single-page
HTML file with embedded CSS and JavaScript. No external dependencies needed
at build time; uses CDN links for Chart.js at view time.
"""

import json
import os
import re
import subprocess
from pathlib import Path

from watcher.change_tracker import Change, ChangeTracker
from watcher.config import Config
from watcher.generators.base_generator import BaseGenerator
from watcher.analyzers.code_analyzer import analyze_project
from watcher.analyzers.claude_advisor import check_and_run as check_claude
from watcher.analyzers.manual_annotations import ANNOTATIONS as MANUAL_ANNOTATIONS
from watcher.parsers.provenance_parser import parse_provenance
from watcher.parsers.python_parser import parse_models_file, parse_routes_file, parse_docker_compose
from watcher.parsers.typescript_parser import parse_app_tsx, parse_tsx_file
from watcher.utils.file_classifier import FileClassifier
from watcher.utils import markdown_writer as md


class DashboardGenerator(BaseGenerator):
    """Generates an interactive HTML dashboard (dashboard.html)."""

    def __init__(self, config: Config):
        super().__init__(config)
        self._change_history: list[dict] = []

    @property
    def filename(self) -> str:
        return "dashboard.html"

    @property
    def trigger_patterns(self) -> list[str]:
        return []  # All patterns

    def should_update(self, changed_files: set[str]) -> bool:
        return len(changed_files) > 0

    def initial_scan(self) -> str:
        return self._build()

    def update(self, changes: list[Change]) -> str | None:
        # Record change history for heatmap
        groups = ChangeTracker.group_by_domain(changes)
        entry = {
            "timestamp": md.timestamp(),
            "total": len(changes),
            "domains": {d: len(cs) for d, cs in groups.items()},
            "files": list(ChangeTracker.changed_files(changes)),
        }
        self._change_history.append(entry)
        return self._build()

    def _build(self) -> str:
        data = self._collect_data()
        return self._render_html(data)

    def _collect_data(self) -> dict:
        """Collect all project data into a single dict for the dashboard."""
        data = {}

        # 1. File statistics by domain
        classifier = FileClassifier(self.config.domain_rules)
        domain_lines = {}
        domain_files = {}
        file_sizes = {}

        for root, dirs, files in os.walk(self.project):
            dirs[:] = [d for d in dirs if d not in self.config.watch_excludes]
            for fname in files:
                if not any(fname.endswith(ext) for ext in (".py", ".tsx", ".ts", ".md", ".yml", ".yaml")):
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
                    domain_files[domain] = domain_files.get(domain, 0) + 1
                    file_sizes[rel] = line_count
                except OSError:
                    pass

        data["domain_lines"] = dict(sorted(domain_lines.items(), key=lambda x: -x[1]))
        data["domain_files"] = dict(sorted(domain_files.items(), key=lambda x: -x[1]))
        data["file_sizes"] = dict(sorted(file_sizes.items(), key=lambda x: -x[1])[:30])

        # 2. Database models
        models_path = self.project / "app" / "models.py"
        if models_path.is_file():
            models = parse_models_file(models_path)
            data["models"] = [
                {"name": m["name"], "table": m["tablename"], "columns": len(m["columns"])}
                for m in models
            ]
        else:
            data["models"] = []

        # 3. API endpoints
        route_dir = self.project / "app" / "routes"
        endpoints = []
        if route_dir.is_dir():
            for rf in sorted(route_dir.glob("*.py")):
                if rf.name == "__init__.py":
                    continue
                routes = parse_routes_file(rf)
                for r in routes:
                    endpoints.append({
                        "module": rf.stem,
                        "method": r["method"],
                        "path": r["path"],
                        "handler": r["function"],
                    })
        data["endpoints"] = endpoints

        # 4. Docker services
        compose_path = self.project / "docker-compose.yml"
        if compose_path.is_file():
            data["services"] = parse_docker_compose(compose_path)
        else:
            data["services"] = []

        # 5. Frontend pages and their API calls
        pages_dir = self.project / "my-app" / "src" / "pages"
        pages = []
        frontend_api_calls = []
        if pages_dir.is_dir():
            for pf in sorted(pages_dir.glob("*.tsx")):
                parsed = parse_tsx_file(pf)
                page_info = {
                    "name": pf.stem,
                    "lines": parsed.get("line_count", 0),
                    "ant_design": parsed.get("ant_design", []),
                    "api_calls": parsed.get("api_calls", []),
                }
                pages.append(page_info)
                for call in parsed.get("api_calls", []):
                    frontend_api_calls.append({
                        "page": pf.stem,
                        "method": call["method"],
                        "url": call["url"],
                    })
        data["pages"] = pages
        data["frontend_api_calls"] = frontend_api_calls

        # 6. Event types
        ep_source = self._read_file("app/services/event_publisher.py")
        event_types = []
        if ep_source:
            in_dict = False
            for line in ep_source.splitlines():
                if "EVENT_TYPE_TO_AUDIT_ACTION" in line:
                    in_dict = True
                    continue
                if in_dict:
                    if line.strip() == "}":
                        break
                    match = re.match(r'\s*"(\w+)":\s*"(\w+)"', line)
                    if match:
                        event_types.append({"type": match.group(1), "action": match.group(2)})
        data["event_types"] = event_types

        # 7. Event sources per route
        event_sources = {}
        if route_dir.is_dir():
            for rf in sorted(route_dir.glob("*.py")):
                if rf.name == "__init__.py":
                    continue
                source = self._read_file(f"app/routes/{rf.name}")
                if "publish_event" not in source:
                    continue
                events = set()
                for match in re.finditer(r'event_type\s*=\s*"(\w+)"', source):
                    events.add(match.group(1))
                for match in re.finditer(r'publish_event\s*\([^,]+,\s*"(\w+)"', source):
                    events.add(match.group(1))
                if events:
                    event_sources[rf.stem] = sorted(events)
        data["event_sources"] = event_sources

        # 8. Routes
        app_tsx = self.project / "my-app" / "src" / "App.tsx"
        if app_tsx.is_file():
            parsed = parse_app_tsx(app_tsx)
            data["routes"] = parsed.get("routes", [])
        else:
            data["routes"] = []

        # 9. API coverage matrix (which pages call which route modules)
        backend_modules = set()
        for ep in endpoints:
            backend_modules.add(ep["module"])
        page_names = [p["name"] for p in pages]

        # Map frontend API URLs to backend modules
        coverage = {}
        for fc in frontend_api_calls:
            url = fc["url"]
            page = fc["page"]
            # Try to match URL to a route module
            for ep in endpoints:
                if ep["path"] and ep["path"] in url:
                    key = f"{page}|{ep['module']}"
                    coverage[key] = coverage.get(key, 0) + 1

        data["coverage_matrix"] = {
            "pages": page_names,
            "modules": sorted(backend_modules),
            "hits": coverage,
        }

        # 10. Git history
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "--no-color", "-20"],
                cwd=str(self.project),
                capture_output=True,
                text=True,
                timeout=10,
            )
            data["git_log"] = result.stdout.strip().splitlines() if result.returncode == 0 else []
        except (subprocess.TimeoutExpired, FileNotFoundError):
            data["git_log"] = []

        # 11. Change history
        data["change_history"] = self._change_history

        # 12. Image provenance / hash chain
        data["provenance"] = parse_provenance(self.project)

        # 13. Timestamp
        data["generated_at"] = md.timestamp()

        # 14. Code analysis (rule-based)
        analysis = analyze_project(self.config, data)
        data["analysis"] = analysis

        # 15. Manual annotations (hand-written by Claude during review)
        data["manual_annotations"] = MANUAL_ANNOTATIONS

        # 16. Claude advisor (LLM-powered, if API key available)
        claude_results = check_claude(self.config.output_path, analysis, data)
        data["claude_analysis"] = claude_results

        return data

    def _render_html(self, data: dict) -> str:
        data_json = json.dumps(data, indent=None, default=str)
        return HTML_TEMPLATE.replace("/*__DATA__*/null", data_json)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Optoz v0.1 — Documentation Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
:root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #242736;
    --border: #2e3347;
    --text: #e1e4ed;
    --text-dim: #8b8fa3;
    --accent: #6c8cff;
    --accent2: #4fc3f7;
    --green: #81c784;
    --orange: #ffb74d;
    --purple: #ce93d8;
    --red: #ef5350;
    --pink: #f48fb1;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
}
header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 16px 32px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}
header h1 { font-size: 20px; font-weight: 600; }
header h1 span { color: var(--accent); }
header .meta { color: var(--text-dim); font-size: 13px; }
nav {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 0 32px;
    display: flex;
    gap: 0;
    overflow-x: auto;
}
nav button {
    background: none;
    border: none;
    color: var(--text-dim);
    padding: 12px 20px;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    border-bottom: 2px solid transparent;
    white-space: nowrap;
    transition: all 0.15s;
}
nav button:hover { color: var(--text); }
nav button.active { color: var(--accent); border-bottom-color: var(--accent); }
main { padding: 24px 32px; max-width: 1400px; margin: 0 auto; }
.tab-content { display: none; }
.tab-content.active { display: block; }
.grid { display: grid; gap: 20px; }
.grid-2 { grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); }
.grid-3 { grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
.card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
}
.card h3 {
    font-size: 14px;
    font-weight: 600;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 16px;
}
.stat-row {
    display: flex;
    gap: 20px;
    margin-bottom: 24px;
    flex-wrap: wrap;
}
.stat {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
    min-width: 160px;
    flex: 1;
}
.stat .value { font-size: 32px; font-weight: 700; color: var(--accent); }
.stat .label { font-size: 12px; color: var(--text-dim); margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}
th {
    text-align: left;
    padding: 10px 12px;
    color: var(--text-dim);
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid var(--border);
}
td {
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
}
tr:hover td { background: var(--surface2); }
.method {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
    font-family: monospace;
}
.method-GET { background: #1b3a2d; color: var(--green); }
.method-POST { background: #2d2a1b; color: var(--orange); }
.method-PUT { background: #1b2a3a; color: var(--accent2); }
.method-DELETE { background: #3a1b1b; color: var(--red); }
.method-PATCH { background: #2d1b3a; color: var(--purple); }
.heatmap-grid {
    display: grid;
    gap: 3px;
    margin-top: 12px;
}
.heatmap-cell {
    border-radius: 3px;
    height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    font-weight: 600;
    cursor: default;
    transition: transform 0.1s;
}
.heatmap-cell:hover { transform: scale(1.05); z-index: 1; }
.coverage-grid {
    display: grid;
    gap: 2px;
    margin-top: 12px;
    overflow-x: auto;
}
.coverage-cell {
    width: 100%;
    aspect-ratio: 1;
    border-radius: 3px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 9px;
    font-weight: 700;
    min-width: 28px;
    min-height: 28px;
}
.coverage-header {
    font-size: 10px;
    color: var(--text-dim);
    writing-mode: vertical-rl;
    text-orientation: mixed;
    transform: rotate(180deg);
    padding: 4px 2px;
    max-height: 80px;
    overflow: hidden;
    text-overflow: ellipsis;
}
.coverage-label {
    font-size: 11px;
    color: var(--text-dim);
    padding: 4px 8px 4px 0;
    text-align: right;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 120px;
}
.event-tag {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    margin: 2px;
    background: var(--surface2);
    border: 1px solid var(--border);
}
.chart-container { position: relative; height: 300px; }
.badge {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 8px;
    font-size: 11px;
    font-weight: 600;
}
.git-log {
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 12px;
    line-height: 2;
}
.git-log .hash { color: var(--accent); }
.git-log .msg { color: var(--text); }
.arch-node {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 16px;
    margin: 6px;
}
.arch-node .dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
}
.treemap-container { position: relative; min-height: 250px; }
.treemap-cell {
    position: absolute;
    border-radius: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-weight: 600;
    color: #000;
    overflow: hidden;
    text-align: center;
    padding: 4px;
    cursor: default;
    transition: opacity 0.15s;
    line-height: 1.2;
}
.treemap-cell:hover { opacity: 0.85; }

/* --- Flow Diagrams --- */
.flow-journey {
    display: flex;
    align-items: center;
    gap: 0;
    overflow-x: auto;
    padding: 16px 0;
}
.flow-step {
    display: flex;
    flex-direction: column;
    align-items: center;
    min-width: 100px;
    flex-shrink: 0;
}
.flow-step .icon {
    width: 52px;
    height: 52px;
    border-radius: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 22px;
    font-weight: 700;
    margin-bottom: 8px;
    border: 2px solid transparent;
    transition: transform 0.15s;
}
.flow-step .icon:hover { transform: scale(1.1); }
.flow-step .step-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--text);
    text-align: center;
    line-height: 1.3;
}
.flow-step .step-sub {
    font-size: 9px;
    color: var(--text-dim);
    text-align: center;
    margin-top: 2px;
}
.flow-arrow-right {
    font-size: 18px;
    color: var(--text-dim);
    margin: 0 4px;
    flex-shrink: 0;
    margin-bottom: 20px;
}

/* --- Architecture Diagram --- */
.arch-diagram {
    position: relative;
    min-height: 340px;
    padding: 20px;
}
.arch-diagram svg {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
}
.arch-layer {
    display: flex;
    justify-content: center;
    gap: 24px;
    margin-bottom: 24px;
    position: relative;
    z-index: 1;
}
.arch-layer-label {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text-dim);
    margin-bottom: 8px;
    text-align: center;
}
.arch-box {
    background: var(--surface2);
    border: 2px solid var(--border);
    border-radius: 12px;
    padding: 14px 20px;
    text-align: center;
    min-width: 140px;
    transition: border-color 0.15s, transform 0.15s;
    cursor: default;
}
.arch-box:hover { border-color: var(--accent); transform: translateY(-2px); }
.arch-box .box-name { font-size: 13px; font-weight: 700; }
.arch-box .box-detail { font-size: 10px; color: var(--text-dim); margin-top: 2px; }
.arch-box .box-hint { font-size: 9px; color: var(--accent); margin-top: 4px; opacity: 0; transition: opacity 0.15s; }
.arch-box:hover .box-hint { opacity: 1; }
.arch-box.frontend { border-color: #4FC3F7; }
.arch-box.backend { border-color: #81C784; }
.arch-box.worker { border-color: #FFB74D; }
.arch-box.storage { border-color: #CE93D8; }
.arch-box.active { box-shadow: 0 0 0 2px var(--accent); }

/* Architecture detail panel */
.arch-detail {
    margin-top: 20px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    animation: slideDown 0.2s ease-out;
}
@keyframes slideDown {
    from { opacity: 0; max-height: 0; }
    to { opacity: 1; max-height: 2000px; }
}
.arch-detail-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
}
.arch-detail-header h4 { font-size: 15px; font-weight: 700; }
.arch-detail-close {
    background: none;
    border: 1px solid var(--border);
    color: var(--text-dim);
    width: 28px;
    height: 28px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.15s;
}
.arch-detail-close:hover { background: var(--surface); color: var(--text); }
.arch-detail-body {
    padding: 16px 20px;
    max-height: 500px;
    overflow-y: auto;
}
.arch-detail-body table { margin-top: 8px; }
.arch-detail-tabs {
    display: flex;
    gap: 0;
    border-bottom: 1px solid var(--border);
}
.arch-detail-tabs button {
    background: none;
    border: none;
    color: var(--text-dim);
    padding: 10px 16px;
    cursor: pointer;
    font-size: 12px;
    font-weight: 500;
    border-bottom: 2px solid transparent;
    transition: all 0.15s;
}
.arch-detail-tabs button:hover { color: var(--text); }
.arch-detail-tabs button.active { color: var(--accent); border-bottom-color: var(--accent); }
.arch-detail-section { display: none; }
.arch-detail-section.active { display: block; }
.arch-mini-stat {
    display: inline-flex;
    align-items: baseline;
    gap: 6px;
    margin-right: 20px;
    margin-bottom: 8px;
}
.arch-mini-stat .num { font-size: 22px; font-weight: 700; color: var(--accent); }
.arch-mini-stat .lbl { font-size: 11px; color: var(--text-dim); }

/* --- Data Pipeline Diagram --- */
.pipeline {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 12px 0;
}
.pipe-row {
    display: flex;
    align-items: center;
    gap: 8px;
}
.pipe-node {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 12px;
    font-weight: 600;
    text-align: center;
    min-width: 110px;
    transition: border-color 0.15s;
}
.pipe-node:hover { border-color: var(--accent); }
.pipe-node.highlight { border-color: var(--green); background: rgba(129,199,132,0.1); }
.pipe-arrow { color: var(--text-dim); font-size: 16px; flex-shrink: 0; }
.pipe-label {
    font-size: 9px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    min-width: 60px;
    text-align: right;
    padding-right: 8px;
    flex-shrink: 0;
}
.pipe-group {
    display: flex;
    gap: 6px;
}

/* --- Triple Write Diagram --- */
.triple-write {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0;
    padding: 16px 0;
}
.tw-source {
    background: var(--surface2);
    border: 2px solid var(--orange);
    border-radius: 12px;
    padding: 14px 28px;
    font-size: 14px;
    font-weight: 700;
    text-align: center;
}
.tw-connector {
    display: flex;
    align-items: flex-start;
    justify-content: center;
    gap: 0;
    margin-top: -1px;
}
.tw-line-v {
    width: 2px;
    height: 30px;
    background: var(--text-dim);
}
.tw-branch {
    display: flex;
    align-items: flex-start;
    justify-content: center;
    position: relative;
}
.tw-branch::before {
    content: '';
    position: absolute;
    top: 0;
    left: 16.67%;
    right: 16.67%;
    height: 2px;
    background: var(--text-dim);
}
.tw-sink-group {
    display: flex;
    gap: 24px;
    justify-content: center;
}
.tw-sink-col {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0;
}
.tw-sink {
    border: 2px solid;
    border-radius: 12px;
    padding: 12px 18px;
    text-align: center;
    min-width: 160px;
}
.tw-sink .sink-name { font-size: 13px; font-weight: 700; }
.tw-sink .sink-table { font-size: 10px; color: var(--text-dim); margin-top: 2px; font-family: monospace; }
.tw-sink .sink-desc { font-size: 10px; color: var(--text-dim); margin-top: 4px; }
.tw-sink.primary { border-color: var(--green); }
.tw-sink.secondary { border-color: var(--purple); }
.tw-sink.stream { border-color: var(--accent2); }
.tw-step-num {
    font-size: 10px;
    font-weight: 700;
    color: var(--text-dim);
    margin-bottom: 4px;
}

/* --- Provenance Diagram --- */
.prov-timeline {
    display: flex;
    gap: 0;
    overflow-x: auto;
    padding: 16px 0;
    align-items: flex-start;
}
.prov-stage {
    min-width: 200px;
    max-width: 240px;
    flex-shrink: 0;
}
.prov-stage-header {
    background: var(--surface2);
    border: 2px solid var(--border);
    border-radius: 10px 10px 0 0;
    padding: 10px 14px;
    text-align: center;
}
.prov-stage-header .prov-title { font-size: 13px; font-weight: 700; }
.prov-stage-header .prov-file { font-size: 9px; color: var(--text-dim); font-family: monospace; margin-top: 2px; }
.prov-stage-body {
    border: 1px solid var(--border);
    border-top: none;
    border-radius: 0 0 10px 10px;
    padding: 10px 12px;
    font-size: 11px;
    min-height: 80px;
}
.prov-section { margin-bottom: 8px; }
.prov-section-label {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 3px;
}
.prov-section-label.compute { color: var(--green); }
.prov-section-label.store { color: var(--purple); }
.prov-section-label.read { color: var(--accent2); }
.prov-section-label.event { color: var(--orange); }
.prov-section-label.chain { color: var(--pink); }
.prov-item {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    color: var(--text);
    padding: 2px 0;
    line-height: 1.4;
    word-break: break-all;
}
.prov-item.missing { color: var(--text-dim); font-style: italic; font-family: inherit; }
.prov-event-badge {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 6px;
    font-size: 9px;
    font-weight: 600;
    background: rgba(255,183,77,0.15);
    border: 1px solid var(--orange);
    color: var(--orange);
    margin: 1px 0;
}
.prov-hash-badge {
    display: inline-block;
    padding: 1px 5px;
    border-radius: 4px;
    font-size: 9px;
    font-weight: 600;
    margin: 1px 2px;
}
.prov-hash-badge.present { background: rgba(129,199,132,0.15); border: 1px solid var(--green); color: var(--green); }
.prov-hash-badge.absent { background: rgba(239,83,80,0.1); border: 1px solid var(--red); color: var(--red); text-decoration: line-through; }
.prov-arrow-col {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-width: 40px;
    flex-shrink: 0;
    padding-top: 30px;
}
.prov-arrow-label {
    font-size: 8px;
    color: var(--text-dim);
    writing-mode: vertical-rl;
    text-orientation: mixed;
    transform: rotate(180deg);
    max-height: 60px;
    overflow: hidden;
    margin-bottom: 4px;
}
.prov-flow-arrows {
    margin-top: 16px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}
.prov-flow-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 11px;
}
.prov-flow-chip .from { color: var(--green); font-weight: 600; }
.prov-flow-chip .to { color: var(--accent2); font-weight: 600; }
.prov-flow-chip .via { color: var(--text-dim); font-family: monospace; font-size: 10px; }

/* --- Analysis Annotations --- */
.analysis-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 16px;
    border-radius: 8px;
    margin-bottom: 12px;
    font-size: 12px;
    cursor: pointer;
    transition: all 0.15s;
    border: 1px solid var(--border);
    border-left: 4px solid;
}
.analysis-bar:hover { background: var(--surface2); }
.analysis-bar.complete { border-left-color: var(--green); background: rgba(129,199,132,0.06); }
.analysis-bar.partial { border-left-color: var(--orange); background: rgba(255,183,77,0.06); }
.analysis-bar.needs_work { border-left-color: var(--red); background: rgba(239,83,80,0.06); }
.analysis-bar.missing { border-left-color: var(--red); background: rgba(239,83,80,0.08); }
.analysis-bar.info { border-left-color: var(--accent); background: rgba(108,140,255,0.04); }
.analysis-status {
    padding: 2px 8px;
    border-radius: 6px;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    flex-shrink: 0;
}
.analysis-status.complete { background: var(--green); color: #000; }
.analysis-status.partial { background: var(--orange); color: #000; }
.analysis-status.needs_work { background: var(--red); color: #fff; }
.analysis-status.missing { background: var(--red); color: #fff; }
.analysis-status.info { background: var(--accent); color: #fff; }
.analysis-progress {
    width: 60px;
    height: 6px;
    background: var(--surface);
    border-radius: 3px;
    overflow: hidden;
    flex-shrink: 0;
}
.analysis-progress-fill {
    height: 100%;
    border-radius: 3px;
    transition: width 0.3s;
}
.analysis-summary { flex: 1; color: var(--text); font-size: 12px; }
.analysis-toggle { color: var(--text-dim); font-size: 14px; flex-shrink: 0; transition: transform 0.2s; }
.analysis-toggle.open { transform: rotate(180deg); }
.analysis-detail {
    display: none;
    padding: 0 14px 12px;
    font-size: 11px;
}
.analysis-detail.open { display: block; }
.analysis-detail .findings { margin-bottom: 8px; }
.analysis-detail .finding {
    padding: 3px 0;
    color: var(--text-dim);
    border-left: 2px solid var(--border);
    padding-left: 10px;
    margin: 3px 0;
}
.analysis-detail .recs { margin-bottom: 8px; }
.analysis-detail .rec {
    padding: 4px 8px;
    background: rgba(108,140,255,0.08);
    border-left: 2px solid var(--accent);
    padding-left: 10px;
    margin: 3px 0;
    color: var(--text);
}
.analysis-detail .claude-rec {
    padding: 4px 8px;
    background: rgba(206,147,216,0.08);
    border-left: 2px solid var(--purple);
    padding-left: 10px;
    margin: 3px 0;
    color: var(--text);
}
.claude-label {
    font-size: 9px;
    font-weight: 700;
    color: var(--purple);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 4px;
}
</style>
</head>
<body>
<header>
    <h1><span>Optoz</span> v0.1 — Documentation Dashboard</h1>
    <div class="meta" id="timestamp"></div>
</header>
<nav id="tabs">
    <button class="active" data-tab="overview">Overview</button>
    <button data-tab="architecture">Architecture</button>
    <button data-tab="dataflow">Data Flow</button>
    <button data-tab="audit">Audit Trail</button>
    <button data-tab="frontend">Frontend</button>
    <button data-tab="changes">Change Log</button>
</nav>
<main>

<!-- OVERVIEW -->
<div class="tab-content active" id="tab-overview">
    <div class="stat-row" id="stats-row"></div>
    <div class="card" style="margin-bottom:20px">
        <h3>User Journey — Expected Workflow</h3>
        <p style="color:var(--text-dim);font-size:12px;margin-bottom:12px">The standard sequence a user follows in the Optoz AI inspection platform, from login through production monitoring.</p>
        <div class="flow-journey" id="user-journey"></div>
    </div>
    <div class="grid grid-2">
        <div class="card">
            <h3>Code Distribution (Lines by Domain)</h3>
            <div class="treemap-container" id="treemap"></div>
        </div>
        <div class="card">
            <h3>File Count by Domain</h3>
            <div class="chart-container"><canvas id="chart-domain-files"></canvas></div>
        </div>
    </div>
    <div class="card" style="margin-top:20px">
        <h3>Git History</h3>
        <div class="git-log" id="git-log"></div>
    </div>
</div>

<!-- ARCHITECTURE -->
<div class="tab-content" id="tab-architecture">
    <div class="card" style="margin-bottom:20px">
        <h3>System Architecture</h3>
        <div class="arch-diagram" id="arch-diagram"></div>
        <div id="arch-detail-container"></div>
    </div>
    <div class="grid grid-2">
        <div class="card">
            <h3>Database Models</h3>
            <table id="models-table"></table>
        </div>
        <div class="card">
            <h3>Docker Services</h3>
            <table id="services-table"></table>
        </div>
    </div>
    <div class="card" style="margin-top:20px">
        <h3>API Endpoints</h3>
        <table id="endpoints-table"></table>
    </div>
</div>

<!-- DATAFLOW -->
<div class="tab-content" id="tab-dataflow">
    <div class="card" style="margin-bottom:20px">
        <h3>Data Pipeline — Request Flow</h3>
        <p style="color:var(--text-dim);font-size:12px;margin-bottom:4px">How data flows through each layer of the system for key operations.</p>
        <div id="data-pipeline"></div>
    </div>
    <div class="card" style="margin-bottom:20px">
        <h3>API Coverage Matrix</h3>
        <p style="color:var(--text-dim);font-size:12px;margin-bottom:12px">
            Frontend pages (rows) vs Backend route modules (columns). Green = connected, dark = no calls detected.
        </p>
        <div style="overflow-x:auto" id="coverage-matrix"></div>
    </div>
    <div class="card" style="margin-top:20px">
        <h3>Frontend API Calls</h3>
        <table id="api-calls-table"></table>
    </div>
</div>

<!-- AUDIT -->
<div class="tab-content" id="tab-audit">
    <div class="stat-row" id="audit-stats"></div>
    <div class="card" style="margin-bottom:20px">
        <h3>Triple-Write Architecture</h3>
        <p style="color:var(--text-dim);font-size:12px;margin-bottom:8px">Every state change atomically writes to three sinks, forming a tamper-evident audit trail (21 CFR Part 11).</p>
        <div id="triple-write-diagram"></div>
    </div>
    <div class="card" style="margin-bottom:20px">
        <h3>Image Provenance — Hash Chain Across Lifecycle</h3>
        <p style="color:var(--text-dim);font-size:12px;margin-bottom:8px">Parsed live from source code. Shows how image identity (hashes) flows through each stage. Green borders = hash is computed. Purple = hash is stored. Dashed = hash is read from a prior stage.</p>
        <div id="provenance-diagram"></div>
    </div>
    <div class="grid grid-2">
        <div class="card">
            <h3>Event Types by Category</h3>
            <div class="chart-container"><canvas id="chart-event-categories"></canvas></div>
        </div>
        <div class="card">
            <h3>Event Sources by Route</h3>
            <div class="chart-container"><canvas id="chart-event-sources"></canvas></div>
        </div>
    </div>
    <div class="card" style="margin-top:20px">
        <h3>Event Type Registry</h3>
        <table id="events-table"></table>
    </div>
</div>

<!-- FRONTEND -->
<div class="tab-content" id="tab-frontend">
    <div class="grid grid-2">
        <div class="card">
            <h3>Page Sizes</h3>
            <div class="chart-container"><canvas id="chart-page-sizes"></canvas></div>
        </div>
        <div class="card">
            <h3>Ant Design Usage</h3>
            <div class="chart-container"><canvas id="chart-ant-design"></canvas></div>
        </div>
    </div>
    <div class="card" style="margin-top:20px">
        <h3>Route Map</h3>
        <table id="routes-table"></table>
    </div>
</div>

<!-- CHANGES -->
<div class="tab-content" id="tab-changes">
    <div class="card">
        <h3>Change Heatmap by Domain</h3>
        <div id="change-heatmap"></div>
    </div>
    <div class="card" style="margin-top:20px">
        <h3>Largest Files (Top 30)</h3>
        <div class="chart-container" style="height:400px"><canvas id="chart-file-sizes"></canvas></div>
    </div>
</div>

</main>

<script>
const D = /*__DATA__*/null;

// --- Tabs ---
document.querySelectorAll('nav button').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    });
});

document.getElementById('timestamp').textContent = 'Generated: ' + D.generated_at;

// --- Analysis annotation renderer ---
const A = D.analysis || {};
const MA = D.manual_annotations || {};
const CA = D.claude_analysis || {};

function renderAnalysis(sectionId, containerEl) {
    const a = A[sectionId];
    if (!a) return;
    // Accept either a DOM element or an ID string
    const container = (typeof containerEl === 'string') ? document.getElementById(containerEl) : containerEl;
    if (!container) return;

    const manual = MA[sectionId];
    const claude = CA[sectionId];
    const statusColors = { complete: 'var(--green)', partial: 'var(--orange)', needs_work: 'var(--red)', missing: 'var(--red)', info: 'var(--accent)' };
    const fillColor = statusColors[a.status] || 'var(--accent)';
    const uid = 'a_' + sectionId;

    let html = `<div class="analysis-bar ${a.status}" onclick="document.getElementById('${uid}').classList.toggle('open');this.querySelector('.analysis-toggle').classList.toggle('open')">`;
    html += `<span class="analysis-status ${a.status}">${a.status.replace('_',' ')}</span>`;
    html += `<div class="analysis-progress"><div class="analysis-progress-fill" style="width:${a.progress}%;background:${fillColor}"></div></div>`;
    html += `<span class="analysis-summary">${a.summary}</span>`;
    html += `<span class="analysis-toggle">▼</span>`;
    html += `</div>`;

    html += `<div class="analysis-detail" id="${uid}">`;

    // Manual annotation (Claude review) — shows first as the primary assessment
    if (manual) {
        html += '<div class="recs" style="margin-bottom:12px">';
        html += '<div class="claude-label" style="margin-bottom:6px">Claude Review (V12)</div>';
        if (manual.claude_summary) {
            html += `<div style="font-size:12px;color:var(--text);line-height:1.6;padding:8px 12px;background:rgba(206,147,216,0.06);border-radius:6px;margin-bottom:8px">${manual.claude_summary}</div>`;
        }
        if (manual.recommendations && manual.recommendations.length) {
            manual.recommendations.forEach((r, i) => {
                html += `<div class="claude-rec" style="margin:4px 0;line-height:1.5">${r}</div>`;
            });
        }
        html += '</div>';
    }

    // Rule-based findings
    if (a.findings && a.findings.length) {
        html += '<div class="findings"><div style="font-size:10px;font-weight:700;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px">Code Analysis Findings</div>';
        a.findings.forEach(f => { html += `<div class="finding">${f}</div>`; });
        html += '</div>';
    }

    // Rule-based recommendations
    if (a.recommendations && a.recommendations.length) {
        html += '<div class="recs"><div style="font-size:10px;font-weight:700;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px">Auto-detected Recommendations</div>';
        a.recommendations.forEach(r => { html += `<div class="rec">${r}</div>`; });
        html += '</div>';
    }

    // Claude API analysis (if available)
    if (claude) {
        html += '<div class="recs"><div class="claude-label">Claude AI Live Analysis</div>';
        if (claude.claude_summary) html += `<div class="finding" style="border-color:var(--purple)">${claude.claude_summary}</div>`;
        if (claude.recommendations) claude.recommendations.forEach(r => { html += `<div class="claude-rec">${r}</div>`; });
        html += '</div>';
    }

    if (!a.findings?.length && !a.recommendations?.length && !manual && !claude) {
        html += '<div style="color:var(--text-dim);padding:4px 0">No issues detected.</div>';
    }

    html += '</div>';

    // Insert after the h3 heading, or after any description <p>
    const wrapper = document.createElement('div');
    wrapper.innerHTML = html;
    const h3 = container.querySelector('h3');
    if (h3) {
        // Find the right insertion point: after h3 and any <p> description
        let insertAfter = h3;
        let next = h3.nextElementSibling;
        while (next && next.tagName === 'P') {
            insertAfter = next;
            next = next.nextElementSibling;
        }
        insertAfter.after(wrapper);
    } else {
        container.prepend(wrapper);
    }
}

// Inject analysis into every section - we need data-analysis attributes on cards
// Instead, we call renderAnalysis after all content is built
function injectAllAnalysis() {
    // Map section IDs to their parent card containers
    const mapping = {
        // Overview
        'user_journey': 'user-journey',
        'code_distribution': 'treemap',
        'git_history': 'git-log',
        // Architecture
        'system_architecture': 'arch-diagram',
        'database_models': 'models-table',
        'docker_services': 'services-table',
        'api_endpoints': 'endpoints-table',
        // Data Flow
        'data_pipeline': 'data-pipeline',
        'coverage_matrix': 'coverage-matrix',
        'frontend_api_calls': 'api-calls-table',
        // Audit Trail
        'triple_write': 'triple-write-diagram',
        'provenance': 'provenance-diagram',
        'event_registry': 'events-table',
        // Frontend
        'page_sizes': 'chart-page-sizes',
        'ant_design_usage': 'chart-ant-design',
        'route_map': 'routes-table',
        // Changes
        'change_heatmap': 'change-heatmap',
        'file_sizes': 'chart-file-sizes',
    };

    Object.entries(mapping).forEach(([sectionId, elementId]) => {
        const el = document.getElementById(elementId);
        if (!el) return;
        // Find the parent .card
        let card = el.closest('.card');
        if (!card) card = el.parentElement;
        if (card) renderAnalysis(sectionId, card);
    });

    // TODOs - add as a card at the bottom of overview if present
    if (A.todos) {
        const overview = document.getElementById('tab-overview');
        if (overview) {
            const todoCard = document.createElement('div');
            todoCard.className = 'card';
            todoCard.style.marginTop = '20px';
            todoCard.innerHTML = '<h3>TODO / FIXME Comments</h3>';
            overview.appendChild(todoCard);
            renderAnalysis('todos', todoCard);
        }
    }
}

// --- Colors ---
const COLORS = ['#6c8cff','#4fc3f7','#81c784','#ffb74d','#ce93d8','#f48fb1','#ef5350','#90a4ae','#a1887f','#fff176',
    '#4dd0e1','#aed581','#ff8a65','#ba68c8','#64b5f6','#dce775','#4db6ac','#e57373','#9575cd','#7986cb'];
function getColor(i) { return COLORS[i % COLORS.length]; }

// --- Overview ---
const totalLines = Object.values(D.domain_lines).reduce((a,b) => a+b, 0);
const totalFiles = Object.values(D.domain_files).reduce((a,b) => a+b, 0);
document.getElementById('stats-row').innerHTML = [
    {v: totalLines.toLocaleString(), l: 'Lines of Code'},
    {v: totalFiles, l: 'Source Files'},
    {v: D.endpoints.length, l: 'API Endpoints'},
    {v: D.models.length, l: 'DB Models'},
    {v: D.event_types.length, l: 'Event Types'},
    {v: D.pages.length, l: 'Frontend Pages'},
].map(s => `<div class="stat"><div class="value">${s.v}</div><div class="label">${s.l}</div></div>`).join('');

// --- User Journey ---
const journeySteps = [
    { icon: '🔐', label: 'Login', sub: 'Authenticate', color: '#90a4ae', page: 'Login' },
    { icon: '📁', label: 'Project Hub', sub: 'Create / Select', color: '#6c8cff', page: 'ProjectHub' },
    { icon: '⚙️', label: 'Setup', sub: 'Configure project', color: '#64b5f6', page: 'ProjectSetup' },
    { icon: '📷', label: 'Capture', sub: 'Acquire images', color: '#4fc3f7', page: 'CaptureWorkspace' },
    { icon: '🏷️', label: 'Labeling', sub: 'Annotate defects', color: '#4dd0e1', page: 'LabelingScreen' },
    { icon: '🧠', label: 'Train Setup', sub: 'Model + params', color: '#81c784', page: 'TrainingSetup' },
    { icon: '📋', label: 'Train Queue', sub: 'Monitor jobs', color: '#aed581', page: 'TrainingQueue' },
    { icon: '✅', label: 'Validate', sub: 'PCCP / metrics', color: '#dce775', page: 'Validation' },
    { icon: '🚀', label: 'Deploy', sub: 'Package model', color: '#ffb74d', page: 'Deployment' },
    { icon: '📊', label: 'Monitor', sub: 'Production stats', color: '#ff8a65', page: 'RuntimeMonitoring' },
];
let journeyHTML = '';
journeySteps.forEach((step, i) => {
    if (i > 0) journeyHTML += '<div class="flow-arrow-right">→</div>';
    journeyHTML += `<div class="flow-step">
        <div class="icon" style="background:${step.color}22;border-color:${step.color}">${step.icon}</div>
        <div class="step-label">${step.label}</div>
        <div class="step-sub">${step.sub}</div>
    </div>`;
});
document.getElementById('user-journey').innerHTML = journeyHTML;

// --- Architecture Diagram (clickable) ---
const routeModules = {};
D.endpoints.forEach(e => { routeModules[e.module] = (routeModules[e.module] || 0) + 1; });
const numRouters = Object.keys(routeModules).length;

document.getElementById('arch-diagram').innerHTML = `
    <div class="arch-layer-label">Client Layer</div>
    <div class="arch-layer">
        <div class="arch-box frontend" data-detail="frontend" onclick="showArchDetail('frontend')">
            <div class="box-name">React Frontend</div>
            <div class="box-detail">:5173 · ${D.pages.length} pages · Ant Design</div>
            <div class="box-hint">Click for detail</div>
        </div>
    </div>
    <div style="text-align:center;color:var(--text-dim);font-size:18px;margin:-8px 0">↓ REST API ↓</div>
    <div class="arch-layer-label" style="margin-top:12px">Application Layer</div>
    <div class="arch-layer">
        <div class="arch-box backend" data-detail="backend" onclick="showArchDetail('backend')">
            <div class="box-name">FastAPI Backend</div>
            <div class="box-detail">:8001 · ${D.endpoints.length} endpoints · ${numRouters} routers</div>
            <div class="box-hint">Click for detail</div>
        </div>
        <div class="arch-box worker" data-detail="training" onclick="showArchDetail('training')">
            <div class="box-name">Training Worker</div>
            <div class="box-detail">GPU · Anomalib · 18 models</div>
            <div class="box-hint">Click for detail</div>
        </div>
    </div>
    <div style="text-align:center;color:var(--text-dim);font-size:18px;margin:-8px 0">↓ SQLAlchemy · S3 SDK · Streams ↓</div>
    <div class="arch-layer-label" style="margin-top:12px">Storage Layer</div>
    <div class="arch-layer">
        <div class="arch-box storage" data-detail="postgres" onclick="showArchDetail('postgres')">
            <div class="box-name">PostgreSQL</div>
            <div class="box-detail">:5432 · ${D.models.length} tables · Events + Data</div>
            <div class="box-hint">Click for detail</div>
        </div>
        <div class="arch-box storage" data-detail="minio" onclick="showArchDetail('minio')">
            <div class="box-name">MinIO S3</div>
            <div class="box-detail">:9000 · Images · Models · Artifacts</div>
            <div class="box-hint">Click for detail</div>
        </div>
        <div class="arch-box storage" data-detail="valkey" onclick="showArchDetail('valkey')">
            <div class="box-name">Valkey</div>
            <div class="box-detail">:6379 · Event Streams · Cache</div>
            <div class="box-hint">Click for detail</div>
        </div>
    </div>
`;

// Architecture detail panel logic
let currentArchDetail = null;
function showArchDetail(which) {
    const container = document.getElementById('arch-detail-container');
    // Toggle off if same
    if (currentArchDetail === which) {
        container.innerHTML = '';
        currentArchDetail = null;
        document.querySelectorAll('.arch-box.active').forEach(b => b.classList.remove('active'));
        return;
    }
    currentArchDetail = which;
    document.querySelectorAll('.arch-box.active').forEach(b => b.classList.remove('active'));
    const box = document.querySelector(`.arch-box[data-detail="${which}"]`);
    if (box) box.classList.add('active');

    const details = buildArchDetail(which);
    container.innerHTML = `<div class="arch-detail">
        <div class="arch-detail-header">
            <h4>${details.title}</h4>
            <button class="arch-detail-close" onclick="showArchDetail('${which}')">&times;</button>
        </div>
        ${details.tabs ? `<div class="arch-detail-tabs" id="arch-dtabs">${details.tabs}</div>` : ''}
        <div class="arch-detail-body">${details.body}</div>
    </div>`;

    // Wire up sub-tabs if present
    container.querySelectorAll('.arch-detail-tabs button').forEach(btn => {
        btn.addEventListener('click', () => {
            container.querySelectorAll('.arch-detail-tabs button').forEach(b => b.classList.remove('active'));
            container.querySelectorAll('.arch-detail-section').forEach(s => s.classList.remove('active'));
            btn.classList.add('active');
            const sec = container.querySelector('#arch-sec-' + btn.dataset.sec);
            if (sec) sec.classList.add('active');
        });
    });

    container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function buildArchDetail(which) {
    switch(which) {
    case 'frontend': {
        let statsH = `<div class="arch-mini-stat"><span class="num">${D.pages.length}</span><span class="lbl">Pages</span></div>`;
        statsH += `<div class="arch-mini-stat"><span class="num">${D.routes.length}</span><span class="lbl">Routes</span></div>`;
        const totalFELines = D.pages.reduce((s,p) => s + p.lines, 0);
        statsH += `<div class="arch-mini-stat"><span class="num">${totalFELines.toLocaleString()}</span><span class="lbl">Lines</span></div>`;

        let pagesT = '<table><thead><tr><th>Page</th><th>Lines</th><th>API Calls</th><th>Ant Design Components</th></tr></thead><tbody>';
        D.pages.forEach(p => {
            pagesT += `<tr><td><strong>${p.name}</strong></td><td>${p.lines}</td><td>${p.api_calls.length}</td><td style="font-size:10px">${p.ant_design.join(', ') || '—'}</td></tr>`;
        });
        pagesT += '</tbody></table>';

        let routesT = '<table><thead><tr><th>Path</th><th>Component</th></tr></thead><tbody>';
        D.routes.forEach(r => { routesT += `<tr><td><code>${r.path}</code></td><td>${r.component}</td></tr>`; });
        routesT += '</tbody></table>';

        return {
            title: 'React Frontend — :5173',
            tabs: '<button class="active" data-sec="pages">Pages</button><button data-sec="routes">Routes</button>',
            body: statsH +
                `<div class="arch-detail-section active" id="arch-sec-pages">${pagesT}</div>` +
                `<div class="arch-detail-section" id="arch-sec-routes">${routesT}</div>`
        };
    }
    case 'backend': {
        let statsH = `<div class="arch-mini-stat"><span class="num">${D.endpoints.length}</span><span class="lbl">Endpoints</span></div>`;
        statsH += `<div class="arch-mini-stat"><span class="num">${numRouters}</span><span class="lbl">Routers</span></div>`;
        const methods = {};
        D.endpoints.forEach(e => { methods[e.method] = (methods[e.method]||0)+1; });
        Object.entries(methods).forEach(([m,c]) => { statsH += `<div class="arch-mini-stat"><span class="num">${c}</span><span class="lbl">${m}</span></div>`; });

        // Group endpoints by module
        let tabs = '';
        let sections = '';
        const modules = [...new Set(D.endpoints.map(e => e.module))];
        modules.forEach((mod, i) => {
            const modEPs = D.endpoints.filter(e => e.module === mod);
            tabs += `<button class="${i===0?'active':''}" data-sec="mod_${mod}">${mod} (${modEPs.length})</button>`;
            let t = '<table><thead><tr><th>Method</th><th>Path</th><th>Handler</th></tr></thead><tbody>';
            modEPs.forEach(e => { t += `<tr><td><span class="method method-${e.method}">${e.method}</span></td><td><code>${e.path}</code></td><td>${e.handler}</td></tr>`; });
            t += '</tbody></table>';
            sections += `<div class="arch-detail-section ${i===0?'active':''}" id="arch-sec-mod_${mod}">${t}</div>`;
        });

        return { title: 'FastAPI Backend — :8001', tabs, body: statsH + sections };
    }
    case 'training': {
        // Show training-related endpoints + event info
        const trainEPs = D.endpoints.filter(e => e.module === 'training');
        let statsH = `<div class="arch-mini-stat"><span class="num">${trainEPs.length}</span><span class="lbl">API Endpoints</span></div>`;
        const trainEvents = D.event_types.filter(e => e.type.includes('Training') || e.type.includes('Exploratory') || e.type.includes('HPO'));
        statsH += `<div class="arch-mini-stat"><span class="num">${trainEvents.length}</span><span class="lbl">Event Types</span></div>`;

        let epsT = '<table><thead><tr><th>Method</th><th>Path</th><th>Handler</th></tr></thead><tbody>';
        trainEPs.forEach(e => { epsT += `<tr><td><span class="method method-${e.method}">${e.method}</span></td><td><code>${e.path}</code></td><td>${e.handler}</td></tr>`; });
        epsT += '</tbody></table>';

        let evtT = '<table><thead><tr><th>Event Type</th><th>Audit Action</th></tr></thead><tbody>';
        trainEvents.forEach(e => { evtT += `<tr><td><strong>${e.type}</strong></td><td><code>${e.action}</code></td></tr>`; });
        evtT += '</tbody></table>';

        let pipelineH = '<div style="margin:12px 0;font-size:12px;color:var(--text-dim)">';
        pipelineH += '<strong>Pipeline:</strong> API creates QUEUED job → Worker polls DB → Loads images from MinIO → Runs Anomalib (GPU) → Saves model to MinIO → Updates DB → Publishes events to Valkey';
        pipelineH += '</div>';

        return {
            title: 'Training Worker — GPU',
            tabs: '<button class="active" data-sec="teps">Endpoints</button><button data-sec="tevts">Events</button>',
            body: statsH + pipelineH +
                `<div class="arch-detail-section active" id="arch-sec-teps">${epsT}</div>` +
                `<div class="arch-detail-section" id="arch-sec-tevts">${evtT}</div>`
        };
    }
    case 'postgres': {
        let statsH = `<div class="arch-mini-stat"><span class="num">${D.models.length}</span><span class="lbl">Tables</span></div>`;
        const totalCols = D.models.reduce((s,m) => s + m.columns, 0);
        statsH += `<div class="arch-mini-stat"><span class="num">${totalCols}</span><span class="lbl">Columns</span></div>`;

        let t = '<table><thead><tr><th>Model</th><th>Table</th><th>Columns</th><th>Role</th></tr></thead><tbody>';
        const roles = {
            Event: 'Hash-chained event store (21 CFR Part 11)',
            AuditRecord: 'Image capture records with file_hash',
            User: 'Authentication & RBAC',
            SystemAuditLog: 'Legacy audit projection',
            PCCPReport: 'Process capability validation',
            LabeledImage: 'Labeling annotations & splits',
            DefectAnnotation: 'Per-defect spatial annotations',
            Project: 'Inspection project configuration',
            TrainingJob: 'Training queue & metrics (chain hashes)',
            DeploymentPackage: 'Model deployment packages',
            CalibrationRecord: 'Lighting calibration references',
            Setting: 'Application settings',
        };
        D.models.forEach(m => {
            t += `<tr><td><strong>${m.name}</strong></td><td><code>${m.table}</code></td><td>${m.columns}</td><td style="font-size:11px;color:var(--text-dim)">${roles[m.name]||''}</td></tr>`;
        });
        t += '</tbody></table>';

        return { title: 'PostgreSQL — :5432', tabs: '', body: statsH + t };
    }
    case 'minio': {
        // Show which operations touch MinIO
        const minioPages = D.frontend_api_calls.filter(c => c.url.includes('capture') || c.url.includes('image') || c.url.includes('mask') || c.url.includes('model'));
        let body = '<div style="margin-bottom:16px">';
        body += '<div class="arch-mini-stat"><span class="num">3</span><span class="lbl">Bucket Types</span></div>';
        body += '</div>';
        body += '<table><thead><tr><th>Bucket Pattern</th><th>Contents</th><th>Written By</th><th>Read By</th></tr></thead><tbody>';
        body += '<tr><td><code>{project_id}/</code></td><td>Captured images (JPEG/PNG)</td><td>Capture route</td><td>Labeling, Inference, Training</td></tr>';
        body += '<tr><td><code>{project_id}/models/</code></td><td>Trained model weights (.pt, .onnx)</td><td>Training Worker</td><td>Inference, Deployment</td></tr>';
        body += '<tr><td><code>{project_id}/masks/</code></td><td>Segmentation masks</td><td>Labeling (SAM2)</td><td>Training, Validation</td></tr>';
        body += '</tbody></table>';
        body += '<div style="margin-top:12px;font-size:11px;color:var(--text-dim)"><strong>Access:</strong> S3 SDK via <code>minio_client.py</code> · Console at :9001</div>';
        return { title: 'MinIO S3 — :9000', tabs: '', body };
    }
    case 'valkey': {
        const eventSrcCount = Object.keys(D.event_sources).length;
        const totalEvtTypes = D.event_types.length;
        let body = '<div style="margin-bottom:16px">';
        body += `<div class="arch-mini-stat"><span class="num">${totalEvtTypes}</span><span class="lbl">Event Types</span></div>`;
        body += `<div class="arch-mini-stat"><span class="num">${eventSrcCount}</span><span class="lbl">Source Routes</span></div>`;
        body += '</div>';
        body += '<table><thead><tr><th>Stream</th><th>Purpose</th><th>Publishers</th></tr></thead><tbody>';
        body += `<tr><td><code>optoz:events</code></td><td>All domain events (XADD)</td><td>${eventSrcCount} routes + Training Worker</td></tr>`;
        body += '</tbody></table>';
        body += '<div style="margin-top:12px;font-size:11px;color:var(--text-dim)">';
        body += '<strong>Triple-write sink #3:</strong> Every <code>publish_event()</code> call adds to this stream for real-time frontend polling.<br>';
        body += '<strong>Client:</strong> <code>valkey_client.py</code> (Redis-compatible)';
        body += '</div>';
        body += '<div style="margin-top:12px"><strong style="font-size:11px">Event sources:</strong><br>';
        Object.entries(D.event_sources).forEach(([mod, evts]) => {
            body += `<span class="event-tag">${mod}</span> `;
        });
        body += '</div>';
        return { title: 'Valkey — :6379', tabs: '', body };
    }
    default:
        return { title: which, tabs: '', body: '<p>No detail available</p>' };
    }
}

// --- Data Pipeline Diagram ---
const pipelines = [
    {
        label: 'Image Capture',
        nodes: [
            {text: 'Camera / Upload', style: ''},
            {text: 'POST /capture', style: 'border-color:var(--green)'},
            {text: 'MinIO (store)', style: 'border-color:var(--purple)'},
            {text: 'PostgreSQL (audit)', style: 'border-color:var(--purple)'},
            {text: 'Valkey (event)', style: 'border-color:var(--accent2)'},
        ]
    },
    {
        label: 'Training',
        nodes: [
            {text: 'Configure Model', style: ''},
            {text: 'POST /training', style: 'border-color:var(--green)'},
            {text: 'DB (queue job)', style: 'border-color:var(--purple)'},
            {text: 'Worker (GPU)', style: 'border-color:var(--orange)'},
            {text: 'MinIO (model)', style: 'border-color:var(--purple)'},
        ]
    },
    {
        label: 'Inference',
        nodes: [
            {text: 'Select Image', style: ''},
            {text: 'POST /inference', style: 'border-color:var(--green)'},
            {text: 'Load Model', style: 'border-color:var(--purple)'},
            {text: 'Anomalib Run', style: 'border-color:var(--orange)'},
            {text: 'Score + Heatmap', style: 'highlight'},
        ]
    },
    {
        label: 'Audit Event',
        nodes: [
            {text: 'Any Action', style: ''},
            {text: 'publish_event()', style: 'border-color:var(--orange)'},
            {text: 'Event Store (hash)', style: 'border-color:var(--green)'},
            {text: 'SystemAuditLog', style: 'border-color:var(--purple)'},
            {text: 'Valkey Stream', style: 'border-color:var(--accent2)'},
        ]
    },
];
let pipeHTML = '<div class="pipeline">';
pipelines.forEach(p => {
    pipeHTML += '<div class="pipe-row">';
    pipeHTML += `<div class="pipe-label">${p.label}</div>`;
    p.nodes.forEach((n, i) => {
        if (i > 0) pipeHTML += '<div class="pipe-arrow">→</div>';
        const cls = n.style === 'highlight' ? 'pipe-node highlight' : 'pipe-node';
        const inlineStyle = n.style && n.style !== 'highlight' ? `style="${n.style}"` : '';
        pipeHTML += `<div class="${cls}" ${inlineStyle}>${n.text}</div>`;
    });
    pipeHTML += '</div>';
});
pipeHTML += '</div>';
document.getElementById('data-pipeline').innerHTML = pipeHTML;

// --- Triple Write Diagram ---
document.getElementById('triple-write-diagram').innerHTML = `
    <div class="triple-write">
        <div class="tw-source">publish_event(db, event_type, aggregate, payload)</div>
        <div class="tw-line-v"></div>
        <div class="tw-sink-group">
            <div class="tw-sink-col">
                <div class="tw-line-v"></div>
                <div class="tw-step-num">① Hash Chain</div>
                <div class="tw-sink primary">
                    <div class="sink-name">Event Store</div>
                    <div class="sink-table">events</div>
                    <div class="sink-desc">Immutable, append-only<br>SHA256 integrity chain<br>Primary source of truth</div>
                </div>
            </div>
            <div class="tw-sink-col">
                <div class="tw-line-v"></div>
                <div class="tw-step-num">② Projection</div>
                <div class="tw-sink secondary">
                    <div class="sink-name">SystemAuditLog</div>
                    <div class="sink-table">system_audit_logs</div>
                    <div class="sink-desc">Legacy-compatible view<br>Queryable by action type<br>Backward compatibility</div>
                </div>
            </div>
            <div class="tw-sink-col">
                <div class="tw-line-v"></div>
                <div class="tw-step-num">③ Real-time</div>
                <div class="tw-sink stream">
                    <div class="sink-name">Valkey Stream</div>
                    <div class="sink-table">optoz:events</div>
                    <div class="sink-desc">XADD pub/sub<br>Live notifications<br>Frontend polling</div>
                </div>
            </div>
        </div>
    </div>
`;

// --- Provenance Diagram ---
if (D.provenance && D.provenance.stages && D.provenance.stages.length) {
    const prov = D.provenance;
    let provHTML = '<div class="prov-timeline">';

    prov.stages.forEach((stage, i) => {
        // Skip stages with no hash activity
        const hasActivity = stage.hash_computations.length || stage.hash_fields_stored.length ||
            stage.events_published.some(e => e.hash_fields.length) || stage.hash_reads.length || stage.chain_hashes.length;
        if (!hasActivity) return;

        if (i > 0) {
            // Find flow arrows to this stage
            const incoming = prov.chain_flow.filter(f => f.to_stage === stage.id);
            const labels = incoming.map(f => f.via);
            provHTML += '<div class="prov-arrow-col">';
            if (labels.length) {
                labels.forEach(l => { provHTML += `<div class="prov-arrow-label">${l}</div>`; });
            }
            provHTML += '<div style="font-size:20px;color:var(--text-dim)">→</div>';
            provHTML += '</div>';
        }

        // Stage card
        const stageColors = {
            capture: '#4FC3F7', labeling: '#81C784', training_api: '#FFB74D',
            training_worker: '#FF8A65', training_hpo: '#CE93D8',
            inference: '#6c8cff', event_system: '#f48fb1', event_store: '#90a4ae', models: '#a1887f'
        };
        const borderColor = stageColors[stage.id] || 'var(--border)';

        provHTML += '<div class="prov-stage">';
        provHTML += `<div class="prov-stage-header" style="border-color:${borderColor}">`;
        provHTML += `<div class="prov-title">${stage.label}</div>`;
        provHTML += `<div class="prov-file">${stage.file}</div>`;
        provHTML += '</div>';
        provHTML += `<div class="prov-stage-body" style="border-color:${borderColor}33">`;

        // Hash computations
        if (stage.hash_computations.length) {
            provHTML += '<div class="prov-section"><div class="prov-section-label compute">Computes</div>';
            stage.hash_computations.forEach(h => { provHTML += `<div class="prov-item">${h}</div>`; });
            provHTML += '</div>';
        }

        // Hash reads
        if (stage.hash_reads.length) {
            provHTML += '<div class="prov-section"><div class="prov-section-label read">Reads</div>';
            stage.hash_reads.forEach(h => { provHTML += `<div class="prov-item">${h}</div>`; });
            provHTML += '</div>';
        }

        // Stored
        if (stage.hash_fields_stored.length) {
            provHTML += '<div class="prov-section"><div class="prov-section-label store">Stores</div>';
            stage.hash_fields_stored.forEach(h => { provHTML += `<div class="prov-item">${h}</div>`; });
            provHTML += '</div>';
        }

        // Chain hashes
        if (stage.chain_hashes.length) {
            provHTML += '<div class="prov-section"><div class="prov-section-label chain">Chain Hash</div>';
            stage.chain_hashes.forEach(h => { provHTML += `<div class="prov-item">${h}</div>`; });
            provHTML += '</div>';
        }

        // Events with hash fields
        const hashEvents = stage.events_published.filter(e => e.hash_fields.length);
        if (hashEvents.length) {
            provHTML += '<div class="prov-section"><div class="prov-section-label event">Events (hash fields)</div>';
            hashEvents.forEach(e => {
                provHTML += `<div class="prov-event-badge">${e.event_type}</div> `;
                e.hash_fields.forEach(hf => { provHTML += `<span class="prov-hash-badge present">${hf}</span>`; });
                provHTML += '<br>';
            });
            provHTML += '</div>';
        }

        // Events WITHOUT hash fields (show the gap)
        const noHashEvents = stage.events_published.filter(e => e.payload_fields.length && !e.hash_fields.length);
        if (noHashEvents.length) {
            provHTML += '<div class="prov-section"><div class="prov-section-label event">Events (no hash)</div>';
            noHashEvents.forEach(e => {
                provHTML += `<div class="prov-event-badge" style="opacity:0.5">${e.event_type}</div> `;
                provHTML += `<span class="prov-hash-badge absent">no hash</span><br>`;
            });
            provHTML += '</div>';
        }

        if (!stage.hash_computations.length && !stage.hash_reads.length && !stage.hash_fields_stored.length && !stage.chain_hashes.length && !hashEvents.length) {
            provHTML += '<div class="prov-item missing">No hash operations detected</div>';
        }

        provHTML += '</div></div>';
    });

    provHTML += '</div>';

    // Flow arrows summary
    if (prov.chain_flow.length) {
        provHTML += '<div class="prov-flow-arrows">';
        provHTML += '<span style="font-size:11px;color:var(--text-dim);margin-right:8px;font-weight:600">Hash flows:</span>';
        prov.chain_flow.forEach(f => {
            const fromLabel = prov.stages.find(s => s.id === f.from_stage)?.label || f.from_stage;
            const toLabel = prov.stages.find(s => s.id === f.to_stage)?.label || f.to_stage;
            provHTML += `<div class="prov-flow-chip"><span class="from">${fromLabel}</span> → <span class="to">${toLabel}</span> <span class="via">${f.via}</span></div>`;
        });
        provHTML += '</div>';
    }

    // Model hash columns
    if (prov.model_hash_columns.length) {
        provHTML += '<div style="margin-top:12px;font-size:11px;color:var(--text-dim)"><strong>DB columns storing hashes:</strong> ';
        provHTML += prov.model_hash_columns.map(c => `<code>${c.model}.${c.column}</code>`).join(', ');
        provHTML += '</div>';
    }

    document.getElementById('provenance-diagram').innerHTML = provHTML;
} else {
    document.getElementById('provenance-diagram').innerHTML = '<p style="color:var(--text-dim)">No hash provenance data detected in codebase.</p>';
}

// Treemap
function renderTreemap(containerId, data) {
    const el = document.getElementById(containerId);
    const entries = Object.entries(data).sort((a,b) => b[1] - a[1]);
    const total = entries.reduce((s,e) => s + e[1], 0);
    if (!total) { el.innerHTML = '<p style="color:var(--text-dim)">No data</p>'; return; }
    const W = el.clientWidth || 600;
    const H = 250;
    el.style.height = H + 'px';

    let cells = entries.map(([name, value], i) => ({name, value, color: getColor(i)}));
    // Simple squarified-ish layout
    let x = 0, y = 0, rowH = H;
    const rects = [];
    let remaining = [...cells];
    let remTotal = total;

    while (remaining.length > 0) {
        const isHoriz = (W - x) >= rowH;
        const availW = W - x;
        const availH = H - y;
        // Take items for this row
        let rowItems = [];
        let rowSum = 0;
        const stripSize = isHoriz ? availH : availW;

        for (let item of remaining) {
            rowItems.push(item);
            rowSum += item.value;
            const stripLen = (rowSum / remTotal) * (isHoriz ? availW : availH);
            if (stripLen >= stripSize * 0.4 || rowItems.length >= remaining.length) break;
        }

        remaining = remaining.slice(rowItems.length);
        const stripFrac = rowSum / remTotal;

        if (isHoriz) {
            const stripW = stripFrac * availW;
            let cy = y;
            for (let item of rowItems) {
                const cellH = (item.value / rowSum) * availH;
                rects.push({...item, x, y: cy, w: stripW, h: cellH});
                cy += cellH;
            }
            x += stripW;
        } else {
            const stripH = stripFrac * availH;
            let cx = x;
            for (let item of rowItems) {
                const cellW = (item.value / rowSum) * availW;
                rects.push({...item, x: cx, y, w: cellW, h: stripH});
                cx += cellW;
            }
            y += stripH;
        }
        remTotal -= rowSum;
    }

    el.innerHTML = rects.map(r => {
        const show = r.w > 50 && r.h > 25;
        const label = show ? `${r.name}<br>${r.value.toLocaleString()}` : '';
        return `<div class="treemap-cell" style="left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;background:${r.color}" title="${r.name}: ${r.value.toLocaleString()} lines">${label}</div>`;
    }).join('');
}
renderTreemap('treemap', D.domain_lines);

// Domain files chart
new Chart(document.getElementById('chart-domain-files'), {
    type: 'bar',
    data: {
        labels: Object.keys(D.domain_files),
        datasets: [{
            data: Object.values(D.domain_files),
            backgroundColor: Object.keys(D.domain_files).map((_,i) => getColor(i)),
            borderRadius: 4,
        }]
    },
    options: {
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
            x: { grid: { color: '#2e3347' }, ticks: { color: '#8b8fa3' } },
            y: { grid: { display: false }, ticks: { color: '#e1e4ed', font: { size: 11 } } }
        }
    }
});

// Git log
document.getElementById('git-log').innerHTML = D.git_log.map(line => {
    const hash = line.substring(0, 7);
    const msg = line.substring(8);
    return `<span class="hash">${hash}</span> <span class="msg">${msg}</span>`;
}).join('<br>') || '<span style="color:var(--text-dim)">No git history</span>';

// --- Architecture ---
let modelsHTML = '<thead><tr><th>Model</th><th>Table</th><th>Columns</th></tr></thead><tbody>';
D.models.forEach(m => { modelsHTML += `<tr><td><strong>${m.name}</strong></td><td><code>${m.table}</code></td><td>${m.columns}</td></tr>`; });
modelsHTML += '</tbody>';
document.getElementById('models-table').innerHTML = modelsHTML;

let svcHTML = '<thead><tr><th>Service</th><th>Image</th><th>Ports</th></tr></thead><tbody>';
D.services.forEach(s => { svcHTML += `<tr><td><strong>${s.name}</strong></td><td>${s.image||'build'}</td><td>${s.ports.join(', ')||'—'}</td></tr>`; });
svcHTML += '</tbody>';
document.getElementById('services-table').innerHTML = svcHTML;

let epHTML = '<thead><tr><th>Module</th><th>Method</th><th>Path</th><th>Handler</th></tr></thead><tbody>';
D.endpoints.forEach(e => { epHTML += `<tr><td>${e.module}</td><td><span class="method method-${e.method}">${e.method}</span></td><td><code>${e.path}</code></td><td>${e.handler}</td></tr>`; });
epHTML += '</tbody>';
document.getElementById('endpoints-table').innerHTML = epHTML;

// --- Dataflow: Coverage Matrix ---
const cm = D.coverage_matrix;
if (cm.pages.length && cm.modules.length) {
    const grid = document.getElementById('coverage-matrix');
    const cols = cm.modules.length + 1;
    let html = `<div class="coverage-grid" style="grid-template-columns: 120px repeat(${cm.modules.length}, 1fr)">`;
    html += '<div></div>';
    cm.modules.forEach(m => { html += `<div class="coverage-header">${m}</div>`; });
    cm.pages.forEach(page => {
        html += `<div class="coverage-label">${page}</div>`;
        cm.modules.forEach(mod => {
            const key = page + '|' + mod;
            const hits = cm.hits[key] || 0;
            const bg = hits > 0 ? `rgba(129,199,132,${Math.min(0.3 + hits * 0.15, 1)})` : 'var(--surface2)';
            html += `<div class="coverage-cell" style="background:${bg}" title="${page} → ${mod}: ${hits} call(s)">${hits || ''}</div>`;
        });
    });
    html += '</div>';
    grid.innerHTML = html;
}

let apiHTML = '<thead><tr><th>Page</th><th>Method</th><th>URL</th></tr></thead><tbody>';
D.frontend_api_calls.forEach(c => { apiHTML += `<tr><td>${c.page}</td><td><span class="method method-${c.method}">${c.method}</span></td><td><code>${c.url}</code></td></tr>`; });
apiHTML += '</tbody>';
document.getElementById('api-calls-table').innerHTML = apiHTML;

// --- Audit ---
const eventCategories = {};
D.event_types.forEach(e => {
    const cat = e.type.replace(/([A-Z])/g, ' $1').trim().split(' ')[0];
    eventCategories[cat] = (eventCategories[cat] || 0) + 1;
});

document.getElementById('audit-stats').innerHTML = [
    {v: D.event_types.length, l: 'Event Types'},
    {v: Object.keys(D.event_sources).length, l: 'Route Sources'},
    {v: Object.keys(eventCategories).length, l: 'Event Categories'},
].map(s => `<div class="stat"><div class="value">${s.v}</div><div class="label">${s.l}</div></div>`).join('');

new Chart(document.getElementById('chart-event-categories'), {
    type: 'doughnut',
    data: {
        labels: Object.keys(eventCategories),
        datasets: [{
            data: Object.values(eventCategories),
            backgroundColor: Object.keys(eventCategories).map((_,i) => getColor(i)),
            borderWidth: 0,
        }]
    },
    options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'right', labels: { color: '#e1e4ed', font: { size: 11 } } } }
    }
});

const srcLabels = Object.keys(D.event_sources);
const srcData = srcLabels.map(k => D.event_sources[k].length);
new Chart(document.getElementById('chart-event-sources'), {
    type: 'bar',
    data: {
        labels: srcLabels,
        datasets: [{
            data: srcData,
            backgroundColor: srcLabels.map((_,i) => getColor(i)),
            borderRadius: 4,
        }]
    },
    options: {
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
            x: { grid: { color: '#2e3347' }, ticks: { color: '#8b8fa3' } },
            y: { grid: { display: false }, ticks: { color: '#e1e4ed', font: { size: 11 } } }
        }
    }
});

let evtHTML = '<thead><tr><th>Event Type</th><th>Audit Action</th><th>Sources</th></tr></thead><tbody>';
D.event_types.forEach(e => {
    const sources = [];
    Object.entries(D.event_sources).forEach(([mod, evts]) => { if (evts.includes(e.type)) sources.push(mod); });
    evtHTML += `<tr><td><strong>${e.type}</strong></td><td><code>${e.action}</code></td><td>${sources.map(s=>`<span class="event-tag">${s}</span>`).join(' ')||'<span style="color:var(--text-dim)">—</span>'}</td></tr>`;
});
evtHTML += '</tbody>';
document.getElementById('events-table').innerHTML = evtHTML;

// --- Frontend ---
new Chart(document.getElementById('chart-page-sizes'), {
    type: 'bar',
    data: {
        labels: D.pages.map(p => p.name),
        datasets: [{
            label: 'Lines',
            data: D.pages.map(p => p.lines),
            backgroundColor: D.pages.map((_,i) => getColor(i)),
            borderRadius: 4,
        }]
    },
    options: {
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
            x: { grid: { color: '#2e3347' }, ticks: { color: '#8b8fa3' } },
            y: { grid: { display: false }, ticks: { color: '#e1e4ed', font: { size: 11 } } }
        }
    }
});

// Ant Design component frequency
const antFreq = {};
D.pages.forEach(p => p.ant_design.forEach(c => { antFreq[c] = (antFreq[c] || 0) + 1; }));
const antSorted = Object.entries(antFreq).sort((a,b) => b[1] - a[1]).slice(0, 15);
new Chart(document.getElementById('chart-ant-design'), {
    type: 'bar',
    data: {
        labels: antSorted.map(e => e[0]),
        datasets: [{
            data: antSorted.map(e => e[1]),
            backgroundColor: antSorted.map((_,i) => getColor(i)),
            borderRadius: 4,
        }]
    },
    options: {
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
            x: { title: { display: true, text: 'Pages using component', color: '#8b8fa3' }, grid: { color: '#2e3347' }, ticks: { color: '#8b8fa3' } },
            y: { grid: { display: false }, ticks: { color: '#e1e4ed', font: { size: 11 } } }
        }
    }
});

let routeHTML = '<thead><tr><th>Path</th><th>Component</th></tr></thead><tbody>';
D.routes.forEach(r => { routeHTML += `<tr><td><code>${r.path}</code></td><td><strong>${r.component}</strong></td></tr>`; });
routeHTML += '</tbody>';
document.getElementById('routes-table').innerHTML = routeHTML;

// --- Changes ---
if (D.change_history.length) {
    const allDomains = new Set();
    D.change_history.forEach(ch => Object.keys(ch.domains).forEach(d => allDomains.add(d)));
    const domains = [...allDomains].sort();
    let hmHTML = '<div class="heatmap-grid" style="grid-template-columns: 140px repeat(' + D.change_history.length + ', 1fr)">';
    domains.forEach(domain => {
        hmHTML += `<div style="font-size:11px;color:var(--text-dim);padding:4px 8px 4px 0;text-align:right">${domain}</div>`;
        D.change_history.forEach(ch => {
            const count = ch.domains[domain] || 0;
            const intensity = count > 0 ? Math.min(0.3 + count * 0.1, 1) : 0.05;
            const bg = count > 0 ? `rgba(108,140,255,${intensity})` : 'var(--surface2)';
            hmHTML += `<div class="heatmap-cell" style="background:${bg}" title="${domain}: ${count} changes at ${ch.timestamp}">${count||''}</div>`;
        });
    });
    hmHTML += '</div>';
    document.getElementById('change-heatmap').innerHTML = hmHTML;
} else {
    document.getElementById('change-heatmap').innerHTML = '<p style="color:var(--text-dim);padding:12px">No changes recorded yet. The heatmap will populate as the watcher detects file changes.</p>';
}

// File sizes chart
const fileSizeEntries = Object.entries(D.file_sizes).slice(0, 25);
new Chart(document.getElementById('chart-file-sizes'), {
    type: 'bar',
    data: {
        labels: fileSizeEntries.map(e => e[0].split('/').pop()),
        datasets: [{
            label: 'Lines',
            data: fileSizeEntries.map(e => e[1]),
            backgroundColor: fileSizeEntries.map((_,i) => getColor(i)),
            borderRadius: 4,
        }]
    },
    options: {
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: {
            legend: { display: false },
            tooltip: { callbacks: { title: (items) => fileSizeEntries[items[0].dataIndex][0] } }
        },
        scales: {
            x: { grid: { color: '#2e3347' }, ticks: { color: '#8b8fa3' } },
            y: { grid: { display: false }, ticks: { color: '#e1e4ed', font: { size: 10 } } }
        }
    }
});

// --- Inject analysis annotations into all sections ---
injectAllAnalysis();
</script>
</body>
</html>"""
