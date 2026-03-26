from pathlib import Path

from watcher.change_tracker import Change
from watcher.config import Config
from watcher.generators.base_generator import BaseGenerator
from watcher.parsers.typescript_parser import parse_app_tsx, parse_tsx_file
from watcher.utils import markdown_writer as md


class VisualDesignGenerator(BaseGenerator):
    """Doc 5: Visual Design Log & Layout — frontend routes, pages, components."""

    def __init__(self, config: Config):
        super().__init__(config)

    @property
    def filename(self) -> str:
        return "05_VISUAL_DESIGN_LOG.md"

    @property
    def trigger_patterns(self) -> list[str]:
        return self.config.visual_patterns

    def initial_scan(self) -> str:
        return self._build()

    def update(self, changes: list[Change]) -> str | None:
        return self._build()

    def _build(self) -> str:
        content = md.heading(1, "Visual Design Log & Layout")
        content += f"> Auto-generated on {md.timestamp()} | Optoz AI Documentation Watcher\n\n"
        content += md.divider()

        # Navigation / Routing
        content += self._build_navigation()

        # Per-page breakdown
        content += self._build_page_breakdown()

        # Shared components
        content += self._build_shared_components()

        return content

    def _build_navigation(self) -> str:
        section = md.heading(2, "Navigation & Routing")

        app_tsx = self.project / "my-app" / "src" / "App.tsx"
        if not app_tsx.is_file():
            return section + "_App.tsx not found._\n\n"

        parsed = parse_app_tsx(app_tsx)

        # Routes
        routes = parsed.get("routes", [])
        if routes:
            # Mermaid route map
            section += md.heading(3, "Route Map")
            mermaid_lines = ["graph LR"]
            mermaid_lines.append('    APP(("App")):::core')
            for r in routes:
                node_id = r["component"]
                path = r["path"]
                mermaid_lines.append(f'    {node_id}["{node_id}\\n{path}"]:::page')
                mermaid_lines.append(f"    APP --> {node_id}")
            mermaid_lines.append("    classDef core fill:#FFB74D,stroke:#F57C00,color:#000")
            mermaid_lines.append("    classDef page fill:#4FC3F7,stroke:#0288D1,color:#000")
            section += md.mermaid("\n".join(mermaid_lines))

            section += md.heading(3, "Route Definitions")
            rows = [[f"`{r['path']}`", r["component"]] for r in routes]
            section += md.table(["Path", "Component"], rows)

        # Menu items
        menu_items = parsed.get("menu_items", [])
        if menu_items:
            section += md.heading(3, "Sidebar Menu")
            rows = [[item["key"], item["icon"], item["label"]] for item in menu_items]
            section += md.table(["Key", "Icon", "Label"], rows)

        return section

    def _build_page_breakdown(self) -> str:
        section = md.heading(2, "Page Components")

        pages_dir = self.project / "my-app" / "src" / "pages"
        if not pages_dir.is_dir():
            return section + "_No pages directory._\n\n"

        for page_file in sorted(pages_dir.glob("*.tsx")):
            parsed = parse_tsx_file(page_file)

            section += md.heading(3, page_file.stem)

            # Components defined
            components = parsed.get("components", [])
            if components:
                section += f"**Components:** {', '.join(c['name'] for c in components)}\n\n"

            # Ant Design usage
            ant = parsed.get("ant_design", [])
            if ant:
                section += f"**Ant Design:** {', '.join(ant)}\n\n"

            # API calls
            api_calls = parsed.get("api_calls", [])
            if api_calls:
                rows = [[c["method"], f"`{c['url']}`"] for c in api_calls]
                section += md.table(["Method", "API Endpoint"], rows)

            # Size
            line_count = parsed.get("line_count", 0)
            section += f"_Lines: {line_count}_\n\n"

        return section

    def _build_shared_components(self) -> str:
        section = md.heading(2, "Shared Components")

        comp_dir = self.project / "my-app" / "src" / "components"
        if not comp_dir.is_dir():
            # Check if components are inline
            return section + "_No separate components directory — components are defined inline in page files._\n\n"

        rows = []
        for comp_file in sorted(comp_dir.rglob("*.tsx")):
            parsed = parse_tsx_file(comp_file)
            components = parsed.get("components", [])
            rel = str(comp_file.relative_to(self.project))
            for c in components:
                rows.append([c["name"], rel, str(parsed.get("line_count", 0))])

        if rows:
            section += md.table(["Component", "File", "Lines"], rows)
        else:
            section += "_No shared components found._\n\n"

        return section
