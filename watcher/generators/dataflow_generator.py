import re
from pathlib import Path

from watcher.change_tracker import Change
from watcher.config import Config
from watcher.generators.base_generator import BaseGenerator
from watcher.parsers.python_parser import parse_routes_file
from watcher.parsers.typescript_parser import parse_tsx_file
from watcher.utils import markdown_writer as md


class DataflowGenerator(BaseGenerator):
    """Doc 3: Dataflow Documentation — request flows from frontend to storage."""

    def __init__(self, config: Config):
        super().__init__(config)

    @property
    def filename(self) -> str:
        return "03_DATAFLOW.md"

    @property
    def trigger_patterns(self) -> list[str]:
        return self.config.dataflow_patterns

    def initial_scan(self) -> str:
        return self._build()

    def update(self, changes: list[Change]) -> str | None:
        return self._build()

    @property
    def _profile(self):
        return self.config.profile

    def _build(self) -> str:
        content = md.heading(1, "Dataflow Documentation")
        content += f"> Auto-generated on {md.timestamp()} | Documentation Watcher\n\n"
        content += md.divider()

        # Overview diagram
        content += self._build_system_overview_diagram()

        # Frontend → Backend flows
        content += self._build_frontend_api_flows()

        # Backend route → service → storage flows
        content += self._build_backend_flows()

        # Storage layer
        content += self._build_storage_flows()

        return content

    def _build_frontend_api_flows(self) -> str:
        section = md.heading(2, "Frontend → Backend API Calls")

        if not self._profile or not self._profile.frontend_pages_dir:
            if not self._profile or not self._profile.has_frontend:
                return section + "_No frontend detected._\n\n"
            return section + "_No pages directory found._\n\n"

        pages_dir = self.project / self._profile.frontend_pages_dir
        if not pages_dir.is_dir():
            return section + "_Pages directory not found._\n\n"

        all_rows = []
        for page_file in sorted(pages_dir.glob("*.tsx")) + sorted(pages_dir.glob("*.jsx")) + sorted(pages_dir.glob("*.vue")):
            parsed = parse_tsx_file(page_file)
            api_calls = parsed.get("api_calls", [])
            if api_calls:
                for call in api_calls:
                    all_rows.append([page_file.stem, call["method"], f"`{call['url']}`"])

        if all_rows:
            section += md.table(["Page", "Method", "API Endpoint"], all_rows)
        else:
            section += "_No direct API calls found in page files._\n\n"

        # Also check for a frontend services directory
        if self._profile.frontend_root:
            for svc_name in ["services", "api", "lib"]:
                svc_dir = self.project / self._profile.frontend_root / svc_name
                if svc_dir.is_dir():
                    section += md.heading(3, "Frontend Service Layer")
                    for svc_file in sorted(svc_dir.glob("*.ts")) + sorted(svc_dir.glob("*.tsx")) + sorted(svc_dir.glob("*.js")):
                        parsed = parse_tsx_file(svc_file)
                        api_calls = parsed.get("api_calls", [])
                        if api_calls:
                            rows = [[call["method"], f"`{call['url']}`"] for call in api_calls]
                            section += f"**{svc_file.name}**\n\n"
                            section += md.table(["Method", "Endpoint"], rows)
                    break

        return section

    def _build_backend_flows(self) -> str:
        section = md.heading(2, "Backend Request Flows")

        if not self._profile or not self._profile.routes_dirs:
            return section + "_No routes directory detected._\n\n"

        for route_dir_rel in self._profile.routes_dirs:
            route_dir = self.project / route_dir_rel
            if not route_dir.is_dir():
                continue

            for route_file in sorted(route_dir.glob("*.py")):
                if route_file.name == "__init__.py":
                    continue

                module = route_file.stem
                feature_name = module.replace("_", " ").title()
                routes = parse_routes_file(route_file)
                if not routes:
                    continue

                # Read source for service/storage references
                rel_path = str(route_file.relative_to(self.project))
                source = self._read_file(rel_path)

                # Find service calls (generic pattern)
                service_calls = set(re.findall(r"(\w+_service|\w+_store|\w+_client|\w+_repo)\.\w+", source))

                # Find storage references (generic)
                storage_refs = []
                if any(kw in source for kw in ["db.add", "db.query", "db.execute", "session.add", "session.query", ".save(", ".create(", ".filter("]):
                    storage_refs.append("Database")
                if any(kw in source.lower() for kw in ["minio", "s3", "put_object", "get_object", "upload_file", "boto3"]):
                    storage_refs.append("Object Storage")
                if any(kw in source.lower() for kw in ["redis", "valkey", "cache", "xadd", "publish"]):
                    storage_refs.append("Cache/Queue")
                if "publish_event" in source or "emit_event" in source:
                    storage_refs.append("Event System")

                section += md.heading(3, feature_name)

                rows = [[r["method"], f"`{r['path']}`", r["function"]] for r in routes]
                section += md.table(["Method", "Path", "Handler"], rows)

                if service_calls:
                    section += f"**Services used:** {', '.join(sorted(service_calls))}\n\n"
                if storage_refs:
                    section += f"**Storage:** {', '.join(sorted(set(storage_refs)))}\n\n"

        return section

    def _build_storage_flows(self) -> str:
        section = md.heading(2, "Storage Layer Summary")

        if not self._profile:
            return section + "_No profile available._\n\n"

        # Auto-detect storage from docker-compose
        from watcher.parsers.python_parser import parse_docker_compose
        storage_rows = []

        if self._profile.docker_compose:
            compose_path = self.project / self._profile.docker_compose
            if compose_path.is_file():
                services = parse_docker_compose(compose_path)
                for svc in services:
                    image = (svc.get("image") or "").lower()
                    name = svc["name"]
                    ports = ", ".join(svc["ports"]) if svc["ports"] else "—"

                    # Identify storage services
                    purpose = ""
                    if "postgres" in image or "mysql" in image or "mariadb" in image:
                        purpose = "Relational database"
                    elif "mongo" in image:
                        purpose = "Document database"
                    elif "redis" in image or "valkey" in image:
                        purpose = "Cache / message broker"
                    elif "minio" in image or "s3" in image:
                        purpose = "Object storage"
                    elif "rabbit" in image:
                        purpose = "Message queue"
                    elif "kafka" in image:
                        purpose = "Event streaming"
                    elif "elastic" in image:
                        purpose = "Search engine"

                    if purpose:
                        storage_rows.append([name, ports, purpose])

        if storage_rows:
            section += md.table(["Store", "Port", "Purpose"], storage_rows)
        else:
            section += "_No storage services detected in docker-compose. Storage may be configured externally._\n\n"

        return section

    def _build_system_overview_diagram(self) -> str:
        section = md.heading(2, "System Overview")

        if not self._profile:
            return section + "_No profile available._\n\n"

        lines = ["graph LR"]

        # Frontend
        if self._profile.has_frontend:
            fe_fw = "Frontend"
            for fw in self._profile.frameworks:
                if fw in ("React", "Vue", "Angular", "Svelte", "Next.js"):
                    fe_fw = fw
                    break
            lines.append("    subgraph Frontend")
            lines.append(f'        FE["{fe_fw}"]')
            lines.append("    end")

        # Backend
        if self._profile.has_backend:
            be_fw = "API"
            for fw in self._profile.frameworks:
                if fw in ("FastAPI", "Django", "Flask", "Express", "Gin"):
                    be_fw = fw
                    break
            lines.append("    subgraph Backend")
            lines.append(f'        API["{be_fw}"]')
            lines.append("    end")

        # Storage (from docker services)
        from watcher.parsers.python_parser import parse_docker_compose
        storage_nodes = []
        if self._profile.docker_compose:
            compose_path = self.project / self._profile.docker_compose
            if compose_path.is_file():
                services = parse_docker_compose(compose_path)
                storage_services = []
                for svc in services:
                    image = (svc.get("image") or "").lower()
                    if any(db in image for db in ["postgres", "mysql", "mongo", "redis", "valkey", "minio", "rabbit", "kafka", "elastic"]):
                        storage_services.append(svc)

                if storage_services:
                    lines.append("    subgraph Storage")
                    for svc in storage_services:
                        node_id = svc["name"].replace("-", "_").replace(".", "_")
                        display = svc["name"].replace("-", " ").replace("_", " ").title()
                        lines.append(f'        {node_id}[("{display}")]')
                        storage_nodes.append(node_id)
                    lines.append("    end")

        # Connections
        if self._profile.has_frontend and self._profile.has_backend:
            lines.append("    FE -->|REST API| API")
        if self._profile.has_backend:
            for sn in storage_nodes:
                lines.append(f"    API --> {sn}")

        if len(lines) > 1:
            section += md.mermaid("\n".join(lines))
        else:
            section += "_No system components detected for diagram._\n\n"

        return section
