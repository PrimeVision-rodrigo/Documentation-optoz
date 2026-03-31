"""
Dashboard Generator — Produces a self-contained interactive HTML dashboard.

Collects all parsed data from the project and renders it as a single-page
HTML file with embedded CSS and JavaScript. Works generically on any project.
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
from watcher.analyzers.manual_annotations import load_annotations
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
        return []

    def should_update(self, changed_files: set[str]) -> bool:
        return len(changed_files) > 0

    def initial_scan(self) -> str:
        return self._build()

    def update(self, changes: list[Change]) -> str | None:
        groups = ChangeTracker.group_by_domain(changes)
        entry = {
            "timestamp": md.timestamp(),
            "total": len(changes),
            "domains": {d: len(cs) for d, cs in groups.items()},
            "files": list(ChangeTracker.changed_files(changes)),
        }
        self._change_history.append(entry)
        return self._build()

    @property
    def _profile(self):
        return self.config.profile

    def _build(self) -> str:
        data = self._collect_data()
        return self._render_html(data)

    def _collect_data(self) -> dict:
        """Collect all project data into a single dict for the dashboard."""
        data = {}
        profile = self._profile

        # Project metadata
        data["project_name"] = self.config.project_name
        data["project_type"] = profile.project_type if profile else "unknown"
        data["frameworks"] = profile.frameworks if profile else []
        data["has_frontend"] = profile.has_frontend if profile else False
        data["has_backend"] = profile.has_backend if profile else False
        data["has_docker"] = profile.has_docker if profile else False
        data["has_event_system"] = profile.has_event_system if profile else False

        # 1. File statistics by domain
        classifier = FileClassifier(self.config.domain_rules)
        domain_lines = {}
        domain_files = {}
        file_sizes = {}

        for root, dirs, files in os.walk(self.project):
            dirs[:] = [d for d in dirs if d not in self.config.watch_excludes]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in (".py", ".tsx", ".ts", ".jsx", ".js", ".go", ".rs", ".java", ".rb",
                               ".md", ".yml", ".yaml", ".vue", ".svelte"):
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
        models_list = []
        if profile and profile.models_files:
            for models_rel in profile.models_files:
                models_path = self.project / models_rel
                if models_path.is_file():
                    models = parse_models_file(models_path)
                    for m in models:
                        models_list.append({
                            "name": m["name"], "table": m["tablename"],
                            "columns": len(m["columns"]),
                        })
        data["models"] = models_list

        # 3. API endpoints
        endpoints = []
        if profile and profile.routes_dirs:
            for route_dir_rel in profile.routes_dirs:
                route_dir = self.project / route_dir_rel
                if not route_dir.is_dir():
                    continue
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
        if profile and profile.docker_compose:
            compose_path = self.project / profile.docker_compose
            data["services"] = parse_docker_compose(compose_path) if compose_path.is_file() else []
        else:
            data["services"] = []

        # 5. Frontend pages and their API calls
        pages = []
        frontend_api_calls = []
        if profile and profile.frontend_pages_dir:
            pages_dir = self.project / profile.frontend_pages_dir
            if pages_dir.is_dir():
                for ext in ("*.tsx", "*.jsx", "*.vue"):
                    for pf in sorted(pages_dir.glob(ext)):
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

        # 6. Event types (search for event registries generically)
        event_types = []
        if profile and profile.has_event_system:
            event_types = self._find_event_types()
        data["event_types"] = event_types

        # 7. Event sources per route
        event_sources = {}
        if profile and profile.routes_dirs:
            for route_dir_rel in profile.routes_dirs:
                route_dir = self.project / route_dir_rel
                if not route_dir.is_dir():
                    continue
                for rf in sorted(route_dir.glob("*.py")):
                    if rf.name == "__init__.py":
                        continue
                    source = self._read_file(str(rf.relative_to(self.project)))
                    events = set()
                    for pattern in [r'publish_event\s*\([^,]*,\s*"(\w+)"',
                                    r'event_type\s*=\s*"(\w+)"',
                                    r'emit\s*\(\s*"(\w+)"']:
                        for match in re.finditer(pattern, source):
                            events.add(match.group(1))
                    if events:
                        event_sources[rf.stem] = sorted(events)
        data["event_sources"] = event_sources

        # 8. Frontend routes
        if profile and profile.frontend_entry:
            app_file = self.project / profile.frontend_entry
            if app_file.is_file():
                parsed = parse_app_tsx(app_file)
                data["routes"] = parsed.get("routes", [])
            else:
                data["routes"] = []
        else:
            data["routes"] = []

        # 9. API coverage matrix
        backend_modules = set(ep["module"] for ep in endpoints)
        page_names = [p["name"] for p in pages]
        coverage = {}
        for fc in frontend_api_calls:
            url = fc["url"]
            page = fc["page"]
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
                capture_output=True, text=True, timeout=10,
            )
            data["git_log"] = result.stdout.strip().splitlines() if result.returncode == 0 else []
        except (subprocess.TimeoutExpired, FileNotFoundError):
            data["git_log"] = []

        # 11. Change history
        data["change_history"] = self._change_history

        # 12. Hash provenance
        data["provenance"] = parse_provenance(self.project)

        # 13. Timestamp
        data["generated_at"] = md.timestamp()

        # 14. Code analysis (rule-based)
        analysis = analyze_project(self.config, data)
        data["analysis"] = analysis

        # 15. Manual annotations
        data["manual_annotations"] = load_annotations(self.config.output_path)

        # 16. Claude advisor
        claude_results = check_claude(self.config.output_path, analysis, data)
        data["claude_analysis"] = claude_results

        return data

    def _find_event_types(self) -> list[dict]:
        """Search for event type registries in the codebase."""
        event_types = []
        seen = set()

        # Search for dict-based event registries
        for root, dirs, files in os.walk(self.project):
            dirs[:] = [d for d in dirs if d not in self.config.watch_excludes]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    source = open(fpath, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue

                # Look for event type → action mappings
                for match in re.finditer(r'"(\w+)":\s*"(\w+)"', source):
                    evt_type = match.group(1)
                    evt_action = match.group(2)
                    # Heuristic: event types are PascalCase with common suffixes
                    if (any(evt_type.endswith(s) for s in ["Created", "Updated", "Deleted", "Completed", "Failed", "Started", "Stopped"]) and
                            evt_type not in seen):
                        seen.add(evt_type)
                        event_types.append({"type": evt_type, "action": evt_action})

        return event_types

    def _render_html(self, data: dict) -> str:
        data_json = json.dumps(data, indent=None, default=str)
        return HTML_TEMPLATE.replace("/*__DATA__*/null", data_json)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Documentation Dashboard</title>
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
body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; }
header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 16px 32px; display: flex; justify-content: space-between; align-items: center; }
header h1 { font-size: 20px; font-weight: 600; }
header h1 span { color: var(--accent); }
header .meta { color: var(--text-dim); font-size: 13px; }
nav { background: var(--surface); border-bottom: 1px solid var(--border); padding: 0 32px; display: flex; gap: 0; overflow-x: auto; }
nav button { background: none; border: none; color: var(--text-dim); padding: 12px 20px; cursor: pointer; font-size: 13px; font-weight: 500; border-bottom: 2px solid transparent; white-space: nowrap; transition: all 0.15s; }
nav button:hover { color: var(--text); }
nav button.active { color: var(--accent); border-bottom-color: var(--accent); }
main { padding: 24px 32px; max-width: 1400px; margin: 0 auto; }
.tab-content { display: none; }
.tab-content.active { display: block; }
.grid { display: grid; gap: 20px; }
.grid-2 { grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); }
.grid-3 { grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 20px; }
.card h3 { font-size: 14px; font-weight: 600; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 16px; }
.stat-row { display: flex; gap: 20px; margin-bottom: 24px; flex-wrap: wrap; }
.stat { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 20px; min-width: 160px; flex: 1; }
.stat .value { font-size: 32px; font-weight: 700; color: var(--accent); }
.stat .label { font-size: 12px; color: var(--text-dim); margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 10px 12px; color: var(--text-dim); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--border); }
td { padding: 8px 12px; border-bottom: 1px solid var(--border); }
tr:hover td { background: var(--surface2); }
.method { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; font-family: monospace; }
.method-GET { background: #1b3a2d; color: var(--green); }
.method-POST { background: #2d2a1b; color: var(--orange); }
.method-PUT { background: #1b2a3a; color: var(--accent2); }
.method-DELETE { background: #3a1b1b; color: var(--red); }
.method-PATCH { background: #2d1b3a; color: var(--purple); }
.heatmap-grid { display: grid; gap: 3px; margin-top: 12px; }
.heatmap-cell { border-radius: 3px; height: 32px; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 600; cursor: default; transition: transform 0.1s; }
.heatmap-cell:hover { transform: scale(1.05); z-index: 1; }
.coverage-grid { display: grid; gap: 2px; margin-top: 12px; overflow-x: auto; }
.coverage-cell { width: 100%; aspect-ratio: 1; border-radius: 3px; display: flex; align-items: center; justify-content: center; font-size: 9px; font-weight: 700; min-width: 28px; min-height: 28px; }
.coverage-header { font-size: 10px; color: var(--text-dim); writing-mode: vertical-rl; text-orientation: mixed; transform: rotate(180deg); padding: 4px 2px; max-height: 80px; overflow: hidden; text-overflow: ellipsis; }
.coverage-label { font-size: 11px; color: var(--text-dim); padding: 4px 8px 4px 0; text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 120px; }
.event-tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; margin: 2px; background: var(--surface2); border: 1px solid var(--border); }
.chart-container { position: relative; height: 300px; }
.badge { display: inline-block; padding: 1px 6px; border-radius: 8px; font-size: 11px; font-weight: 600; }
.git-log { font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 12px; line-height: 2; }
.git-log .hash { color: var(--accent); }
.git-log .msg { color: var(--text); }
.treemap-container { position: relative; min-height: 250px; }
.treemap-cell { position: absolute; border-radius: 4px; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 600; color: #000; overflow: hidden; text-align: center; padding: 4px; cursor: default; transition: opacity 0.15s; line-height: 1.2; }
.treemap-cell:hover { opacity: 0.85; }
.arch-box { background: var(--surface2); border: 2px solid var(--border); border-radius: 12px; padding: 14px 20px; text-align: center; min-width: 140px; transition: border-color 0.15s, transform 0.15s; cursor: default; display: inline-block; margin: 6px; }
.arch-box:hover { border-color: var(--accent); transform: translateY(-2px); }
.arch-box .box-name { font-size: 13px; font-weight: 700; }
.arch-box .box-detail { font-size: 10px; color: var(--text-dim); margin-top: 2px; }
.arch-layer { display: flex; justify-content: center; gap: 24px; margin-bottom: 24px; flex-wrap: wrap; }
.arch-layer-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); margin-bottom: 8px; text-align: center; }
.analysis-bar { display: flex; align-items: center; gap: 10px; padding: 12px 16px; border-radius: 8px; margin-bottom: 12px; font-size: 12px; cursor: pointer; transition: all 0.15s; border: 1px solid var(--border); border-left: 4px solid; }
.analysis-bar:hover { background: var(--surface2); }
.analysis-bar.complete { border-left-color: var(--green); background: rgba(129,199,132,0.06); }
.analysis-bar.partial { border-left-color: var(--orange); background: rgba(255,183,77,0.06); }
.analysis-bar.needs_work { border-left-color: var(--red); background: rgba(239,83,80,0.06); }
.analysis-bar.info { border-left-color: var(--accent); background: rgba(108,140,255,0.04); }
.analysis-status { padding: 2px 8px; border-radius: 6px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; flex-shrink: 0; }
.analysis-status.complete { background: var(--green); color: #000; }
.analysis-status.partial { background: var(--orange); color: #000; }
.analysis-status.needs_work { background: var(--red); color: #fff; }
.analysis-status.info { background: var(--accent); color: #fff; }
.analysis-progress { width: 60px; height: 6px; background: var(--surface); border-radius: 3px; overflow: hidden; flex-shrink: 0; }
.analysis-progress-fill { height: 100%; border-radius: 3px; transition: width 0.3s; }
.analysis-summary { flex: 1; color: var(--text); font-size: 12px; }
.analysis-toggle { color: var(--text-dim); font-size: 14px; flex-shrink: 0; transition: transform 0.2s; }
.analysis-toggle.open { transform: rotate(180deg); }
.analysis-detail { display: none; padding: 0 14px 12px; font-size: 11px; }
.analysis-detail.open { display: block; }
.analysis-detail .finding { padding: 3px 0; color: var(--text-dim); border-left: 2px solid var(--border); padding-left: 10px; margin: 3px 0; }
.analysis-detail .rec { padding: 4px 8px; background: rgba(108,140,255,0.08); border-left: 2px solid var(--accent); padding-left: 10px; margin: 3px 0; color: var(--text); }
.analysis-detail .claude-rec { padding: 4px 8px; background: rgba(206,147,216,0.08); border-left: 2px solid var(--purple); padding-left: 10px; margin: 3px 0; color: var(--text); }
.claude-label { font-size: 9px; font-weight: 700; color: var(--purple); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
.fw-tag { display: inline-block; padding: 3px 10px; border-radius: 6px; font-size: 11px; font-weight: 600; margin: 2px; background: var(--surface2); border: 1px solid var(--border); }
</style>
</head>
<body>
<header>
    <h1><span id="proj-name"></span> — Documentation Dashboard</h1>
    <div class="meta" id="timestamp"></div>
</header>
<nav id="tabs"></nav>
<main id="main-content"></main>

<script>
const D = /*__DATA__*/null;

// --- Dynamic tab setup ---
const tabs = [
    {id: 'overview', label: 'Overview', show: true},
    {id: 'architecture', label: 'Architecture', show: true},
    {id: 'dataflow', label: 'Data Flow', show: D.has_frontend || D.endpoints.length > 0},
    {id: 'audit', label: 'Events', show: D.has_event_system || D.event_types.length > 0},
    {id: 'frontend', label: 'Frontend', show: D.has_frontend},
    {id: 'changes', label: 'Change Log', show: true},
];

const navEl = document.getElementById('tabs');
tabs.filter(t => t.show).forEach((tab, i) => {
    const btn = document.createElement('button');
    btn.textContent = tab.label;
    btn.dataset.tab = tab.id;
    if (i === 0) btn.className = 'active';
    navEl.appendChild(btn);
});

// Build tab content containers
const mainEl = document.getElementById('main-content');
tabs.filter(t => t.show).forEach((tab, i) => {
    const div = document.createElement('div');
    div.className = 'tab-content' + (i === 0 ? ' active' : '');
    div.id = 'tab-' + tab.id;
    mainEl.appendChild(div);
});

// Tab switching
navEl.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', () => {
        navEl.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    });
});

document.getElementById('proj-name').textContent = D.project_name || 'Project';
document.getElementById('timestamp').textContent = 'Generated: ' + D.generated_at;

// --- Analysis ---
const A = D.analysis || {};
const MA = D.manual_annotations || {};
const CA = D.claude_analysis || {};

function renderAnalysis(sectionId, containerEl) {
    const a = A[sectionId];
    if (!a) return;
    const container = (typeof containerEl === 'string') ? document.getElementById(containerEl) : containerEl;
    if (!container) return;
    const manual = MA[sectionId];
    const claude = CA[sectionId];
    const statusColors = { complete: 'var(--green)', partial: 'var(--orange)', needs_work: 'var(--red)', info: 'var(--accent)' };
    const fillColor = statusColors[a.status] || 'var(--accent)';
    const uid = 'a_' + sectionId;
    let html = `<div class="analysis-bar ${a.status}" onclick="document.getElementById('${uid}').classList.toggle('open');this.querySelector('.analysis-toggle').classList.toggle('open')">`;
    html += `<span class="analysis-status ${a.status}">${a.status.replace('_',' ')}</span>`;
    html += `<div class="analysis-progress"><div class="analysis-progress-fill" style="width:${a.progress}%;background:${fillColor}"></div></div>`;
    html += `<span class="analysis-summary">${a.summary}</span>`;
    html += `<span class="analysis-toggle">\u25BC</span></div>`;
    html += `<div class="analysis-detail" id="${uid}">`;
    if (manual && manual.claude_summary) {
        html += `<div style="font-size:12px;color:var(--text);line-height:1.6;padding:8px 12px;background:rgba(206,147,216,0.06);border-radius:6px;margin-bottom:8px">${manual.claude_summary}</div>`;
    }
    if (manual && manual.recommendations) {
        manual.recommendations.forEach(r => { html += `<div class="claude-rec">${r}</div>`; });
    }
    if (a.findings && a.findings.length) {
        html += '<div style="font-size:10px;font-weight:700;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;margin:8px 0 4px">Findings</div>';
        a.findings.forEach(f => { html += `<div class="finding">${f}</div>`; });
    }
    if (a.recommendations && a.recommendations.length) {
        html += '<div style="font-size:10px;font-weight:700;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin:8px 0 4px">Recommendations</div>';
        a.recommendations.forEach(r => { html += `<div class="rec">${r}</div>`; });
    }
    if (claude) {
        html += '<div class="claude-label" style="margin-top:8px">Claude AI Analysis</div>';
        if (claude.claude_summary) html += `<div class="finding" style="border-color:var(--purple)">${claude.claude_summary}</div>`;
        if (claude.recommendations) claude.recommendations.forEach(r => { html += `<div class="claude-rec">${r}</div>`; });
    }
    html += '</div>';
    const wrapper = document.createElement('div');
    wrapper.innerHTML = html;
    const h3 = container.querySelector('h3');
    if (h3) { let insertAfter = h3; let next = h3.nextElementSibling; while (next && next.tagName === 'P') { insertAfter = next; next = next.nextElementSibling; } insertAfter.after(wrapper); }
    else container.prepend(wrapper);
}

const COLORS = ['#6c8cff','#4fc3f7','#81c784','#ffb74d','#ce93d8','#f48fb1','#ef5350','#90a4ae','#a1887f','#fff176','#4dd0e1','#aed581','#ff8a65','#ba68c8','#64b5f6','#dce775','#4db6ac','#e57373','#9575cd','#7986cb'];
function getColor(i) { return COLORS[i % COLORS.length]; }

// ===================== OVERVIEW TAB =====================
const overviewTab = document.getElementById('tab-overview');
if (overviewTab) {
    const totalLines = Object.values(D.domain_lines).reduce((a,b) => a+b, 0);
    const totalFiles = Object.values(D.domain_files).reduce((a,b) => a+b, 0);

    let statsItems = [
        {v: totalLines.toLocaleString(), l: 'Lines of Code'},
        {v: totalFiles, l: 'Source Files'},
    ];
    if (D.endpoints.length) statsItems.push({v: D.endpoints.length, l: 'API Endpoints'});
    if (D.models.length) statsItems.push({v: D.models.length, l: 'DB Models'});
    if (D.event_types.length) statsItems.push({v: D.event_types.length, l: 'Event Types'});
    if (D.pages.length) statsItems.push({v: D.pages.length, l: 'Frontend Pages'});

    let statsHTML = '<div class="stat-row">' + statsItems.map(s =>
        `<div class="stat"><div class="value">${s.v}</div><div class="label">${s.l}</div></div>`
    ).join('') + '</div>';

    // Project info card
    let infoHTML = '<div class="card" style="margin-bottom:20px"><h3>Project Info</h3>';
    infoHTML += `<p style="color:var(--text);font-size:14px;margin-bottom:8px"><strong>${D.project_name}</strong> &mdash; ${D.project_type} project</p>`;
    if (D.frameworks.length) {
        infoHTML += '<div style="margin-bottom:8px">' + D.frameworks.map(f => `<span class="fw-tag">${f}</span>`).join(' ') + '</div>';
    }
    infoHTML += '</div>';

    overviewTab.innerHTML = statsHTML + infoHTML +
        '<div class="grid grid-2">' +
        '<div class="card"><h3>Code Distribution (Lines by Domain)</h3><div class="treemap-container" id="treemap"></div></div>' +
        '<div class="card"><h3>File Count by Domain</h3><div class="chart-container"><canvas id="chart-domain-files"></canvas></div></div>' +
        '</div>' +
        '<div class="card" style="margin-top:20px"><h3>Git History</h3><div class="git-log" id="git-log"></div></div>';

    // Treemap
    renderTreemap('treemap', D.domain_lines);

    // Domain files chart
    new Chart(document.getElementById('chart-domain-files'), {
        type: 'bar',
        data: { labels: Object.keys(D.domain_files), datasets: [{ data: Object.values(D.domain_files), backgroundColor: Object.keys(D.domain_files).map((_,i) => getColor(i)), borderRadius: 4 }] },
        options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { color: '#2e3347' }, ticks: { color: '#8b8fa3' } }, y: { grid: { display: false }, ticks: { color: '#e1e4ed', font: { size: 11 } } } } }
    });

    // Git log
    document.getElementById('git-log').innerHTML = D.git_log.map(line => {
        const hash = line.substring(0, 7); const msg = line.substring(8);
        return `<span class="hash">${hash}</span> <span class="msg">${msg}</span>`;
    }).join('<br>') || '<span style="color:var(--text-dim)">No git history</span>';

    // Analysis
    renderAnalysis('code_distribution', document.querySelector('#tab-overview .treemap-container')?.closest('.card'));
    renderAnalysis('git_history', document.getElementById('git-log')?.closest('.card'));
    if (A.todos) {
        const todoCard = document.createElement('div');
        todoCard.className = 'card'; todoCard.style.marginTop = '20px';
        todoCard.innerHTML = '<h3>TODO / FIXME Comments</h3>';
        overviewTab.appendChild(todoCard);
        renderAnalysis('todos', todoCard);
    }
}

// ===================== ARCHITECTURE TAB =====================
const archTab = document.getElementById('tab-architecture');
if (archTab) {
    let html = '<div class="card" style="margin-bottom:20px"><h3>System Architecture</h3><div id="arch-diagram"></div></div>';
    html += '<div class="grid grid-2">';
    if (D.models.length) html += '<div class="card"><h3>Database Models</h3><table id="models-table"></table></div>';
    if (D.services.length) html += '<div class="card"><h3>Docker Services</h3><table id="services-table"></table></div>';
    html += '</div>';
    if (D.endpoints.length) html += '<div class="card" style="margin-top:20px"><h3>API Endpoints</h3><table id="endpoints-table"></table></div>';
    archTab.innerHTML = html;

    // Architecture diagram (dynamic)
    const archDiagram = document.getElementById('arch-diagram');
    let archHTML = '';

    if (D.has_frontend) {
        archHTML += '<div class="arch-layer-label">Client Layer</div><div class="arch-layer">';
        const feFW = D.frameworks.find(f => ['React','Vue','Angular','Svelte','Next.js'].includes(f)) || 'Frontend';
        archHTML += `<div class="arch-box" style="border-color:#4FC3F7"><div class="box-name">${feFW} Frontend</div><div class="box-detail">${D.pages.length} pages</div></div>`;
        archHTML += '</div>';
        if (D.has_backend) archHTML += '<div style="text-align:center;color:var(--text-dim);font-size:18px;margin:-8px 0">&darr; API &darr;</div>';
    }
    if (D.has_backend) {
        archHTML += '<div class="arch-layer-label" style="margin-top:12px">Application Layer</div><div class="arch-layer">';
        const beFW = D.frameworks.find(f => ['FastAPI','Django','Flask','Express','Gin'].includes(f)) || 'Backend';
        const numRouters = new Set(D.endpoints.map(e => e.module)).size;
        archHTML += `<div class="arch-box" style="border-color:#81C784"><div class="box-name">${beFW} Backend</div><div class="box-detail">${D.endpoints.length} endpoints &middot; ${numRouters} modules</div></div>`;
        archHTML += '</div>';
    }
    // Storage from docker services
    const storageSvcs = D.services.filter(s => {
        const img = (s.image||'').toLowerCase();
        return ['postgres','mysql','mongo','redis','valkey','minio','rabbit','kafka','elastic','mariadb'].some(db => img.includes(db));
    });
    if (storageSvcs.length) {
        if (D.has_backend) archHTML += '<div style="text-align:center;color:var(--text-dim);font-size:18px;margin:-8px 0">&darr; Storage &darr;</div>';
        archHTML += '<div class="arch-layer-label" style="margin-top:12px">Storage Layer</div><div class="arch-layer">';
        storageSvcs.forEach(s => {
            const port = s.ports.length ? ':' + s.ports[0].split(':').pop() : '';
            archHTML += `<div class="arch-box" style="border-color:#CE93D8"><div class="box-name">${s.name}</div><div class="box-detail">${port} ${s.image||'build'}</div></div>`;
        });
        archHTML += '</div>';
    }
    archDiagram.innerHTML = archHTML || '<p style="color:var(--text-dim)">No architecture components detected.</p>';

    // Models table
    const modelsTable = document.getElementById('models-table');
    if (modelsTable) {
        let mt = '<thead><tr><th>Model</th><th>Table</th><th>Columns</th></tr></thead><tbody>';
        D.models.forEach(m => { mt += `<tr><td><strong>${m.name}</strong></td><td><code>${m.table}</code></td><td>${m.columns}</td></tr>`; });
        mt += '</tbody>';
        modelsTable.innerHTML = mt;
    }

    // Services table
    const svcTable = document.getElementById('services-table');
    if (svcTable) {
        let st = '<thead><tr><th>Service</th><th>Image</th><th>Ports</th></tr></thead><tbody>';
        D.services.forEach(s => { st += `<tr><td><strong>${s.name}</strong></td><td>${s.image||'build'}</td><td>${s.ports.join(', ')||'\u2014'}</td></tr>`; });
        st += '</tbody>';
        svcTable.innerHTML = st;
    }

    // Endpoints table
    const epTable = document.getElementById('endpoints-table');
    if (epTable) {
        let et = '<thead><tr><th>Module</th><th>Method</th><th>Path</th><th>Handler</th></tr></thead><tbody>';
        D.endpoints.forEach(e => { et += `<tr><td>${e.module}</td><td><span class="method method-${e.method}">${e.method}</span></td><td><code>${e.path}</code></td><td>${e.handler}</td></tr>`; });
        et += '</tbody>';
        epTable.innerHTML = et;
    }

    renderAnalysis('system_architecture', document.getElementById('arch-diagram')?.closest('.card'));
    if (modelsTable) renderAnalysis('database_models', modelsTable.closest('.card'));
    if (svcTable) renderAnalysis('docker_services', svcTable.closest('.card'));
    if (epTable) renderAnalysis('api_endpoints', epTable.closest('.card'));
}

// ===================== DATA FLOW TAB =====================
const dfTab = document.getElementById('tab-dataflow');
if (dfTab) {
    let html = '';
    if (D.has_frontend && D.endpoints.length) {
        html += '<div class="card" style="margin-bottom:20px"><h3>API Coverage Matrix</h3>';
        html += '<p style="color:var(--text-dim);font-size:12px;margin-bottom:12px">Frontend pages (rows) vs Backend route modules (columns). Green = connected.</p>';
        html += '<div style="overflow-x:auto" id="coverage-matrix"></div></div>';
    }
    if (D.frontend_api_calls.length) {
        html += '<div class="card" style="margin-top:20px"><h3>Frontend API Calls</h3><table id="api-calls-table"></table></div>';
    }
    dfTab.innerHTML = html;

    // Coverage matrix
    const cm = D.coverage_matrix;
    if (cm.pages.length && cm.modules.length) {
        const grid = document.getElementById('coverage-matrix');
        let cmHTML = `<div class="coverage-grid" style="grid-template-columns: 120px repeat(${cm.modules.length}, 1fr)">`;
        cmHTML += '<div></div>';
        cm.modules.forEach(m => { cmHTML += `<div class="coverage-header">${m}</div>`; });
        cm.pages.forEach(page => {
            cmHTML += `<div class="coverage-label">${page}</div>`;
            cm.modules.forEach(mod => {
                const key = page + '|' + mod;
                const hits = cm.hits[key] || 0;
                const bg = hits > 0 ? `rgba(129,199,132,${Math.min(0.3 + hits * 0.15, 1)})` : 'var(--surface2)';
                cmHTML += `<div class="coverage-cell" style="background:${bg}" title="${page} \u2192 ${mod}: ${hits} call(s)">${hits || ''}</div>`;
            });
        });
        cmHTML += '</div>';
        grid.innerHTML = cmHTML;
        renderAnalysis('coverage_matrix', grid.closest('.card'));
    }

    // API calls table
    const apiTable = document.getElementById('api-calls-table');
    if (apiTable) {
        let at = '<thead><tr><th>Page</th><th>Method</th><th>URL</th></tr></thead><tbody>';
        D.frontend_api_calls.forEach(c => { at += `<tr><td>${c.page}</td><td><span class="method method-${c.method}">${c.method}</span></td><td><code>${c.url}</code></td></tr>`; });
        at += '</tbody>';
        apiTable.innerHTML = at;
        renderAnalysis('frontend_api_calls', apiTable.closest('.card'));
    }
}

// ===================== EVENTS TAB =====================
const auditTab = document.getElementById('tab-audit');
if (auditTab) {
    let html = '';
    if (D.event_types.length) {
        html += '<div class="stat-row">';
        html += `<div class="stat"><div class="value">${D.event_types.length}</div><div class="label">Event Types</div></div>`;
        html += `<div class="stat"><div class="value">${Object.keys(D.event_sources).length}</div><div class="label">Source Modules</div></div>`;
        html += '</div>';
    }
    if (D.event_types.length) {
        html += '<div class="grid grid-2">';
        html += '<div class="card"><h3>Event Types by Category</h3><div class="chart-container"><canvas id="chart-event-categories"></canvas></div></div>';
        html += '<div class="card"><h3>Event Sources</h3><div class="chart-container"><canvas id="chart-event-sources"></canvas></div></div>';
        html += '</div>';
        html += '<div class="card" style="margin-top:20px"><h3>Event Type Registry</h3><table id="events-table"></table></div>';
    }
    if (!D.event_types.length && D.has_event_system) {
        html += '<div class="card"><h3>Event System</h3><p style="color:var(--text-dim)">Event publishing patterns detected but no formal event registry found.</p></div>';
    }
    auditTab.innerHTML = html;

    if (D.event_types.length) {
        const eventCategories = {};
        D.event_types.forEach(e => { const cat = e.type.replace(/([A-Z])/g, ' $1').trim().split(' ')[0]; eventCategories[cat] = (eventCategories[cat] || 0) + 1; });

        new Chart(document.getElementById('chart-event-categories'), {
            type: 'doughnut',
            data: { labels: Object.keys(eventCategories), datasets: [{ data: Object.values(eventCategories), backgroundColor: Object.keys(eventCategories).map((_,i) => getColor(i)), borderWidth: 0 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right', labels: { color: '#e1e4ed', font: { size: 11 } } } } }
        });

        const srcLabels = Object.keys(D.event_sources);
        const srcData = srcLabels.map(k => D.event_sources[k].length);
        if (srcLabels.length) {
            new Chart(document.getElementById('chart-event-sources'), {
                type: 'bar',
                data: { labels: srcLabels, datasets: [{ data: srcData, backgroundColor: srcLabels.map((_,i) => getColor(i)), borderRadius: 4 }] },
                options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { color: '#2e3347' }, ticks: { color: '#8b8fa3' } }, y: { grid: { display: false }, ticks: { color: '#e1e4ed', font: { size: 11 } } } } }
            });
        }

        let evtHTML = '<thead><tr><th>Event Type</th><th>Action</th><th>Sources</th></tr></thead><tbody>';
        D.event_types.forEach(e => {
            const sources = [];
            Object.entries(D.event_sources).forEach(([mod, evts]) => { if (evts.includes(e.type)) sources.push(mod); });
            evtHTML += `<tr><td><strong>${e.type}</strong></td><td><code>${e.action}</code></td><td>${sources.map(s=>`<span class="event-tag">${s}</span>`).join(' ')||'\u2014'}</td></tr>`;
        });
        evtHTML += '</tbody>';
        document.getElementById('events-table').innerHTML = evtHTML;
        renderAnalysis('event_registry', document.getElementById('events-table')?.closest('.card'));
    }
}

// ===================== FRONTEND TAB =====================
const feTab = document.getElementById('tab-frontend');
if (feTab && D.has_frontend) {
    let html = '<div class="grid grid-2">';
    if (D.pages.length) html += '<div class="card"><h3>Page Sizes</h3><div class="chart-container"><canvas id="chart-page-sizes"></canvas></div></div>';
    // UI library usage
    const uiFreq = {};
    D.pages.forEach(p => (p.ant_design||[]).forEach(c => { uiFreq[c] = (uiFreq[c] || 0) + 1; }));
    if (Object.keys(uiFreq).length) html += '<div class="card"><h3>UI Component Usage</h3><div class="chart-container"><canvas id="chart-ui-components"></canvas></div></div>';
    html += '</div>';
    if (D.routes.length) html += '<div class="card" style="margin-top:20px"><h3>Route Map</h3><table id="routes-table"></table></div>';
    feTab.innerHTML = html;

    if (D.pages.length) {
        new Chart(document.getElementById('chart-page-sizes'), {
            type: 'bar',
            data: { labels: D.pages.map(p => p.name), datasets: [{ label: 'Lines', data: D.pages.map(p => p.lines), backgroundColor: D.pages.map((_,i) => getColor(i)), borderRadius: 4 }] },
            options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { color: '#2e3347' }, ticks: { color: '#8b8fa3' } }, y: { grid: { display: false }, ticks: { color: '#e1e4ed', font: { size: 11 } } } } }
        });
        renderAnalysis('page_sizes', document.getElementById('chart-page-sizes')?.closest('.card'));
    }

    const uiCanvas = document.getElementById('chart-ui-components');
    if (uiCanvas) {
        const uiSorted = Object.entries(uiFreq).sort((a,b) => b[1] - a[1]).slice(0, 15);
        new Chart(uiCanvas, {
            type: 'bar',
            data: { labels: uiSorted.map(e => e[0]), datasets: [{ data: uiSorted.map(e => e[1]), backgroundColor: uiSorted.map((_,i) => getColor(i)), borderRadius: 4 }] },
            options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { title: { display: true, text: 'Pages using component', color: '#8b8fa3' }, grid: { color: '#2e3347' }, ticks: { color: '#8b8fa3' } }, y: { grid: { display: false }, ticks: { color: '#e1e4ed', font: { size: 11 } } } } }
        });
        renderAnalysis('ui_library_usage', uiCanvas.closest('.card'));
    }

    const routesTable = document.getElementById('routes-table');
    if (routesTable) {
        let rt = '<thead><tr><th>Path</th><th>Component</th></tr></thead><tbody>';
        D.routes.forEach(r => { rt += `<tr><td><code>${r.path}</code></td><td><strong>${r.component}</strong></td></tr>`; });
        rt += '</tbody>';
        routesTable.innerHTML = rt;
        renderAnalysis('route_map', routesTable.closest('.card'));
    }
}

// ===================== CHANGES TAB =====================
const changesTab = document.getElementById('tab-changes');
if (changesTab) {
    changesTab.innerHTML =
        '<div class="card"><h3>Change Heatmap by Domain</h3><div id="change-heatmap"></div></div>' +
        '<div class="card" style="margin-top:20px"><h3>Largest Files (Top 30)</h3><div class="chart-container" style="height:400px"><canvas id="chart-file-sizes"></canvas></div></div>';

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

    const fileSizeEntries = Object.entries(D.file_sizes).slice(0, 25);
    new Chart(document.getElementById('chart-file-sizes'), {
        type: 'bar',
        data: { labels: fileSizeEntries.map(e => e[0].split('/').pop()), datasets: [{ label: 'Lines', data: fileSizeEntries.map(e => e[1]), backgroundColor: fileSizeEntries.map((_,i) => getColor(i)), borderRadius: 4 }] },
        options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { title: (items) => fileSizeEntries[items[0].dataIndex][0] } } }, scales: { x: { grid: { color: '#2e3347' }, ticks: { color: '#8b8fa3' } }, y: { grid: { display: false }, ticks: { color: '#e1e4ed', font: { size: 10 } } } } }
    });

    renderAnalysis('change_heatmap', document.getElementById('change-heatmap')?.closest('.card'));
    renderAnalysis('file_sizes', document.getElementById('chart-file-sizes')?.closest('.card'));
}

// ===================== TREEMAP =====================
function renderTreemap(containerId, data) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const entries = Object.entries(data).sort((a,b) => b[1] - a[1]);
    const total = entries.reduce((s,e) => s + e[1], 0);
    if (!total) { el.innerHTML = '<p style="color:var(--text-dim)">No data</p>'; return; }
    const W = el.clientWidth || 600;
    const H = 250;
    el.style.height = H + 'px';
    let cells = entries.map(([name, value], i) => ({name, value, color: getColor(i)}));
    let x = 0, y = 0;
    const rects = [];
    let remaining = [...cells];
    let remTotal = total;
    while (remaining.length > 0) {
        const availW = W - x;
        const availH = H - y;
        const isHoriz = availW >= availH;
        const stripSize = isHoriz ? availH : availW;
        let rowItems = [];
        let rowSum = 0;
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
            for (let item of rowItems) { const cellH = (item.value / rowSum) * availH; rects.push({...item, x, y: cy, w: stripW, h: cellH}); cy += cellH; }
            x += stripW;
        } else {
            const stripH = stripFrac * availH;
            let cx = x;
            for (let item of rowItems) { const cellW = (item.value / rowSum) * availW; rects.push({...item, x: cx, y, w: cellW, h: stripH}); cx += cellW; }
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
</script>
</body>
</html>"""
