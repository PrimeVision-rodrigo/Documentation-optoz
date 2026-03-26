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

    def _build(self) -> str:
        content = md.heading(1, "Dataflow Documentation")
        content += f"> Auto-generated on {md.timestamp()} | Optoz AI Documentation Watcher\n\n"
        content += md.divider()

        # Overview diagram
        content += self._build_system_overview_diagram()

        # Frontend → Backend flows
        content += self._build_frontend_api_flows()

        # Backend route → service → storage flows
        content += self._build_backend_flows()

        # Storage layer
        content += self._build_storage_flows()

        # Per-feature sequence diagrams
        content += self._build_feature_sequence_diagrams()

        return content

    def _build_frontend_api_flows(self) -> str:
        section = md.heading(2, "Frontend → Backend API Calls")

        pages_dir = self.project / "my-app" / "src" / "pages"
        if not pages_dir.is_dir():
            return section + "_No pages directory found._\n\n"

        all_rows = []
        for page_file in sorted(pages_dir.glob("*.tsx")):
            parsed = parse_tsx_file(page_file)
            api_calls = parsed.get("api_calls", [])
            if api_calls:
                for call in api_calls:
                    all_rows.append([page_file.stem, call["method"], f"`{call['url']}`"])

        if all_rows:
            section += md.table(["Page", "Method", "API Endpoint"], all_rows)
        else:
            section += "_No direct API calls found in page files._\n\n"

        # Also check for a services directory
        svc_dir = self.project / "my-app" / "src" / "services"
        if svc_dir.is_dir():
            section += md.heading(3, "Frontend Service Layer")
            for svc_file in sorted(svc_dir.glob("*.ts")):
                parsed = parse_tsx_file(svc_file)
                api_calls = parsed.get("api_calls", [])
                if api_calls:
                    rows = [[call["method"], f"`{call['url']}`"] for call in api_calls]
                    section += f"**{svc_file.name}**\n\n"
                    section += md.table(["Method", "Endpoint"], rows)

        return section

    def _build_backend_flows(self) -> str:
        section = md.heading(2, "Backend Request Flows")

        route_dir = self.project / "app" / "routes"
        if not route_dir.is_dir():
            return section + "_No routes directory._\n\n"

        features = {
            "projects": "Project Management",
            "capture": "Image Capture",
            "labeling": "Labeling & Annotation",
            "training": "Model Training",
            "inference": "Inference",
            "deployment": "Deployment",
            "audit": "Audit Trail",
            "production": "Production Monitoring",
            "calibration": "Calibration",
            "users": "User Management",
            "settings": "Settings",
            "sam2": "SAM2 Segmentation",
            "defect_validation": "Validation",
            "sample_projects": "Sample Projects",
            "system": "System Health",
            "auth": "Authentication",
        }

        for route_file in sorted(route_dir.glob("*.py")):
            if route_file.name == "__init__.py":
                continue

            module = route_file.stem
            feature_name = features.get(module, module.replace("_", " ").title())
            routes = parse_routes_file(route_file)
            if not routes:
                continue

            # Read source for service/storage references
            source = self._read_file(f"app/routes/{route_file.name}")

            # Find service calls
            service_calls = set(re.findall(r"(\w+_service|\w+_store|event_store|minio_client|vlk)\.\w+", source))
            # Find storage references
            storage_refs = []
            if "db.add" in source or "db.query" in source or "db.execute" in source:
                storage_refs.append("PostgreSQL")
            if "minio" in source.lower() or "put_object" in source or "get_object" in source:
                storage_refs.append("MinIO")
            if "vlk" in source or "valkey" in source.lower() or "xadd" in source:
                storage_refs.append("Valkey")
            if "publish_event" in source:
                storage_refs.append("Event Store (triple-write)")

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

        rows = [
            ["PostgreSQL", "5432", "Relational data: projects, training jobs, users, events, audit records, settings"],
            ["MinIO", "9000", "Object storage: captured images, trained models, deployment packages"],
            ["Valkey", "6379", "Event streaming: real-time event pub/sub, cache"],
        ]
        section += md.table(["Store", "Port", "Purpose"], rows)

        # Event sourcing triple-write
        section += md.heading(3, "Event Sourcing Triple-Write")
        section += "Every state change writes to three sinks atomically:\n\n"
        section += md.mermaid(
            "graph LR\n"
            "    PUB[\"publish_event()\"]:::fn\n"
            "    ES[(Event Store)]:::db\n"
            "    SAL[(SystemAuditLog)]:::db\n"
            "    VK[(Valkey Stream)]:::stream\n"
            "\n"
            "    PUB -->|hash-chain| ES\n"
            "    PUB -->|legacy projection| SAL\n"
            "    PUB -->|real-time| VK\n"
            "\n"
            "    classDef fn fill:#FFB74D,stroke:#F57C00,color:#000\n"
            "    classDef db fill:#CE93D8,stroke:#7B1FA2,color:#000\n"
            "    classDef stream fill:#4FC3F7,stroke:#0288D1,color:#000"
        )

        return section

    def _build_system_overview_diagram(self) -> str:
        section = md.heading(2, "System Overview")
        section += md.mermaid(
            "graph LR\n"
            "    subgraph Frontend\n"
            "        FE[\"React + Ant Design\\n:5173\"]\n"
            "    end\n"
            "    subgraph Backend\n"
            "        API[\"FastAPI\\n:8001\"]\n"
            "        TW[\"Training Worker\\nGPU\"]\n"
            "    end\n"
            "    subgraph Storage\n"
            "        PG[(PostgreSQL)]\n"
            "        MIO[(MinIO S3)]\n"
            "        VK[(Valkey)]\n"
            "    end\n"
            "\n"
            "    FE -->|REST /api/*| API\n"
            "    API --> PG\n"
            "    API --> MIO\n"
            "    API --> VK\n"
            "    API -.->|queue| TW\n"
            "    TW --> PG\n"
            "    TW --> MIO\n"
            "    TW --> VK"
        )
        return section

    def _build_feature_sequence_diagrams(self) -> str:
        section = md.heading(2, "Key Feature Flows")

        # Training flow
        section += md.heading(3, "Training Pipeline")
        section += md.mermaid(
            "sequenceDiagram\n"
            "    participant U as User\n"
            "    participant FE as Frontend\n"
            "    participant API as Backend API\n"
            "    participant DB as PostgreSQL\n"
            "    participant TW as Training Worker\n"
            "    participant S3 as MinIO\n"
            "    participant VK as Valkey\n"
            "\n"
            "    U->>FE: Configure & start training\n"
            "    FE->>API: POST /api/training\n"
            "    API->>DB: Create TrainingJob (QUEUED)\n"
            "    API->>VK: publish_event(TrainingJobCreated)\n"
            "    API-->>FE: 200 job_id\n"
            "    TW->>DB: Poll for QUEUED jobs\n"
            "    TW->>DB: Update status → RUNNING\n"
            "    TW->>S3: Load training images\n"
            "    TW->>TW: Anomalib training (GPU)\n"
            "    TW->>S3: Save trained model\n"
            "    TW->>DB: Update status → COMPLETED\n"
            "    TW->>VK: publish_event(TrainingJobCompleted)\n"
            "    FE->>API: Poll job status\n"
            "    API-->>FE: COMPLETED + metrics"
        )

        # Capture flow
        section += md.heading(3, "Image Capture")
        section += md.mermaid(
            "sequenceDiagram\n"
            "    participant U as User\n"
            "    participant FE as Frontend\n"
            "    participant API as Backend API\n"
            "    participant S3 as MinIO\n"
            "    participant DB as PostgreSQL\n"
            "    participant VK as Valkey\n"
            "\n"
            "    U->>FE: Capture image\n"
            "    FE->>API: POST /api/capture\n"
            "    API->>S3: Store image (bucket/project_id/)\n"
            "    API->>DB: Create AuditRecord\n"
            "    API->>VK: publish_event(ImageCaptured)\n"
            "    API-->>FE: 200 image_uuid"
        )

        return section
