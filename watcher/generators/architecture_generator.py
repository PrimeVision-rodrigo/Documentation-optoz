import re
from pathlib import Path

from watcher.change_tracker import Change
from watcher.config import Config
from watcher.generators.base_generator import BaseGenerator
from watcher.parsers.python_parser import parse_models_file, parse_routes_file, parse_docker_compose
from watcher.utils import markdown_writer as md


class ArchitectureGenerator(BaseGenerator):
    """Doc 2: Architecture Documentation — models, endpoints, services, infra."""

    def __init__(self, config: Config):
        super().__init__(config)

    @property
    def filename(self) -> str:
        return "02_ARCHITECTURE.md"

    @property
    def trigger_patterns(self) -> list[str]:
        return self.config.architecture_patterns

    def initial_scan(self) -> str:
        return self._build()

    def update(self, changes: list[Change]) -> str | None:
        return self._build()

    @property
    def _profile(self):
        return self.config.profile

    def _build(self) -> str:
        content = md.heading(1, "Architecture Documentation")
        content += f"> Auto-generated on {md.timestamp()} | Documentation Watcher\n\n"
        content += md.divider()

        # Service dependency diagram
        content += self._build_dependency_diagram()

        # Database Models
        content += self._build_models_section()

        # ER Diagram
        content += self._build_er_diagram()

        # API Endpoints
        content += self._build_endpoints_section()

        # Services Layer
        content += self._build_services_section()

        # Docker Infrastructure
        content += self._build_infrastructure_section()

        return content

    def _build_models_section(self) -> str:
        section = md.heading(2, "Database Models")

        if not self._profile or not self._profile.models_files:
            return section + "_No model files detected._\n\n"

        found_any = False
        for models_rel in self._profile.models_files:
            models_path = self.project / models_rel
            if not models_path.is_file():
                continue

            models = parse_models_file(models_path)
            if not models:
                continue

            found_any = True
            if len(self._profile.models_files) > 1:
                section += md.heading(3, f"`{models_rel}`")

            for model in models:
                section += md.heading(3 if len(self._profile.models_files) == 1 else 4,
                                      f"`{model['name']}` (table: `{model['tablename']}`)")
                if model["columns"]:
                    rows = [[c["name"], c["type"]] for c in model["columns"]]
                    section += md.table(["Column", "Type"], rows)

        if not found_any:
            section += "_No models found in detected model files._\n\n"

        return section

    def _build_endpoints_section(self) -> str:
        section = md.heading(2, "API Endpoints")

        if not self._profile or not self._profile.routes_dirs:
            return section + "_No routes directory found._\n\n"

        found_any = False
        for route_dir_rel in self._profile.routes_dirs:
            route_dir = self.project / route_dir_rel
            if not route_dir.is_dir():
                continue

            for route_file in sorted(route_dir.glob("*.py")):
                if route_file.name == "__init__.py":
                    continue

                routes = parse_routes_file(route_file)
                if not routes:
                    continue

                found_any = True
                module_name = route_file.stem
                section += md.heading(3, f"`{module_name}` routes")

                rows = []
                for r in routes:
                    rows.append([r["method"], f"`{r['path']}`", r["function"], str(r["line"])])

                section += md.table(["Method", "Path", "Handler", "Line"], rows)

        if not found_any:
            section += "_No API endpoints found._\n\n"

        return section

    def _build_services_section(self) -> str:
        section = md.heading(2, "Services Layer")

        if not self._profile or not self._profile.services_dirs:
            return section + "_No services directory found._\n\n"

        rows = []
        for svc_dir_rel in self._profile.services_dirs:
            svc_dir = self.project / svc_dir_rel
            if not svc_dir.is_dir():
                continue

            for svc_file in sorted(svc_dir.glob("*.py")):
                if svc_file.name == "__init__.py":
                    continue

                try:
                    source = svc_file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

                funcs = re.findall(r"^(?:async\s+)?def\s+(\w+)", source, re.MULTILINE)
                classes = re.findall(r"^class\s+(\w+)", source, re.MULTILINE)

                desc_parts = []
                if classes:
                    desc_parts.append(f"Classes: {', '.join(classes)}")
                if funcs:
                    public = [f for f in funcs if not f.startswith("_")]
                    desc_parts.append(f"{len(public)} public function(s)")

                rel = str(svc_file.relative_to(self.project))
                rows.append([
                    f"`{rel}`",
                    "; ".join(desc_parts) if desc_parts else "—",
                ])

        if rows:
            section += md.table(["File", "Contents"], rows)
        else:
            section += "_No service files found._\n\n"

        return section

    def _build_infrastructure_section(self) -> str:
        section = md.heading(2, "Docker Infrastructure")

        if not self._profile or not self._profile.docker_compose:
            return section + "_No docker-compose file found._\n\n"

        compose_path = self.project / self._profile.docker_compose
        if not compose_path.is_file():
            return section + "_docker-compose file not found._\n\n"

        services = parse_docker_compose(compose_path)
        if not services:
            return section + "_No services parsed._\n\n"

        rows = []
        for svc in services:
            ports = ", ".join(svc["ports"]) if svc["ports"] else "—"
            image = svc["image"] or "build context"
            rows.append([f"`{svc['name']}`", image, ports])

        section += md.table(["Service", "Image", "Ports"], rows)

        # Also list Dockerfiles
        dockerfiles = sorted(self.project.glob("Dockerfile*"))
        if dockerfiles:
            section += md.heading(3, "Dockerfiles")
            section += md.bullet_list([f"`{d.name}`" for d in dockerfiles])

        return section

    def _build_dependency_diagram(self) -> str:
        """Build a dynamic service dependency diagram from detected components."""
        section = md.heading(2, "Service Dependency Diagram")

        if not self._profile:
            return section + "_Project profile not available._\n\n"

        lines = ["graph TD"]
        node_count = 0

        # Frontend
        if self._profile.has_frontend:
            fe_label = "Frontend"
            for fw in self._profile.frameworks:
                if fw in ("React", "Vue", "Angular", "Svelte", "Next.js", "Nuxt"):
                    fe_label = fw
                    break
            lines.append(f'    FE["{fe_label} Frontend"]:::frontend')
            node_count += 1

        # Backend
        if self._profile.has_backend:
            be_label = "Backend API"
            for fw in self._profile.frameworks:
                if fw in ("FastAPI", "Django", "Flask", "Express", "Gin", "Echo"):
                    be_label = f"{fw} Backend"
                    break
            lines.append(f'    BE["{be_label}"]:::backend')
            node_count += 1

        # Docker services (storage layer)
        storage_nodes = []
        if self._profile.has_docker and self._profile.docker_compose:
            compose_path = self.project / self._profile.docker_compose
            if compose_path.is_file():
                services = parse_docker_compose(compose_path)
                for svc in services:
                    name = svc["name"]
                    image = (svc.get("image") or "").lower()
                    # Identify storage/infrastructure services
                    if any(db in image for db in ["postgres", "mysql", "mariadb", "mongo", "redis", "valkey", "minio", "rabbit", "kafka", "elastic"]):
                        node_id = name.replace("-", "_").replace(".", "_")
                        port_str = f"\\n:{svc['ports'][0].split(':')[-1]}" if svc["ports"] else ""
                        display_name = name.replace("-", " ").replace("_", " ").title()
                        lines.append(f'    {node_id}[("{display_name}{port_str}")]:::storage')
                        storage_nodes.append(node_id)
                        node_count += 1

        # Connections
        if self._profile.has_frontend and self._profile.has_backend:
            lines.append("    FE -->|API| BE")
        if self._profile.has_backend:
            for sn in storage_nodes:
                lines.append(f"    BE --> {sn}")

        # Styling
        lines.append("")
        lines.append("    classDef frontend fill:#4FC3F7,stroke:#0288D1,color:#000")
        lines.append("    classDef backend fill:#81C784,stroke:#388E3C,color:#000")
        lines.append("    classDef worker fill:#FFB74D,stroke:#F57C00,color:#000")
        lines.append("    classDef storage fill:#CE93D8,stroke:#7B1FA2,color:#000")

        if node_count == 0:
            return section + "_No architecture components detected._\n\n"

        section += md.mermaid("\n".join(lines))
        return section

    def _build_er_diagram(self) -> str:
        section = md.heading(2, "Entity Relationship Diagram")

        if not self._profile or not self._profile.models_files:
            return section

        all_models = []
        for models_rel in self._profile.models_files:
            models_path = self.project / models_rel
            if not models_path.is_file():
                continue
            models = parse_models_file(models_path)
            all_models.extend(models)

        if not all_models:
            return section

        lines = ["erDiagram"]
        model_names = set()
        for model in all_models:
            entity = model["name"]
            model_names.add(entity)
            lines.append(f"    {entity} {{")
            for col in model["columns"][:8]:
                col_type = col["type"] if col["type"] != "unknown" else "column"
                lines.append(f"        {col_type} {col['name']}")
            lines.append("    }")

        # Auto-detect relationships from ForeignKey columns
        for model in all_models:
            for col in model["columns"]:
                col_name = col["name"]
                col_type_str = col.get("type", "")
                # Detect FK by naming convention (e.g., project_id → Project)
                if col_name.endswith("_id"):
                    target = col_name[:-3].title().replace("_", "")
                    # Check various capitalizations
                    for mn in model_names:
                        if mn.lower() == target.lower() and mn != model["name"]:
                            lines.append(f'    {mn} ||--o{{ {model["name"]} : has')
                            break

        section += md.mermaid("\n".join(lines))
        return section
