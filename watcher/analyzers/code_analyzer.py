"""
Code Analyzer — Rule-based analysis engine (generic).

Scans any project codebase and produces per-section assessments with:
- Implementation status and progress
- Specific findings from code patterns
- Recommended actions (heuristic-based)

Each section in the dashboard gets an annotation dict:
{
    "status": "complete" | "partial" | "needs_work" | "missing" | "info",
    "progress": 0-100,
    "summary": "one-line summary",
    "findings": ["specific finding 1", ...],
    "recommendations": ["recommended action 1", ...],
}
"""

import os
import re
from pathlib import Path

from watcher.config import Config


def analyze_project(config: Config, dashboard_data: dict) -> dict[str, dict]:
    """Run all analysis rules and return per-section annotations."""
    D = dashboard_data
    sections = {}

    # === OVERVIEW TAB ===
    sections["code_distribution"] = _analyze_code_distribution(D)
    sections["git_history"] = _analyze_git_history(D)

    # === ARCHITECTURE TAB ===
    sections["system_architecture"] = _analyze_architecture(D)
    sections["database_models"] = _analyze_models(D)
    sections["docker_services"] = _analyze_docker(D)
    sections["api_endpoints"] = _analyze_endpoints(D)

    # === DATA FLOW TAB ===
    sections["coverage_matrix"] = _analyze_coverage(D)
    sections["frontend_api_calls"] = _analyze_frontend_calls(D)

    # === FRONTEND TAB ===
    if D.get("pages"):
        sections["page_sizes"] = _analyze_pages(D)
        sections["ui_library_usage"] = _analyze_ui_library(D)
        sections["route_map"] = _analyze_routes(D)

    # === AUDIT/EVENT TAB ===
    if D.get("event_types") or D.get("event_sources"):
        sections["event_registry"] = _analyze_events(D)

    # === CHANGES TAB ===
    sections["change_heatmap"] = _analyze_changes(D)
    sections["file_sizes"] = _analyze_file_sizes(D)

    # --- Cross-cutting: TODO/FIXME scan ---
    todos = _scan_todos(config.project_path, config)
    if todos:
        sections["todos"] = {
            "status": "needs_work",
            "progress": 0,
            "summary": f"{len(todos)} TODO/FIXME comments found in codebase",
            "findings": todos[:20],
            "recommendations": ["Address high-priority TODOs before release"],
        }

    return sections


# ============================================================
# Section analyzers
# ============================================================

