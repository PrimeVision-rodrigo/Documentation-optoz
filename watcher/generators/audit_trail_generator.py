import re
from pathlib import Path

from watcher.change_tracker import Change
from watcher.config import Config
from watcher.generators.base_generator import BaseGenerator
from watcher.parsers.provenance_parser import parse_provenance
from watcher.parsers.python_parser import parse_python_file
from watcher.utils import markdown_writer as md


class AuditTrailGenerator(BaseGenerator):
    """Doc 4: Audit Trail Data Flow — event types, sources, consumers."""

    def __init__(self, config: Config):
        super().__init__(config)

    @property
    def filename(self) -> str:
        return "04_AUDIT_TRAIL_DATAFLOW.md"

    @property
    def trigger_patterns(self) -> list[str]:
        return self.config.audit_patterns

    def initial_scan(self) -> str:
        return self._build()

    def update(self, changes: list[Change]) -> str | None:
        return self._build()

    def _build(self) -> str:
        content = md.heading(1, "Audit Trail Data Flow")
        content += f"> Auto-generated on {md.timestamp()} | Optoz AI Documentation Watcher\n\n"
        content += md.divider()

        # Event types registry
        content += self._build_event_registry()

        # Triple-write architecture
        content += self._build_triple_write()

        # Event sources by route
        content += self._build_event_sources()

        # Event store schema
        content += self._build_event_store_schema()

        # Training worker events
        content += self._build_training_events()

        # Image provenance chain
        content += self._build_provenance_chain()

        return content

    def _build_event_registry(self) -> str:
        section = md.heading(2, "Event Type Registry")

        # Parse event_publisher.py for EVENT_TYPE_TO_AUDIT_ACTION
        ep_path = "app/services/event_publisher.py"
        source = self._read_file(ep_path)
        if not source:
            return section + "_event_publisher.py not found._\n\n"

        # Extract event types from the dict
        event_types = []
        in_dict = False
        for line in source.splitlines():
            if "EVENT_TYPE_TO_AUDIT_ACTION" in line:
                in_dict = True
                continue
            if in_dict:
                if line.strip() == "}":
                    break
                match = re.match(r'\s*"(\w+)":\s*"(\w+)"', line)
                if match:
                    event_types.append([match.group(1), match.group(2)])

        if event_types:
            section += f"**{len(event_types)} event types registered**\n\n"
            section += md.table(["Event Type", "Audit Action"], event_types)
        else:
            section += "_Could not parse event types._\n\n"

        return section

    def _build_triple_write(self) -> str:
        section = md.heading(2, "Triple-Write Architecture")

        section += md.mermaid(
            "graph TD\n"
            "    CALL[\"publish_event(db, event_type,\\naggregate_type, aggregate_id, payload)\"]:::fn\n"
            "\n"
            "    ES[(\"Event Store\\n<i>events</i>\\nHash-chained, Immutable\")]:::primary\n"
            "    SAL[(\"SystemAuditLog\\n<i>system_audit_logs</i>\\nLegacy projection\")]:::secondary\n"
            "    VK[(\"Valkey Stream\\n<i>optoz:events</i>\\nReal-time pub/sub\")]:::stream\n"
            "\n"
            "    CALL -->|1. Append with hash chain| ES\n"
            "    CALL -->|2. Write audit record| SAL\n"
            "    CALL -->|3. XADD to stream| VK\n"
            "\n"
            "    classDef fn fill:#FFB74D,stroke:#F57C00,color:#000\n"
            "    classDef primary fill:#A5D6A7,stroke:#388E3C,color:#000\n"
            "    classDef secondary fill:#CE93D8,stroke:#7B1FA2,color:#000\n"
            "    classDef stream fill:#4FC3F7,stroke:#0288D1,color:#000"
        )

        section += md.heading(3, "Integrity Chain")
        section += md.mermaid(
            "sequenceDiagram\n"
            "    participant R as Route Handler\n"
            "    participant PUB as publish_event()\n"
            "    participant ES as Event Store\n"
            "    participant SAL as SystemAuditLog\n"
            "    participant VK as Valkey\n"
            "\n"
            "    R->>PUB: event_type, payload\n"
            "    PUB->>ES: Generate UUID4 event_id\n"
            "    PUB->>ES: Fetch previous_hash\n"
            "    PUB->>ES: Compute integrity_hash = SHA256(event_id + type + payload + prev_hash)\n"
            "    PUB->>ES: INSERT (append-only)\n"
            "    PUB->>SAL: INSERT audit record\n"
            "    PUB->>VK: XADD optoz:events\n"
            "    PUB-->>R: event_id"
        )

        section += "This forms an append-only, tamper-evident log (21 CFR Part 11 compliant).\n\n"

        return section

    def _build_event_sources(self) -> str:
        section = md.heading(2, "Event Sources by Route")

        route_dir = self.project / "app" / "routes"
        if not route_dir.is_dir():
            return section + "_No routes directory._\n\n"

        rows = []
        for route_file in sorted(route_dir.glob("*.py")):
            if route_file.name == "__init__.py":
                continue

            source = self._read_file(f"app/routes/{route_file.name}")
            if "publish_event" not in source:
                continue

            # Find all publish_event calls and their event types
            events = []
            for match in re.finditer(r'event_type\s*=\s*"(\w+)"', source):
                events.append(match.group(1))

            # Also match positional argument pattern
            for match in re.finditer(r'publish_event\s*\([^,]+,\s*"(\w+)"', source):
                if match.group(1) not in events:
                    events.append(match.group(1))

            if events:
                for evt in sorted(set(events)):
                    rows.append([route_file.stem, evt])

        if rows:
            section += md.table(["Route Module", "Event Type"], rows)

            # Build Mermaid diagram of event sources
            route_events: dict[str, list[str]] = {}
            for route_mod, evt in rows:
                route_events.setdefault(route_mod, []).append(evt)

            mermaid_lines = ["graph LR"]
            for route_mod, events in route_events.items():
                node_id = route_mod.replace("_", "")
                mermaid_lines.append(f'    {node_id}["{route_mod}"]:::route')
                for evt in events[:3]:  # Cap at 3 per route for readability
                    evt_id = evt.replace(" ", "")
                    mermaid_lines.append(f'    {evt_id}(["{evt}"]):::event')
                    mermaid_lines.append(f"    {node_id} --> {evt_id}")
                if len(events) > 3:
                    more_id = f"{node_id}more"
                    mermaid_lines.append(f'    {more_id}(["...+{len(events)-3} more"]):::event')
                    mermaid_lines.append(f"    {node_id} --> {more_id}")

            mermaid_lines.append("    classDef route fill:#81C784,stroke:#388E3C,color:#000")
            mermaid_lines.append("    classDef event fill:#FFE082,stroke:#F9A825,color:#000")
            section += md.mermaid("\n".join(mermaid_lines))
        else:
            section += "_No publish_event calls found in routes._\n\n"

        return section

    def _build_event_store_schema(self) -> str:
        section = md.heading(2, "Event Store Schema")

        models_source = self._read_file("app/models.py")
        if not models_source:
            return section + "_models.py not found._\n\n"

        # Extract Event model columns
        in_event_class = False
        columns = []
        for line in models_source.splitlines():
            if re.match(r"^class Event\b", line):
                in_event_class = True
                continue
            if in_event_class:
                if re.match(r"^class \w", line):
                    break
                col_match = re.match(r"\s+(\w+)\s*=\s*Column\((\w+)", line)
                if col_match:
                    columns.append([col_match.group(1), col_match.group(2)])

        if columns:
            section += md.table(["Column", "Type"], columns)
        else:
            section += "_Could not parse Event model._\n\n"

        # Also show SystemAuditLog
        in_audit_class = False
        audit_columns = []
        for line in models_source.splitlines():
            if re.match(r"^class SystemAuditLog\b", line):
                in_audit_class = True
                continue
            if in_audit_class:
                if re.match(r"^class \w", line):
                    break
                col_match = re.match(r"\s+(\w+)\s*=\s*Column\((\w+)", line)
                if col_match:
                    audit_columns.append([col_match.group(1), col_match.group(2)])

        if audit_columns:
            section += md.heading(3, "SystemAuditLog Schema")
            section += md.table(["Column", "Type"], audit_columns)

        return section

    def _build_training_events(self) -> str:
        section = md.heading(2, "Training Worker Events")

        source = self._read_file("training/events.py")
        if not source:
            return section + "_training/events.py not found._\n\n"

        # Extract event types
        events = []
        for match in re.finditer(r'"(\w+)":\s*"(\w+)"', source):
            events.append([match.group(1), match.group(2)])

        if events:
            section += "The training worker publishes events independently (same triple-write pattern):\n\n"
            section += md.table(["Event Type", "Audit Action"], events)
        else:
            section += "_Could not parse training events._\n\n"

        return section

    def _build_provenance_chain(self) -> str:
        section = md.heading(2, "Image Provenance — Hash Chain Across Lifecycle")
        section += "_Parsed live from source code. Shows how image identity (hashes) flows through each stage._\n\n"

        prov = parse_provenance(self.project)
        if not prov["stages"]:
            return section + "_No provenance data detected._\n\n"

        # Build Mermaid graph
        mermaid_lines = ["graph LR"]

        for stage in prov["stages"]:
            has_activity = (
                stage["hash_computations"] or stage["hash_fields_stored"]
                or any(e["hash_fields"] for e in stage["events_published"])
                or stage["hash_reads"] or stage["chain_hashes"]
            )
            if not has_activity:
                continue

            sid = stage["id"]
            label_parts = [stage["label"]]
            if stage["hash_computations"]:
                label_parts.append("Computes: " + ", ".join(
                    c.split("→")[-1].strip() for c in stage["hash_computations"][:2]
                ))
            if stage["chain_hashes"]:
                label_parts.append("Chain hash")

            label = "\\n".join(label_parts)
            mermaid_lines.append(f'    {sid}["{label}"]:::stage')

        # Add flow edges
        for flow in prov["chain_flow"]:
            mermaid_lines.append(
                f'    {flow["from_stage"]} -->|{flow["via"]}| {flow["to_stage"]}'
            )

        mermaid_lines.append("    classDef stage fill:#242736,stroke:#6c8cff,color:#e1e4ed")
        section += md.mermaid("\n".join(mermaid_lines))

        # Per-stage detail table
        rows = []
        for stage in prov["stages"]:
            has_activity = (
                stage["hash_computations"] or stage["hash_fields_stored"]
                or any(e["hash_fields"] for e in stage["events_published"])
                or stage["hash_reads"] or stage["chain_hashes"]
            )
            if not has_activity:
                continue

            computes = ", ".join(stage["hash_computations"][:3]) or "—"
            reads = ", ".join(stage["hash_reads"][:3]) or "—"
            event_hashes = []
            for e in stage["events_published"]:
                if e["hash_fields"]:
                    event_hashes.append(f"{e['event_type']}: {', '.join(e['hash_fields'])}")
            evt_str = "; ".join(event_hashes) or "—"
            rows.append([stage["label"], f"`{stage['file']}`", computes, reads, evt_str])

        section += md.table(
            ["Stage", "File", "Computes", "Reads", "Event Hash Fields"],
            rows,
        )

        # DB columns storing hashes
        if prov["model_hash_columns"]:
            section += md.heading(3, "Database Columns Storing Hashes")
            rows = [[c["model"], c["column"], c["type"]] for c in prov["model_hash_columns"]]
            section += md.table(["Model", "Column", "Type"], rows)

        # Flow summary
        if prov["chain_flow"]:
            section += md.heading(3, "Hash Flow Connections")
            rows = []
            for f in prov["chain_flow"]:
                from_label = next((s["label"] for s in prov["stages"] if s["id"] == f["from_stage"]), f["from_stage"])
                to_label = next((s["label"] for s in prov["stages"] if s["id"] == f["to_stage"]), f["to_stage"])
                rows.append([from_label, to_label, f"`{f['via']}`"])
            section += md.table(["From", "To", "Via"], rows)

        return section
