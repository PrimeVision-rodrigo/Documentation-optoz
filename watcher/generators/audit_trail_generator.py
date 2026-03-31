import os
import re
from pathlib import Path

from watcher.change_tracker import Change
from watcher.config import Config
from watcher.generators.base_generator import BaseGenerator
from watcher.parsers.provenance_parser import parse_provenance
from watcher.utils import markdown_writer as md


class AuditTrailGenerator(BaseGenerator):
    """Doc 4: Event & Logging System — event types, sources, patterns."""

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

    @property
    def _profile(self):
        return self.config.profile

    def _build(self) -> str:
        content = md.heading(1, "Event & Logging System")
        content += f"> Auto-generated on {md.timestamp()} | Documentation Watcher\n\n"
        content += md.divider()

        if not self._profile or not self._profile.has_event_system:
            content += "_No event/logging system detected in this project._\n\n"
            content += "The watcher looks for patterns like `publish_event()`, `emit()`, `dispatch()`, "
            content += "EventEmitter usage, Celery tasks, etc.\n\n"
            return content

        # Event sources by route
        content += self._build_event_sources()

        # Event-related models
        content += self._build_event_models()

        # Hash provenance (if detected)
        content += self._build_provenance_chain()

        return content

    def _build_event_sources(self) -> str:
        section = md.heading(2, "Event Sources")

        if not self._profile or not self._profile.routes_dirs:
            return section + "_No routes directory detected._\n\n"

        # Search for event publishing patterns across all Python files
        event_patterns_re = [
            (r'publish_event\s*\([^,]*,\s*["\'](\w+)["\']', "publish_event"),
            (r'event_type\s*=\s*["\'](\w+)["\']', "event_type"),
            (r'emit\s*\(\s*["\'](\w+)["\']', "emit"),
            (r'dispatch\s*\(\s*["\'](\w+)["\']', "dispatch"),
            (r'send_task\s*\(\s*["\']([^"\']+)["\']', "celery_task"),
        ]

        rows = []

        # Search in route directories
        for route_dir_rel in self._profile.routes_dirs:
            route_dir = self.project / route_dir_rel
            if not route_dir.is_dir():
                continue

            for route_file in sorted(route_dir.glob("*.py")):
                if route_file.name == "__init__.py":
                    continue

                source = self._read_file(str(route_file.relative_to(self.project)))
                events = set()

                for pattern, _ in event_patterns_re:
                    for match in re.finditer(pattern, source):
                        events.add(match.group(1))

                if events:
                    for evt in sorted(events):
                        rows.append([route_file.stem, evt])

        # Also search service directories
        for svc_dir_rel in (self._profile.services_dirs or []):
            svc_dir = self.project / svc_dir_rel
            if not svc_dir.is_dir():
                continue

            for svc_file in sorted(svc_dir.glob("*.py")):
                if svc_file.name == "__init__.py":
                    continue

                source = self._read_file(str(svc_file.relative_to(self.project)))
                events = set()

                for pattern, _ in event_patterns_re:
                    for match in re.finditer(pattern, source):
                        events.add(match.group(1))

                if events:
                    for evt in sorted(events):
                        rows.append([str(svc_file.relative_to(self.project)), evt])

        if rows:
            section += f"**{len(set(r[1] for r in rows))} unique event types detected**\n\n"
            section += md.table(["Source", "Event Type"], rows)

            # Build Mermaid diagram
            source_events: dict[str, list[str]] = {}
            for src, evt in rows:
                source_events.setdefault(src, []).append(evt)

            mermaid_lines = ["graph LR"]
            for src, events in source_events.items():
                node_id = src.replace("/", "_").replace(".", "_").replace("-", "_")
                mermaid_lines.append(f'    {node_id}["{src}"]:::route')
                for evt in events[:5]:
                    evt_id = evt.replace(" ", "").replace(".", "_")
                    mermaid_lines.append(f'    {evt_id}(["{evt}"]):::event')
                    mermaid_lines.append(f"    {node_id} --> {evt_id}")
                if len(events) > 5:
                    more_id = f"{node_id}_more"
                    mermaid_lines.append(f'    {more_id}(["...+{len(events)-5} more"]):::event')
                    mermaid_lines.append(f"    {node_id} --> {more_id}")

            mermaid_lines.append("    classDef route fill:#81C784,stroke:#388E3C,color:#000")
            mermaid_lines.append("    classDef event fill:#FFE082,stroke:#F9A825,color:#000")
            section += md.mermaid("\n".join(mermaid_lines))
        else:
            section += "_No event publishing calls found in routes or services._\n\n"

        return section

    def _build_event_models(self) -> str:
        section = md.heading(2, "Event-Related Models")

        if not self._profile or not self._profile.models_files:
            return section + "_No models detected._\n\n"

        from watcher.parsers.python_parser import parse_models_file

        found_any = False
        for models_rel in self._profile.models_files:
            models_path = self.project / models_rel
            if not models_path.is_file():
                continue

            models = parse_models_file(models_path)
            for model in models:
                name_lower = model["name"].lower()
                # Match models that look event/audit related
                if any(kw in name_lower for kw in ["event", "audit", "log", "history", "activity"]):
                    found_any = True
                    section += md.heading(3, f"`{model['name']}` (table: `{model['tablename']}`)")
                    if model["columns"]:
                        rows = [[c["name"], c["type"]] for c in model["columns"]]
                        section += md.table(["Column", "Type"], rows)

        if not found_any:
            section += "_No event/audit model classes found._\n\n"

        return section

    def _build_provenance_chain(self) -> str:
        section = md.heading(2, "Hash Provenance Chain")
        section += "_Detected hash operations across the codebase._\n\n"

        prov = parse_provenance(self.project)
        if not prov["stages"]:
            return section + "_No hash provenance data detected._\n\n"

        # Check if there's actually hash activity
        active_stages = [s for s in prov["stages"] if
                         s["hash_computations"] or s["hash_fields_stored"] or
                         any(e["hash_fields"] for e in s["events_published"]) or
                         s["hash_reads"] or s["chain_hashes"]]

        if not active_stages:
            return section + "_No hash operations found._\n\n"

        # Per-stage detail table
        rows = []
        for stage in active_stages:
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

        # Hash flow connections
        if prov["chain_flow"]:
            section += md.heading(3, "Hash Flow Connections")
            rows = []
            for f in prov["chain_flow"]:
                from_label = next((s["label"] for s in prov["stages"] if s["id"] == f["from_stage"]), f["from_stage"])
                to_label = next((s["label"] for s in prov["stages"] if s["id"] == f["to_stage"]), f["to_stage"])
                rows.append([from_label, to_label, f"`{f['via']}`"])
            section += md.table(["From", "To", "Via"], rows)

        # DB columns storing hashes
        if prov["model_hash_columns"]:
            section += md.heading(3, "Database Columns Storing Hashes")
            rows = [[c["model"], c["column"], c["type"]] for c in prov["model_hash_columns"]]
            section += md.table(["Model", "Column", "Type"], rows)

        return section
