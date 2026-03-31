"""
Project Detector — Auto-detects project structure, languages, and frameworks.

Scans a target directory and produces a ProjectProfile that replaces all
hardcoded path assumptions. Every generator and analyzer uses this profile
to discover where to look for models, routes, frontend code, etc.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProjectProfile:
    """Auto-detected project profile."""

    name: str = ""
    languages: dict[str, int] = field(default_factory=dict)
    frameworks: list[str] = field(default_factory=list)
    project_type: str = "unknown"  # fullstack, backend, frontend, library, monorepo

    # Discovered paths (relative to project root)
    models_files: list[str] = field(default_factory=list)
    routes_dirs: list[str] = field(default_factory=list)
    services_dirs: list[str] = field(default_factory=list)
    docker_compose: str | None = None
    frontend_entry: str | None = None
    frontend_pages_dir: str | None = None
    frontend_components_dir: str | None = None
    frontend_root: str | None = None

    # Auto-detected domain rules
    domain_rules: dict[str, str] = field(default_factory=dict)

    # Trigger patterns
    architecture_patterns: list[str] = field(default_factory=list)
    dataflow_patterns: list[str] = field(default_factory=list)
    event_patterns: list[str] = field(default_factory=list)
    visual_patterns: list[str] = field(default_factory=list)

    # Feature flags
    has_frontend: bool = False
    has_backend: bool = False
    has_docker: bool = False
    has_database_models: bool = False
    has_api_endpoints: bool = False
    has_event_system: bool = False
    has_git: bool = False


# Language extension mapping
EXT_TO_LANG = {
    ".py": "Python",
    ".tsx": "TypeScript (TSX)",
    ".ts": "TypeScript",
    ".jsx": "JavaScript (JSX)",
    ".js": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".vue": "Vue",
    ".svelte": "Svelte",
}

# Directories to always skip
SKIP_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "env", "node_modules",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".next", ".nuxt", ".output", "target", "bin", "obj",
    ".idea", ".vscode", ".DS_Store",
}

# Common code file extensions for scanning
CODE_EXTENSIONS = set(EXT_TO_LANG.keys()) | {".md", ".yml", ".yaml", ".json", ".toml", ".cfg"}


def detect_project(project_path: Path, excludes: list[str] | None = None) -> ProjectProfile:
    """Scan a project directory and return a complete ProjectProfile."""
    profile = ProjectProfile()
    skip = SKIP_DIRS | set(excludes or [])

    # 1. Detect project name
    profile.name = _detect_name(project_path)

    # 2. Scan file tree — languages, paths, patterns
    _scan_tree(project_path, profile, skip)

    # 3. Detect frameworks
    _detect_frameworks(project_path, profile)

    # 4. Discover key paths
    _discover_paths(project_path, profile, skip)

    # 5. Set feature flags
    profile.has_frontend = profile.frontend_entry is not None or profile.frontend_pages_dir is not None
    profile.has_backend = bool(profile.routes_dirs) or bool(profile.models_files)
    profile.has_docker = profile.docker_compose is not None
    profile.has_database_models = bool(profile.models_files)
    profile.has_api_endpoints = bool(profile.routes_dirs)
    profile.has_event_system = _detect_event_system(project_path, profile, skip)
    profile.has_git = (project_path / ".git").is_dir()

    # 6. Determine project type
    if profile.has_frontend and profile.has_backend:
        profile.project_type = "fullstack"
    elif profile.has_frontend:
        profile.project_type = "frontend"
    elif profile.has_backend:
        profile.project_type = "backend"
    else:
        profile.project_type = "library"

    # 7. Build domain rules from directory structure
    if not profile.domain_rules:
        profile.domain_rules = _build_domain_rules(project_path, profile, skip)

    # 8. Build trigger patterns
    _build_trigger_patterns(profile)

    return profile


def _detect_name(project_path: Path) -> str:
    """Detect project name from config files or directory name."""
    # Try package.json
    pkg_json = project_path / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            if data.get("name"):
                return data["name"]
        except (json.JSONDecodeError, OSError):
            pass

    # Try pyproject.toml
    pyproject = project_path / "pyproject.toml"
    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8")
            match = re.search(r'name\s*=\s*"([^"]+)"', content)
            if match:
                return match.group(1)
        except OSError:
            pass

    # Try setup.py
    setup_py = project_path / "setup.py"
    if setup_py.is_file():
        try:
            content = setup_py.read_text(encoding="utf-8")
            match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                return match.group(1)
        except OSError:
            pass

    # Try setup.cfg
    setup_cfg = project_path / "setup.cfg"
    if setup_cfg.is_file():
        try:
            content = setup_cfg.read_text(encoding="utf-8")
            match = re.search(r'name\s*=\s*(.+)', content)
            if match:
                return match.group(1).strip()
        except OSError:
            pass

    # Try Cargo.toml (Rust)
    cargo = project_path / "Cargo.toml"
    if cargo.is_file():
        try:
            content = cargo.read_text(encoding="utf-8")
            match = re.search(r'name\s*=\s*"([^"]+)"', content)
            if match:
                return match.group(1)
        except OSError:
            pass

    # Fall back to directory name
    name = project_path.name
    # Clean up common suffixes
    for suffix in ("_v0.1", "_v0.2", "_v1", "-main", "-master", "-dev"):
        if name.lower().endswith(suffix):
            name = name[:-len(suffix)]
    return name.replace("_", " ").replace("-", " ").title()


def _scan_tree(project_path: Path, profile: ProjectProfile, skip: set[str]):
    """Walk the file tree to count languages."""
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in skip]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            lang = EXT_TO_LANG.get(ext)
            if not lang:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    line_count = sum(1 for _ in f)
                profile.languages[lang] = profile.languages.get(lang, 0) + line_count
            except OSError:
                pass


def _detect_frameworks(project_path: Path, profile: ProjectProfile):
    """Detect frameworks from config files and imports."""
    frameworks = set()

    # Check package.json for JS/TS frameworks
    pkg_json = project_path / "package.json"
    # Also check subdirectories for monorepo-style projects
    pkg_paths = [pkg_json]
    for d in project_path.iterdir():
        if d.is_dir() and d.name not in SKIP_DIRS:
            sub_pkg = d / "package.json"
            if sub_pkg.is_file():
                pkg_paths.append(sub_pkg)

    for pkg_path in pkg_paths:
        if not pkg_path.is_file():
            continue
        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
            deps = {}
            deps.update(data.get("dependencies", {}))
            deps.update(data.get("devDependencies", {}))

            if "react" in deps:
                frameworks.add("React")
            if "vue" in deps:
                frameworks.add("Vue")
            if "@angular/core" in deps:
                frameworks.add("Angular")
            if "svelte" in deps:
                frameworks.add("Svelte")
            if "next" in deps:
                frameworks.add("Next.js")
            if "nuxt" in deps:
                frameworks.add("Nuxt")
            if "express" in deps:
                frameworks.add("Express")
            if "antd" in deps:
                frameworks.add("Ant Design")
            if "@mui/material" in deps:
                frameworks.add("Material UI")
            if "@chakra-ui/react" in deps:
                frameworks.add("Chakra UI")
            if "tailwindcss" in deps:
                frameworks.add("Tailwind CSS")
            if "prisma" in deps or "@prisma/client" in deps:
                frameworks.add("Prisma")
        except (json.JSONDecodeError, OSError):
            pass

    # Check Python requirements/config for Python frameworks
    for req_file in ["requirements.txt", "requirements/base.txt", "requirements/prod.txt"]:
        req_path = project_path / req_file
        if req_path.is_file():
            try:
                content = req_path.read_text(encoding="utf-8").lower()
                if "fastapi" in content:
                    frameworks.add("FastAPI")
                if "django" in content:
                    frameworks.add("Django")
                if "flask" in content:
                    frameworks.add("Flask")
                if "sqlalchemy" in content:
                    frameworks.add("SQLAlchemy")
                if "sqlmodel" in content:
                    frameworks.add("SQLModel")
                if "tortoise" in content:
                    frameworks.add("Tortoise ORM")
                if "peewee" in content:
                    frameworks.add("Peewee")
                if "celery" in content:
                    frameworks.add("Celery")
                if "dramatiq" in content:
                    frameworks.add("Dramatiq")
            except OSError:
                pass

    # Check pyproject.toml dependencies
    pyproject = project_path / "pyproject.toml"
    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8").lower()
            if "fastapi" in content:
                frameworks.add("FastAPI")
            if "django" in content:
                frameworks.add("Django")
            if "flask" in content:
                frameworks.add("Flask")
            if "sqlalchemy" in content:
                frameworks.add("SQLAlchemy")
        except OSError:
            pass

    # Check for Go modules
    go_mod = project_path / "go.mod"
    if go_mod.is_file():
        try:
            content = go_mod.read_text(encoding="utf-8")
            if "gin-gonic" in content:
                frameworks.add("Gin")
            if "echo" in content:
                frameworks.add("Echo")
            if "fiber" in content:
                frameworks.add("Fiber")
        except OSError:
            pass

    # Check for Docker
    if (project_path / "docker-compose.yml").is_file() or (project_path / "docker-compose.yaml").is_file():
        frameworks.add("Docker")
    if list(project_path.glob("Dockerfile*")):
        frameworks.add("Docker")

    # Check for Django by looking for manage.py
    if (project_path / "manage.py").is_file():
        frameworks.add("Django")

    profile.frameworks = sorted(frameworks)


def _discover_paths(project_path: Path, profile: ProjectProfile, skip: set[str]):
    """Discover key file paths within the project."""

    # Docker compose
    for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]:
        if (project_path / name).is_file():
            profile.docker_compose = name
            break

    # Find model files (ORM models)
    model_patterns = [
        "models.py", "model.py", "entities.py",
    ]
    model_dir_patterns = ["models", "entities"]

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in skip]
        rel_root = os.path.relpath(root, project_path)
        if rel_root == ".":
            rel_root = ""

        for fname in files:
            if fname in model_patterns:
                rel = os.path.join(rel_root, fname) if rel_root else fname
                # Verify it actually contains model definitions
                fpath = os.path.join(root, fname)
                try:
                    content = open(fpath, encoding="utf-8", errors="replace").read()
                    # Check for ORM patterns
                    if any(p in content for p in [
                        "Column(", "models.Model", "Base)", "Model)", "Field(",
                        "db.Column", "Table(", "mapped_column",
                    ]):
                        profile.models_files.append(rel)
                except OSError:
                    pass

        # Check for model directories
        for dname in dirs:
            if dname in model_dir_patterns:
                model_dir = os.path.join(root, dname)
                for mf in os.listdir(model_dir):
                    if mf.endswith(".py") and mf != "__init__.py":
                        rel = os.path.join(rel_root, dname, mf) if rel_root else os.path.join(dname, mf)
                        profile.models_files.append(rel)

    # Find route/endpoint directories
    route_dir_patterns = ["routes", "api", "endpoints", "views", "controllers", "routers"]
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in skip]
        for dname in dirs:
            if dname in route_dir_patterns:
                rel = os.path.relpath(os.path.join(root, dname), project_path)
                # Verify it contains actual route files
                dpath = os.path.join(root, dname)
                has_routes = False
                for rf in os.listdir(dpath):
                    if rf.endswith(".py") and rf != "__init__.py":
                        try:
                            content = open(os.path.join(dpath, rf), encoding="utf-8", errors="replace").read()
                            if any(p in content for p in [
                                "@router.", "@app.", "APIRouter", "Blueprint",
                                "urlpatterns", "path(", "Route(", ".get(", ".post(",
                            ]):
                                has_routes = True
                                break
                        except OSError:
                            pass
                if has_routes:
                    profile.routes_dirs.append(rel)

    # Find service directories
    service_dir_patterns = ["services", "service"]
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in skip]
        for dname in dirs:
            if dname in service_dir_patterns:
                rel = os.path.relpath(os.path.join(root, dname), project_path)
                profile.services_dirs.append(rel)

    # Find frontend entry points
    entry_patterns = ["App.tsx", "App.jsx", "App.vue", "App.svelte", "app.tsx", "app.jsx"]
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in skip]
        for fname in files:
            if fname in entry_patterns:
                rel = os.path.relpath(os.path.join(root, fname), project_path)
                profile.frontend_entry = rel
                # Derive frontend root
                frontend_src = os.path.dirname(rel)
                profile.frontend_root = frontend_src
                break
        if profile.frontend_entry:
            break

    # Find frontend pages and components directories
    page_dir_patterns = ["pages", "views", "screens"]
    comp_dir_patterns = ["components"]

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in skip]
        rel_root = os.path.relpath(root, project_path)

        for dname in dirs:
            dpath = os.path.join(root, dname)
            # Only match frontend directories (should contain tsx/jsx/vue files)
            has_frontend_files = any(
                f.endswith(('.tsx', '.jsx', '.vue', '.svelte'))
                for f in os.listdir(dpath) if os.path.isfile(os.path.join(dpath, f))
            )
            if not has_frontend_files:
                continue

            rel = os.path.relpath(dpath, project_path)
            if dname in page_dir_patterns and not profile.frontend_pages_dir:
                profile.frontend_pages_dir = rel
            if dname in comp_dir_patterns and not profile.frontend_components_dir:
                profile.frontend_components_dir = rel


def _detect_event_system(project_path: Path, profile: ProjectProfile, skip: set[str]) -> bool:
    """Check if the project has an event/messaging system."""
    event_patterns = [
        "publish_event", "emit_event", "dispatch_event", "EventEmitter",
        "event_bus", "message_queue", "celery", "dramatiq", "rq",
        "publish(", "emit(", "dispatch(",
    ]
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in skip]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            try:
                content = open(fpath, encoding="utf-8", errors="replace").read(10000)
                if any(p in content for p in event_patterns):
                    return True
            except OSError:
                pass
    return False


def _build_domain_rules(project_path: Path, profile: ProjectProfile, skip: set[str]) -> dict[str, str]:
    """Build domain classification rules from directory structure."""
    rules = {}

    # Get top-level directories with code files
    for item in sorted(project_path.iterdir()):
        if not item.is_dir() or item.name in skip or item.name.startswith("."):
            continue

        # Check if directory has any code files
        has_code = False
        for root, dirs, files in os.walk(item):
            dirs[:] = [d for d in dirs if d not in skip]
            if any(os.path.splitext(f)[1] in CODE_EXTENSIONS for f in files):
                has_code = True
                break

        if has_code:
            rel = item.name
            label = _dir_to_label(rel, profile)
            rules[rel + "/"] = label

    # Add specific file rules for known important files
    for name in ["docker-compose.yml", "docker-compose.yaml", "Dockerfile"]:
        if (project_path / name).is_file():
            rules[name] = "Infrastructure"

    # Add second-level directories for known structures
    for routes_dir in profile.routes_dirs:
        rules[routes_dir + "/"] = _dir_to_label(routes_dir, profile)
    for svc_dir in profile.services_dirs:
        rules[svc_dir + "/"] = _dir_to_label(svc_dir, profile)
    for models_file in profile.models_files:
        rules[models_file] = "Database Models"
    if profile.frontend_pages_dir:
        rules[profile.frontend_pages_dir + "/"] = "Frontend Pages"
    if profile.frontend_components_dir:
        rules[profile.frontend_components_dir + "/"] = "Frontend Components"
    if profile.frontend_entry:
        rules[profile.frontend_entry] = "Frontend Core"

    return rules


def _dir_to_label(rel_path: str, profile: ProjectProfile) -> str:
    """Convert a directory path to a human-readable domain label."""
    parts = rel_path.replace("\\", "/").split("/")
    name = parts[-1] if parts else rel_path

    # Common directory name mappings
    label_map = {
        "src": "Source",
        "lib": "Library",
        "app": "Application",
        "api": "API",
        "routes": "Routes",
        "views": "Views",
        "controllers": "Controllers",
        "models": "Models",
        "services": "Services",
        "utils": "Utilities",
        "helpers": "Helpers",
        "middleware": "Middleware",
        "tests": "Tests",
        "test": "Tests",
        "spec": "Tests",
        "docs": "Documentation",
        "scripts": "Scripts",
        "config": "Configuration",
        "migrations": "Database Migrations",
        "alembic": "Database Migrations",
        "static": "Static Files",
        "public": "Public Assets",
        "assets": "Assets",
        "components": "Components",
        "pages": "Pages",
        "screens": "Screens",
        "hooks": "Hooks",
        "store": "State Store",
        "stores": "State Stores",
        "reducers": "Reducers",
        "actions": "Actions",
        "types": "Types",
        "interfaces": "Interfaces",
        "training": "Training",
        "inference": "Inference",
        "workers": "Workers",
        "tasks": "Tasks",
        "jobs": "Jobs",
        "plugins": "Plugins",
        "extensions": "Extensions",
        "templates": "Templates",
        "cmd": "Commands",
        "pkg": "Packages",
        "internal": "Internal",
    }

    if name.lower() in label_map:
        base_label = label_map[name.lower()]
    else:
        base_label = name.replace("_", " ").replace("-", " ").title()

    # Add parent context for nested dirs
    if len(parts) > 1:
        parent = parts[-2]
        if parent.lower() in ("app", "src", "server", "backend", "client", "frontend"):
            return f"Backend {base_label}" if parent.lower() in ("app", "server", "backend") else f"Frontend {base_label}"

    return base_label


def _build_trigger_patterns(profile: ProjectProfile):
    """Build per-document trigger patterns from discovered paths."""
    # Architecture: models, routes, services, docker
    arch = []
    arch.extend(profile.models_files)
    for d in profile.routes_dirs:
        arch.append(d + "/")
    for d in profile.services_dirs:
        arch.append(d + "/")
    if profile.docker_compose:
        arch.append(profile.docker_compose)
    profile.architecture_patterns = arch

    # Dataflow: routes, services, frontend pages, models
    df = []
    for d in profile.routes_dirs:
        df.append(d + "/")
    for d in profile.services_dirs:
        df.append(d + "/")
    if profile.frontend_pages_dir:
        df.append(profile.frontend_pages_dir + "/")
    df.extend(profile.models_files)
    profile.dataflow_patterns = df

    # Event/Audit: routes, services, models
    evt = []
    for d in profile.services_dirs:
        evt.append(d + "/")
    for d in profile.routes_dirs:
        evt.append(d + "/")
    evt.extend(profile.models_files)
    profile.event_patterns = evt

    # Visual: frontend entry, pages, components
    vis = []
    if profile.frontend_entry:
        vis.append(profile.frontend_entry)
    if profile.frontend_pages_dir:
        vis.append(profile.frontend_pages_dir + "/")
    if profile.frontend_components_dir:
        vis.append(profile.frontend_components_dir + "/")
    profile.visual_patterns = vis
