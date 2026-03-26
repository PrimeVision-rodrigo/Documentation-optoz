import ast
import re
from pathlib import Path


def parse_python_file(path: Path) -> dict:
    """Parse a Python file using AST to extract structural information."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    result = {
        "classes": [],
        "functions": [],
        "routes": [],
        "models": [],
        "events": [],
        "imports": [],
    }

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return result

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_info = _parse_class(node, source)
            result["classes"].append(class_info)
            # Check if it's a SQLAlchemy model
            for base in node.bases:
                base_name = _get_name(base)
                if base_name in ("Base", "DeclarativeBase"):
                    result["models"].append(class_info)

        elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            func_info = _parse_function(node, source)
            result["functions"].append(func_info)
            # Check for route decorators
            for dec in node.decorator_list:
                dec_str = _decorator_to_string(dec)
                if dec_str and any(m in dec_str for m in (".get", ".post", ".put", ".delete", ".patch")):
                    result["routes"].append({
                        "name": node.name,
                        "decorator": dec_str,
                        "line": node.lineno,
                        "args": [a.arg for a in node.args.args if a.arg != "self"],
                    })

        elif isinstance(node, ast.Assign):
            # Look for dict assignments like EVENT_TYPE_TO_AUDIT_ACTION
            for target in node.targets:
                if isinstance(target, ast.Name) and "EVENT" in target.id.upper():
                    if isinstance(node.value, ast.Dict):
                        result["events"].append({
                            "name": target.id,
                            "keys": [_get_constant(k) for k in node.value.keys if _get_constant(k)],
                            "line": node.lineno,
                        })

        elif isinstance(node, ast.Import):
            for alias in node.names:
                result["imports"].append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                result["imports"].append(node.module)

    # Also find publish_event calls via regex (more reliable for this pattern)
    result["publish_event_calls"] = _find_publish_event_calls(source)

    return result


def parse_models_file(path: Path) -> list[dict]:
    """Parse SQLAlchemy models file specifically for model definitions."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return []

    models = []
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # Check if it extends Base
        bases = [_get_name(b) for b in node.bases]
        if "Base" not in bases:
            continue

        columns = []
        tablename = None
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        if target.id == "__tablename__":
                            tablename = _get_constant(item.value)
                        else:
                            col_type = _infer_column_type(item.value)
                            columns.append({"name": target.id, "type": col_type})

        models.append({
            "name": node.name,
            "tablename": tablename,
            "columns": columns,
            "line": node.lineno,
        })

    return models


def parse_routes_file(path: Path) -> list[dict]:
    """Parse a FastAPI routes file for endpoint definitions."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return []

    routes = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            dec_str = _decorator_to_string(dec)
            if not dec_str:
                continue
            method = None
            endpoint_path = None
            for m in ("get", "post", "put", "delete", "patch"):
                if f".{m}(" in dec_str or f".{m}" == dec_str.split("(")[0].split(".")[-1]:
                    method = m.upper()
                    # Extract path argument
                    match = re.search(r'\("([^"]*)"', dec_str)
                    if match:
                        endpoint_path = match.group(1)
                    else:
                        endpoint_path = ""
                    break

            if method:
                routes.append({
                    "function": node.name,
                    "method": method,
                    "path": endpoint_path or "",
                    "line": node.lineno,
                    "args": [a.arg for a in node.args.args if a.arg not in ("self", "request", "db", "current_user")],
                })

    return routes


def parse_docker_compose(path: Path) -> list[dict]:
    """Parse docker-compose.yml for service definitions (regex-based)."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    services = []
    current_service = None
    indent_level = 0

    for line in content.splitlines():
        # Match service definitions (2-space indented under 'services:')
        svc_match = re.match(r"^  (\w[\w-]*):", line)
        if svc_match and not line.strip().startswith("#"):
            current_service = {"name": svc_match.group(1), "ports": [], "image": None, "depends_on": []}
            services.append(current_service)
            continue

        if current_service:
            stripped = line.strip()
            if stripped.startswith("image:"):
                current_service["image"] = stripped.split(":", 1)[1].strip().strip('"')
            elif re.match(r'^- "\d+:\d+"$', stripped) or re.match(r"^- \d+:\d+$", stripped):
                port = stripped.lstrip("- ").strip('"')
                current_service["ports"].append(port)

    return services


def _parse_class(node: ast.ClassDef, source: str) -> dict:
    methods = []
    attributes = []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(item.name)
        elif isinstance(item, ast.Assign):
            for t in item.targets:
                if isinstance(t, ast.Name):
                    attributes.append(t.id)
    return {
        "name": node.name,
        "bases": [_get_name(b) for b in node.bases],
        "methods": methods,
        "attributes": attributes,
        "line": node.lineno,
    }


def _parse_function(node, source: str) -> dict:
    return {
        "name": node.name,
        "args": [a.arg for a in node.args.args if a.arg != "self"],
        "line": node.lineno,
        "is_async": isinstance(node, ast.AsyncFunctionDef),
    }


def _decorator_to_string(dec) -> str | None:
    if isinstance(dec, ast.Call):
        func_str = _decorator_to_string(dec.func)
        if func_str:
            args = []
            for a in dec.args:
                c = _get_constant(a)
                if c is not None:
                    args.append(f'"{c}"')
            return f"{func_str}({', '.join(args)})"
    elif isinstance(dec, ast.Attribute):
        value_str = _decorator_to_string(dec.value)
        if value_str:
            return f"{value_str}.{dec.attr}"
        return dec.attr
    elif isinstance(dec, ast.Name):
        return dec.id
    return None


def _get_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _get_constant(node) -> str | None:
    if isinstance(node, ast.Constant):
        return str(node.value)
    return None


def _infer_column_type(node) -> str:
    """Try to get the column type from a Column() call."""
    if isinstance(node, ast.Call):
        func_name = _get_name(node.func)
        if func_name == "Column" and node.args:
            return _get_name(node.args[0])
        elif func_name == "mapped_column" and node.args:
            return _get_name(node.args[0])
        return func_name
    return "unknown"


def _find_publish_event_calls(source: str) -> list[dict]:
    """Find all publish_event() or publish_training_event() calls via regex."""
    calls = []
    for match in re.finditer(
        r'publish_(?:training_)?event\s*\(\s*[^,]*,?\s*["\'](\w+)["\']',
        source,
    ):
        calls.append({"event_type": match.group(1), "line": source[:match.start()].count("\n") + 1})

    # Also match: event_type="EventName" pattern
    for match in re.finditer(
        r'event_type\s*=\s*["\'](\w+)["\']',
        source,
    ):
        if match.group(1) not in [c["event_type"] for c in calls]:
            calls.append({"event_type": match.group(1), "line": source[:match.start()].count("\n") + 1})

    return calls