def _analyze_code_distribution(D: dict) -> dict:
    total_lines = sum(D.get("domain_lines", {}).values())
    total_files = sum(D.get("domain_files", {}).values())
    domains = D.get("domain_lines", {})
    findings = []
    recs = []

    if domains:
        largest = max(domains.values())
        largest_name = max(domains, key=domains.get)
        if total_lines > 0 and largest > total_lines * 0.4:
            findings.append(f"'{largest_name}' contains {largest:,} lines ({int(largest/total_lines*100)}% of codebase)")
            recs.append(f"Consider splitting large files in {largest_name} into smaller modules")

    test_files = D.get("domain_files", {}).get("Tests", 0)
    if test_files == 0:
        findings.append("No test files detected in codebase")
        recs.append("Add unit tests for critical paths")

    return {
        "status": "info",
        "progress": 100,
        "summary": f"{total_lines:,} lines across {total_files} files in {len(domains)} domains",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_git_history(D: dict) -> dict:
    commits = D.get("git_log", [])
    findings = []
    if not commits:
        findings.append("No git history available")
    else:
        findings.append(f"{len(commits)} recent commits (showing last 20)")

    return {
        "status": "info",
        "progress": 100,
        "summary": f"{len(commits)} commits in history",
        "findings": findings,
        "recommendations": [],
    }


def _analyze_architecture(D: dict) -> dict:
    findings = []
    recs = []

    svc_count = len(D.get("services", []))
    endpoints = D.get("endpoints", [])
    models = D.get("models", [])
    modules = set(e["module"] for e in endpoints) if endpoints else set()

    if svc_count:
        findings.append(f"{svc_count} Docker services defined")
    findings.append(f"{len(endpoints)} API endpoints across {len(modules)} route modules")
    findings.append(f"{len(models)} database models")

    return {
        "status": "complete" if endpoints and models else "partial",
        "progress": 80 if endpoints and models else 40,
        "summary": f"{len(endpoints)} endpoints, {len(models)} models, {svc_count} Docker services.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_models(D: dict) -> dict:
    models = D.get("models", [])
    findings = []
    recs = []

    if not models:
        return {"status": "info", "progress": 0, "summary": "No models detected", "findings": [], "recommendations": []}

    total_cols = sum(m["columns"] for m in models)
    findings.append(f"{len(models)} models with {total_cols} total columns")

    for m in models:
        if m["columns"] > 15:
            findings.append(f"{m['name']} has {m['columns']} columns — consider if all belong in one table")

    return {
        "status": "complete",
        "progress": 100,
        "summary": f"{len(models)} tables, {total_cols} columns.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_docker(D: dict) -> dict:
    services = D.get("services", [])
    if not services:
        return {"status": "info", "progress": 0, "summary": "No Docker services", "findings": [], "recommendations": []}

    findings = [f"{len(services)} Docker services configured"]
    return {
        "status": "complete",
        "progress": 100,
        "summary": f"{len(services)} services. Infrastructure containerized.",
        "findings": findings,
        "recommendations": [],
    }


def _analyze_endpoints(D: dict) -> dict:
    endpoints = D.get("endpoints", [])
    if not endpoints:
        return {"status": "info", "progress": 0, "summary": "No endpoints detected", "findings": [], "recommendations": []}

    findings = []
    recs = []

    methods = {}
    for e in endpoints:
        methods[e["method"]] = methods.get(e["method"], 0) + 1
    findings.append(f"{len(endpoints)} endpoints: " + ", ".join(f"{c} {m}" for m, c in sorted(methods.items())))

    return {
        "status": "complete",
        "progress": 95,
        "summary": f"{len(endpoints)} endpoints.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_coverage(D: dict) -> dict:
    cm = D.get("coverage_matrix", {})
    pages = cm.get("pages", [])
    modules = cm.get("modules", [])
    hits = cm.get("hits", {})

    if not pages or not modules:
        return {"status": "info", "progress": 0, "summary": "No coverage data", "findings": [], "recommendations": []}

    findings = []
    recs = []

    connected_pages = set()
    for key in hits:
        page = key.split("|")[0]
        connected_pages.add(page)
    disconnected = set(pages) - connected_pages
    if disconnected:
        findings.append(f"Pages with no detected backend calls: {', '.join(sorted(disconnected))}")
        recs.append("These pages may use a service layer not detected by direct URL parsing")

    connected_modules = set()
    for key in hits:
        mod = key.split("|")[1]
        connected_modules.add(mod)
    orphaned = set(modules) - connected_modules
    if orphaned:
        findings.append(f"Backend modules with no frontend callers: {', '.join(sorted(orphaned))}")

    coverage_pct = int(len(connected_pages) / len(pages) * 100) if pages else 0
    return {
        "status": "partial" if disconnected else "complete",
        "progress": coverage_pct,
        "summary": f"{len(connected_pages)}/{len(pages)} pages connected to backend. {len(orphaned)} orphaned modules.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_frontend_calls(D: dict) -> dict:
    calls = D.get("frontend_api_calls", [])
    if not calls:
        return {"status": "info", "progress": 0, "summary": "No frontend API calls detected", "findings": [], "recommendations": []}

    findings = [f"{len(calls)} API calls detected across all pages"]
    return {
        "status": "info",
        "progress": 100,
        "summary": f"{len(calls)} frontend→backend API calls mapped",
        "findings": findings,
        "recommendations": [],
    }


def _analyze_events(D: dict) -> dict:
    event_types = D.get("event_types", [])
    sources = D.get("event_sources", {})

    all_types = {e["type"] for e in event_types} if event_types else set()
    published_types = set()
    for evts in sources.values():
        published_types.update(evts)

    findings = [f"{len(all_types)} event types, {len(published_types)} published from detected sources"]

    return {
        "status": "complete" if all_types else "info",
        "progress": 95 if all_types else 0,
        "summary": f"{len(all_types)} event types detected.",
        "findings": findings,
        "recommendations": [],
    }


def _analyze_pages(D: dict) -> dict:
    pages = D.get("pages", [])
    if not pages:
        return {"status": "info", "progress": 0, "summary": "No pages", "findings": [], "recommendations": []}

    findings = []
    recs = []

    large_pages = [p for p in pages if p["lines"] > 500]
    if large_pages:
        names = ", ".join(p["name"] for p in large_pages)
        findings.append(f"Large pages (>500 lines): {names}")
        recs.append("Consider extracting reusable components from large page files")

    total_lines = sum(p["lines"] for p in pages)
    findings.append(f"{len(pages)} pages, {total_lines:,} total lines")

    return {
        "status": "complete",
        "progress": 100,
        "summary": f"{len(pages)} pages. {len(large_pages)} could benefit from refactoring.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_ui_library(D: dict) -> dict:
    pages = D.get("pages", [])
    all_components = set()
    for p in pages:
        all_components.update(p.get("ant_design", []))

    if not all_components:
        return {"status": "info", "progress": 100, "summary": "No UI library components detected", "findings": [], "recommendations": []}

    return {
        "status": "info",
        "progress": 100,
        "summary": f"{len(all_components)} unique UI components used across {len(pages)} pages",
        "findings": [f"Components: {', '.join(sorted(all_components))}"],
        "recommendations": [],
    }


def _analyze_routes(D: dict) -> dict:
    routes = D.get("routes", [])
    return {
        "status": "complete" if routes else "info",
        "progress": 100 if routes else 0,
        "summary": f"{len(routes)} frontend routes defined",
        "findings": [f"{len(routes)} routes mapped to page components"] if routes else [],
        "recommendations": [],
    }


def _analyze_changes(D: dict) -> dict:
    history = D.get("change_history", [])
    if not history:
        return {
            "status": "info",
            "progress": 0,
            "summary": "No changes recorded yet. Heatmap populates as the watcher detects file changes.",
            "findings": [],
            "recommendations": [],
        }

    total_changes = sum(h["total"] for h in history)
    return {
        "status": "info",
        "progress": 100,
        "summary": f"{total_changes} changes across {len(history)} flush intervals",
        "findings": [f"Most recent: {history[-1]['timestamp']}"],
        "recommendations": [],
    }


def _analyze_file_sizes(D: dict) -> dict:
    files = D.get("file_sizes", {})
    large = [(f, lines) for f, lines in files.items() if lines > 800]
    findings = []
    recs = []

    if large:
        findings.append(f"{len(large)} files exceed 800 lines")
        for f, lines in large[:5]:
            findings.append(f"  {f}: {lines:,} lines")
        recs.append("Consider splitting large files for maintainability")

    return {
        "status": "info" if not large else "needs_work",
        "progress": 100,
        "summary": f"{len(files)} files tracked. {len(large)} are notably large.",
        "findings": findings,
        "recommendations": recs,
    }


# ============================================================
# Cross-cutting: TODO/FIXME scanner
# ============================================================

def _scan_todos(project: Path, config: Config) -> list[str]:
    """Scan source files for TODO/FIXME/HACK/XXX comments."""
    todos = []
    code_exts = (".py", ".tsx", ".ts", ".jsx", ".js", ".go", ".rs", ".java", ".rb")
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in config.watch_excludes]
        for fname in files:
            if not any(fname.endswith(ext) for ext in code_exts):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        for marker in ("TODO", "FIXME", "HACK", "XXX"):
                            if marker in line and ("#" in line or "//" in line):
                                rel = os.path.relpath(fpath, project)
                                comment = line.strip()
                                if len(comment) > 100:
                                    comment = comment[:97] + "..."
                                todos.append(f"{rel}:{i} — {comment}")
                                break
            except OSError:
                pass
    return todos
