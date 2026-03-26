import re
from pathlib import Path


def parse_tsx_file(path: Path) -> dict:
    """Parse a TSX/TS file using regex to extract structural information."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    return {
        "components": _find_components(source),
        "imports": _find_imports(source),
        "routes": _find_routes(source),
        "api_calls": _find_api_calls(source),
        "ant_design": _find_ant_design_usage(source),
        "menu_items": _find_menu_items(source),
        "line_count": source.count("\n") + 1,
    }


def parse_app_tsx(path: Path) -> dict:
    """Parse App.tsx specifically for routing and navigation structure."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    return {
        "routes": _find_route_elements(source),
        "menu_items": _find_menu_items(source),
        "imports": _find_imports(source),
    }


def _find_components(source: str) -> list[dict]:
    """Find React component definitions."""
    components = []

    # function ComponentName or const ComponentName = () =>
    for match in re.finditer(
        r"(?:export\s+)?(?:default\s+)?function\s+([A-Z]\w+)",
        source,
    ):
        components.append({"name": match.group(1), "type": "function", "line": source[: match.start()].count("\n") + 1})

    for match in re.finditer(
        r"(?:export\s+)?const\s+([A-Z]\w+)\s*(?::\s*\w+\s*)?=\s*\(",
        source,
    ):
        name = match.group(1)
        if name not in [c["name"] for c in components]:
            components.append({"name": name, "type": "arrow", "line": source[: match.start()].count("\n") + 1})

    return components


def _find_imports(source: str) -> list[dict]:
    """Find import statements."""
    imports = []
    for match in re.finditer(
        r"import\s+(?:type\s+)?(?:\{([^}]+)\}|(\w+))\s+from\s+['\"]([^'\"]+)['\"]",
        source,
    ):
        names = match.group(1) or match.group(2)
        module = match.group(3)
        imports.append({
            "names": [n.strip() for n in names.split(",") if n.strip()],
            "module": module,
        })
    return imports


def _find_routes(source: str) -> list[dict]:
    """Find React Router Route elements."""
    routes = []
    for match in re.finditer(
        r'<Route\s+path=["\']([^"\']+)["\']\s+element=\{<(\w+)',
        source,
    ):
        routes.append({"path": match.group(1), "component": match.group(2)})
    return routes


def _find_route_elements(source: str) -> list[dict]:
    """Find route definitions in various formats."""
    routes = []

    # <Route path="/foo" element={<Component />} />
    for match in re.finditer(
        r'<Route\s+path=["\']([^"\']+)["\']\s+element=\{<(\w+)',
        source,
    ):
        routes.append({"path": match.group(1), "component": match.group(2)})

    # Also match element before path
    for match in re.finditer(
        r'<Route\s+element=\{<(\w+)[^}]*\}\s+path=["\']([^"\']+)["\']',
        source,
    ):
        path = match.group(2)
        if path not in [r["path"] for r in routes]:
            routes.append({"path": path, "component": match.group(1)})

    return routes


def _find_api_calls(source: str) -> list[dict]:
    """Find API/fetch calls."""
    calls = []

    # axios or fetch patterns
    for match in re.finditer(
        r"(?:axios|fetch|api)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*[`'\"]([^`'\"]+)[`'\"]",
        source,
    ):
        calls.append({"method": match.group(1).upper(), "url": match.group(2)})

    # Template literal API calls
    for match in re.finditer(
        r"(?:axios|fetch|api)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*`([^`]+)`",
        source,
    ):
        url = match.group(2)
        if url not in [c["url"] for c in calls]:
            calls.append({"method": match.group(1).upper(), "url": url})

    # Direct fetch() calls
    for match in re.finditer(
        r"fetch\s*\(\s*[`'\"]([^`'\"]+)[`'\"]",
        source,
    ):
        url = match.group(1)
        if url not in [c["url"] for c in calls]:
            calls.append({"method": "GET", "url": url})

    return calls


def _find_ant_design_usage(source: str) -> list[str]:
    """Find Ant Design component usage."""
    components = set()
    for match in re.finditer(r"<(Table|Form|Modal|Button|Card|Layout|Menu|Tabs|Drawer|Select|Input|Tag|Badge|Space|Row|Col|Statistic|Descriptions|Steps|Upload|Result|Alert|Tooltip|Popconfirm|Spin|Progress|Typography|Divider|Collapse|Timeline|Transfer|Tree|Cascader)\b", source):
        components.add(match.group(1))
    return sorted(components)


def _find_menu_items(source: str) -> list[dict]:
    """Find sidebar/menu item definitions."""
    items = []
    # Pattern: { key: "N", icon: <Icon />, label: "Name" }
    for match in re.finditer(
        r'\{\s*key:\s*["\'](\w+)["\']\s*,\s*icon:\s*<(\w+)\s*/>\s*,\s*label:\s*["\']([^"\']+)["\']',
        source,
    ):
        items.append({"key": match.group(1), "icon": match.group(2), "label": match.group(3)})
    return items
