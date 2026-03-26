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

    def _build(self) -> str:
        content = md.heading(1, "Architecture Documentation")
        content += f"> Auto-generated on {md.timestamp()} | Optoz AI Documentation Watcher\n\n"
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

        models_path = self.project / "app" / "models.py"
        if not models_path.is_file():
            return section + "_models.py not found._\n\n"

        models = parse_models_file(models_path)
        if not models:
            return section + "_No models found._\n\n"

        for model in models:
            section += md.heading(3, f"`{model['name']}` (table: `{model['tablename']}`)")
            if model["columns"]:
                rows = [[c["name"], c["type"]] for c in model["columns"]]
                section += md.table(["Column", "Type"], rows)

        return section

    def _build_endpoints_section(self) -> str:
        section = md.heading(2, "API Endpoints")

        route_dir = self.project / "app" / "routes"
        if not route_dir.is_dir():
            return section + "_No routes directory found._\n\n"

        for route_file in sorted(route_dir.glob("*.py")):
            if route_file.name == "__init__.py":
                continue

            routes = parse_routes_file(route_file)
            if not routes:
                continue

            module_name = route_file.stem
            section += md.heading(3, f"`{module_name}` routes")

            rows = []
            for r in routes:
                rows.append([r["method"], f"`{r['path']}`", r["function"], str(r["line"])])

            section += md.table(["Method", "Path", "Handler", "Line"], rows)

        return section

    def _build_services_section(self) -> str:
        section = md.heading(2, "Services Layer")

        services_dir = self.project / "app" / "services"
        if not services_dir.is_dir():
            return section + "_No services directory found._\n\n"

        rows = []
        for svc_file in sorted(services_dir.glob("*.py")):
            if svc_file.name == "__init__.py":
                continue

            try:
                source = svc_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            # Count functions and classes
            import re
            funcs = re.findall(r"^(?:async\s+)?def\s+(\w+)", source, re.MULTILINE)
            classes = re.findall(r"^class\s+(\w+)", source, re.MULTILINE)

            desc_parts = []
            if classes:
                desc_parts.append(f"Classes: {', '.join(classes)}")
            if funcs:
                public = [f for f in funcs if not f.startswith("_")]
                desc_parts.append(f"{len(public)} public function(s)")

            rows.append([
                f"`{svc_file.name}`",
                "; ".join(desc_parts) if desc_parts else "—",
            ])

        section += md.table(["File", "Contents"], rows)
        return section

    def _build_infrastructure_section(self) -> str:
        section = md.heading(2, "Docker Infrastructure")

        compose_path = self.project / "docker-compose.yml"
        if not compose_path.is_file():
            return section + "_docker-compose.yml not found._\n\n"

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
        section = md.heading(2, "Service Dependency Diagram")
        section += md.mermaid(
            "graph TD\n"
            "    FE[\"React Frontend\\n:5173\"]:::frontend\n"
            "    BE[\"FastAPI Backend\\n:8001\"]:::backend\n"
            "    TW[\"Training Worker\\nGPU\"]:::worker\n"
            "    PG[(\"PostgreSQL\\n:5432\")]:::storage\n"
            "    MIO[(\"MinIO S3\\n:9000\")]:::storage\n"
            "    VK[(\"Valkey\\n:6379\")]:::storage\n"
            "\n"
            "    FE -->|REST API| BE\n"
            "    BE -->|SQLAlchemy| PG\n"
            "    BE -->|S3 SDK| MIO\n"
            "    BE -->|Stream pub| VK\n"
            "    BE -->|Queue jobs| TW\n"
            "    TW -->|Read/Write| PG\n"
            "    TW -->|Store models| MIO\n"
            "    TW -->|Publish events| VK\n"
            "\n"
            "    classDef frontend fill:#4FC3F7,stroke:#0288D1,color:#000\n"
            "    classDef backend fill:#81C784,stroke:#388E3C,color:#000\n"
            "    classDef worker fill:#FFB74D,stroke:#F57C00,color:#000\n"
            "    classDef storage fill:#CE93D8,stroke:#7B1FA2,color:#000"
        )
        return section

    def _build_er_diagram(self) -> str:
        section = md.heading(2, "Entity Relationship Diagram")

        models_path = self.project / "app" / "models.py"
        if not models_path.is_file():
            return section

        models = parse_models_file(models_path)
        if not models:
            return section

        lines = ["erDiagram"]
        for model in models:
            entity = model["name"]
            lines.append(f"    {entity} {{")
            for col in model["columns"][:8]:  # Limit columns for readability
                col_type = col["type"] if col["type"] != "unknown" else "column"
                lines.append(f"        {col_type} {col['name']}")
            lines.append("    }")

        # Add relationships based on common FK patterns
        lines.append("    Project ||--o{ TrainingJob : has")
        lines.append("    Project ||--o{ LabeledImage : contains")
        lines.append("    Project ||--o{ DeploymentPackage : produces")
        lines.append("    Project ||--o{ CalibrationRecord : calibrates")
        lines.append("    LabeledImage ||--o{ DefectAnnotation : annotated_with")
        lines.append("    Event ||--o{ SystemAuditLog : projects_to")

        section += md.mermaid("\n".join(lines))
        return section
