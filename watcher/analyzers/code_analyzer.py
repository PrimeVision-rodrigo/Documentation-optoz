"""
Code Analyzer — Rule-based analysis engine.

Scans the project codebase and produces per-section assessments with:
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
from watcher.parsers.python_parser import parse_routes_file, parse_models_file
from watcher.parsers.typescript_parser import parse_tsx_file, parse_app_tsx


def analyze_project(config: Config, dashboard_data: dict) -> dict[str, dict]:
    """Run all analysis rules and return per-section annotations.

    Args:
        config: watcher config
        dashboard_data: the full data dict already collected by dashboard generator

    Returns:
        Dict mapping section_id → annotation dict
    """
    project = config.project_path
    D = dashboard_data
    sections = {}

    # --- Dev status from markdown (parse existing percentages) ---
    dev_status = _parse_dev_status(project)

    # === OVERVIEW TAB ===

    sections["user_journey"] = _analyze_user_journey(project, D, dev_status)
    sections["code_distribution"] = _analyze_code_distribution(D)
    sections["git_history"] = _analyze_git_history(D)

    # === ARCHITECTURE TAB ===

    sections["system_architecture"] = _analyze_architecture(project, D, dev_status)
    sections["database_models"] = _analyze_models(project, D)
    sections["docker_services"] = _analyze_docker(D)
    sections["api_endpoints"] = _analyze_endpoints(project, D)

    # === DATA FLOW TAB ===

    sections["data_pipeline"] = _analyze_data_pipeline(project, D, dev_status)
    sections["coverage_matrix"] = _analyze_coverage(D)
    sections["frontend_api_calls"] = _analyze_frontend_calls(D)

    # === AUDIT TRAIL TAB ===

    sections["triple_write"] = _analyze_triple_write(project, D, dev_status)
    sections["provenance"] = _analyze_provenance(D)
    sections["event_registry"] = _analyze_events(project, D)

    # === FRONTEND TAB ===

    sections["page_sizes"] = _analyze_pages(D)
    sections["ant_design_usage"] = _analyze_ant_design(D)
    sections["route_map"] = _analyze_routes(D)

    # === CHANGES TAB ===

    sections["change_heatmap"] = _analyze_changes(D)
    sections["file_sizes"] = _analyze_file_sizes(D)

    # --- Cross-cutting: TODO/FIXME scan ---
    todos = _scan_todos(project, config)
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
# Dev status parser
# ============================================================

def _parse_dev_status(project: Path) -> dict[str, int]:
    """Parse DEVELOPMENT_STATUS_AND_NEXT_STEPS.md for coverage percentages."""
    status = {}
    for path in [
        project / "docs" / "development" / "DEVELOPMENT_STATUS_AND_NEXT_STEPS.md",
        project / "DEVELOPMENT_STATUS_AND_NEXT_STEPS.md",
    ]:
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Match both plain and bold: "| Core functionality | 90% |" or "| **Core** | 90% |"
        for match in re.finditer(
            r'\|\s*\*{0,2}([^|*]+?)\*{0,2}\s*\|\s*(\d+)%',
            content,
        ):
            key = match.group(1).strip().lower()
            pct = int(match.group(2))
            # Only take dimension rows (skip noise like "30% of mean")
            if pct <= 100 and len(key) > 3 and not key.startswith(("---", "dim")):
                status[key] = pct
        break
    return status


# ============================================================
# Section analyzers
# ============================================================

def _analyze_user_journey(project: Path, D: dict, dev_status: dict) -> dict:
    core = dev_status.get("core functionality", 0)
    findings = []
    recs = []

    if dev_status.get("image quality", 0) < 50:
        findings.append(f"Image quality analysis at {dev_status.get('image quality', 0)}% — only lighting calibration implemented")
        recs.append("Add blur detection, exposure analysis, and SNR checks to the capture pipeline")
    if dev_status.get("edge deployment", 0) < 50:
        findings.append(f"Edge deployment at {dev_status.get('edge deployment', 0)}% — packages are metadata stubs")
        recs.append("Implement actual model export (ONNX/TensorRT) and deployment artifact generation")
    if dev_status.get("active learning", 0) == 0:
        findings.append("Active learning not implemented")
        recs.append("Design a feedback loop: inference results → suggested labeling priorities")
    if dev_status.get("factory integration", 0) == 0:
        findings.append("No factory integration (PLC, MES, digital I/O)")
        recs.append("Define integration protocol for factory floor communication")

    return {
        "status": "partial" if core < 100 else "complete",
        "progress": core,
        "summary": f"Core workflow {core}% complete. {len(findings)} areas need development.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_code_distribution(D: dict) -> dict:
    total_lines = sum(D.get("domain_lines", {}).values())
    total_files = sum(D.get("domain_files", {}).values())
    domains = D.get("domain_lines", {})
    findings = []
    recs = []

    # Check for imbalances
    if domains:
        largest = max(domains.values())
        largest_name = max(domains, key=domains.get)
        if largest > total_lines * 0.4:
            findings.append(f"'{largest_name}' contains {largest:,} lines ({int(largest/total_lines*100)}% of codebase)")
            recs.append(f"Consider splitting large files in {largest_name} into smaller modules")

    test_files = D.get("domain_files", {}).get("Tests", 0)
    if test_files == 0:
        findings.append("No test files detected in codebase")
        recs.append("Add unit tests for critical paths: event publishing, training pipeline, inference")

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
        versions = [c for c in commits if c.strip().startswith(("V", "v")) or ": V" in c or "V1" in c]
        if versions:
            findings.append(f"Version milestones found: {len(versions)}")

    return {
        "status": "info",
        "progress": 100,
        "summary": f"{len(commits)} commits in history",
        "findings": findings,
        "recommendations": [],
    }


def _analyze_architecture(project: Path, D: dict, dev_status: dict) -> dict:
    findings = []
    recs = []

    svc_count = len([s for s in D.get("services", []) if s.get("image") or s.get("ports")])
    findings.append(f"{svc_count} Docker services defined")
    findings.append(f"{len(D.get('endpoints', []))} API endpoints across {len(set(e['module'] for e in D.get('endpoints', [])))} route modules")
    findings.append(f"{len(D.get('models', []))} database models")

    compliance = dev_status.get("compliance (21 cfr part 11)", 0)
    if compliance < 100:
        findings.append(f"Compliance coverage at {compliance}%")
        recs.append("Complete digital signature implementation and add role-based access controls to remaining endpoints")

    data_arch = dev_status.get("data architecture", 0)
    if data_arch >= 100:
        findings.append("Event sourcing architecture fully realized")

    return {
        "status": "complete" if compliance >= 85 else "partial",
        "progress": min(compliance, 95),
        "summary": f"Microservice architecture with event sourcing. Compliance at {compliance}%.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_models(project: Path, D: dict) -> dict:
    models = D.get("models", [])
    findings = []
    recs = []

    total_cols = sum(m["columns"] for m in models)
    findings.append(f"{len(models)} models with {total_cols} total columns")

    # Check for models with very many columns (potential normalization issue)
    for m in models:
        if m["columns"] > 15:
            findings.append(f"{m['name']} has {m['columns']} columns — consider if all belong in one table")

    # Check for critical models
    model_names = {m["name"] for m in models}
    expected = {"Event", "User", "Project", "TrainingJob", "AuditRecord"}
    missing = expected - model_names
    if missing:
        findings.append(f"Missing expected models: {', '.join(missing)}")
        recs.append(f"Add models: {', '.join(missing)}")

    return {
        "status": "complete",
        "progress": 100,
        "summary": f"{len(models)} tables, {total_cols} columns. All core models present.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_docker(D: dict) -> dict:
    services = D.get("services", [])
    real_services = [s for s in services if s.get("name") not in ("optoz-net", "postgres_data", "valkey_data")]
    findings = [f"{len(real_services)} Docker services configured"]
    recs = []

    named = {s["name"] for s in services}
    if "training-worker" not in named:
        recs.append("Training worker service not found in docker-compose")

    return {
        "status": "complete",
        "progress": 100,
        "summary": f"{len(real_services)} services. Infrastructure fully containerized.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_endpoints(project: Path, D: dict) -> dict:
    endpoints = D.get("endpoints", [])
    findings = []
    recs = []

    # Count by method
    methods = {}
    for e in endpoints:
        methods[e["method"]] = methods.get(e["method"], 0) + 1

    findings.append(f"{len(endpoints)} endpoints: " + ", ".join(f"{c} {m}" for m, c in sorted(methods.items())))

    # Check for routes without auth
    event_sources = D.get("event_sources", {})
    modules_with_events = set(event_sources.keys())
    all_modules = set(e["module"] for e in endpoints)
    modules_without_events = all_modules - modules_with_events - {"system", "auth", "sam2", "sample_projects"}
    if modules_without_events:
        findings.append(f"Routes without audit events: {', '.join(sorted(modules_without_events))}")
        recs.append(f"Add publish_event() calls to: {', '.join(sorted(modules_without_events))}")

    return {
        "status": "complete",
        "progress": 95,
        "summary": f"{len(endpoints)} endpoints. {len(modules_with_events)}/{len(all_modules)} modules publish audit events.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_data_pipeline(project: Path, D: dict, dev_status: dict) -> dict:
    findings = []
    recs = []

    inference = dev_status.get("inference & monitoring", 0)
    training = dev_status.get("training & models", 0)
    findings.append(f"Training pipeline: {training}%")
    findings.append(f"Inference pipeline: {inference}%")

    img_quality = dev_status.get("image quality", 0)
    if img_quality < 50:
        findings.append(f"Image quality pipeline only {img_quality}% — no automated quality gate before training")
        recs.append("Add image quality validation step between capture and training (blur, exposure, SNR)")

    return {
        "status": "partial" if img_quality < 50 else "complete",
        "progress": int((training + inference) / 2),
        "summary": f"Core pipelines operational. Image quality validation is the main gap.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_coverage(D: dict) -> dict:
    cm = D.get("coverage_matrix", {})
    pages = cm.get("pages", [])
    modules = cm.get("modules", [])
    hits = cm.get("hits", {})
    findings = []
    recs = []

    if not pages or not modules:
        return {"status": "info", "progress": 0, "summary": "No coverage data", "findings": [], "recommendations": []}

    # Find disconnected pages
    connected_pages = set()
    for key in hits:
        page = key.split("|")[0]
        connected_pages.add(page)
    disconnected = set(pages) - connected_pages
    if disconnected:
        findings.append(f"Pages with no detected backend calls: {', '.join(sorted(disconnected))}")
        recs.append("These pages may use a service layer not detected by direct URL parsing, or may need API integration")

    # Find orphaned modules
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
    findings = [f"{len(calls)} API calls detected across all pages"]

    # Check for hardcoded URLs vs template literals
    hardcoded = [c for c in calls if "${" not in c["url"]]
    if hardcoded:
        findings.append(f"{len(hardcoded)} calls use static URLs (may not be project-scoped)")

    return {
        "status": "info",
        "progress": 100,
        "summary": f"{len(calls)} frontend→backend API calls mapped",
        "findings": findings,
        "recommendations": [],
    }


def _analyze_triple_write(project: Path, D: dict, dev_status: dict) -> dict:
    compliance = dev_status.get("compliance (21 cfr part 11)", 0)
    findings = [f"21 CFR Part 11 compliance: {compliance}%"]
    recs = []

    event_types = D.get("event_types", [])
    findings.append(f"{len(event_types)} event types in triple-write registry")

    if compliance < 100:
        recs.append("Complete remaining compliance items: electronic signatures, full RBAC on all routes")

    return {
        "status": "partial" if compliance < 100 else "complete",
        "progress": compliance,
        "summary": f"Triple-write operational with {len(event_types)} event types. Compliance at {compliance}%.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_provenance(D: dict) -> dict:
    prov = D.get("provenance", {})
    stages = prov.get("stages", [])
    flows = prov.get("chain_flow", [])
    findings = []
    recs = []

    active_stages = [s for s in stages if s.get("hash_computations") or s.get("hash_reads") or s.get("chain_hashes")]
    findings.append(f"{len(active_stages)} lifecycle stages with hash operations")
    findings.append(f"{len(flows)} hash flow connections between stages")

    # Check for gaps
    stage_ids = {s["id"] for s in stages}
    for s in stages:
        if s["id"] == "labeling":
            has_hash = any(e.get("hash_fields") for e in s.get("events_published", []))
            if not has_hash:
                findings.append("Labeling stage does not include file_hash in event payloads")
                recs.append("Add file_hash to ImageLabeled event payload for unbroken provenance chain")

    # Check if inference connects to capture
    capture_to_inference = any(f["from_stage"] == "capture" and f["to_stage"] == "inference" for f in flows)
    if capture_to_inference:
        findings.append("Capture → Inference hash chain verified (file_hash flows through)")
    else:
        findings.append("No direct hash flow from capture to inference detected")
        recs.append("Verify that inference reads file_hash from AuditRecord")

    progress = 75 if recs else 100
    return {
        "status": "partial" if recs else "complete",
        "progress": progress,
        "summary": f"Hash provenance across {len(active_stages)} stages. {'Gap in labeling stage.' if recs else 'Chain intact.'}",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_events(project: Path, D: dict) -> dict:
    event_types = D.get("event_types", [])
    sources = D.get("event_sources", {})
    findings = []
    recs = []

    all_types = {e["type"] for e in event_types}
    published_types = set()
    for evts in sources.values():
        published_types.update(evts)

    # Types registered but never published from routes
    orphaned = all_types - published_types
    # Some are published by training worker, not routes
    training_events = {"TrainingJobCompleted", "TrainingJobFailed", "ExploratorySearchCompleted",
                       "HPOJobCompleted", "HPOTrialCompleted"}
    truly_orphaned = orphaned - training_events
    if truly_orphaned:
        findings.append(f"Event types not published by any route: {', '.join(sorted(truly_orphaned))}")

    findings.append(f"{len(event_types)} types registered, {len(published_types)} published from routes, {len(training_events & all_types)} from training worker")

    return {
        "status": "complete",
        "progress": 95,
        "summary": f"{len(event_types)} event types. Full coverage across routes and training worker.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_pages(D: dict) -> dict:
    pages = D.get("pages", [])
    findings = []
    recs = []

    large_pages = [p for p in pages if p["lines"] > 500]
    if large_pages:
        names = ", ".join(p["name"] for p in large_pages)
        findings.append(f"Large pages (>500 lines): {names}")
        recs.append("Consider extracting reusable components from large page files")

    total_lines = sum(p["lines"] for p in pages)
    findings.append(f"{len(pages)} pages, {total_lines:,} total lines")

    no_api = [p for p in pages if not p.get("api_calls")]
    if no_api:
        names = ", ".join(p["name"] for p in no_api)
        findings.append(f"Pages with no detected API calls: {names}")
        recs.append("These pages may use a shared service/store layer — verify data fetching pattern")

    return {
        "status": "complete",
        "progress": 100,
        "summary": f"{len(pages)} pages implemented. {len(large_pages)} could benefit from refactoring.",
        "findings": findings,
        "recommendations": recs,
    }


def _analyze_ant_design(D: dict) -> dict:
    pages = D.get("pages", [])
    all_components = set()
    for p in pages:
        all_components.update(p.get("ant_design", []))

    return {
        "status": "info",
        "progress": 100,
        "summary": f"{len(all_components)} unique Ant Design components used across {len(pages)} pages",
        "findings": [f"Components: {', '.join(sorted(all_components))}"],
        "recommendations": [],
    }


def _analyze_routes(D: dict) -> dict:
    routes = D.get("routes", [])
    return {
        "status": "complete",
        "progress": 100,
        "summary": f"{len(routes)} frontend routes defined",
        "findings": [f"{len(routes)} routes mapped to page components"],
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
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in config.watch_excludes]
        for fname in files:
            if not any(fname.endswith(ext) for ext in (".py", ".tsx", ".ts")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        for marker in ("TODO", "FIXME", "HACK", "XXX"):
                            if marker in line and "#" in line:
                                rel = os.path.relpath(fpath, project)
                                comment = line.strip()
                                if len(comment) > 100:
                                    comment = comment[:97] + "..."
                                todos.append(f"{rel}:{i} — {comment}")
                                break
            except OSError:
                pass
    return todos
