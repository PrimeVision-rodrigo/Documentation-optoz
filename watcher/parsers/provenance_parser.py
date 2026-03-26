"""
Provenance Parser — Extracts hash chain and image lifecycle data from source code.

Scans Python files for:
- hashlib.sha256 computations
- file_hash / chain_hash variable usage
- publish_event() payload fields (especially hash-related ones)
- Model columns that store hashes

Produces a per-stage provenance map that documents how image identity
is tracked through capture → labeling → training → inference.
"""

import re
from pathlib import Path


# Map file paths to lifecycle stages
STAGE_RULES = {
    "app/routes/capture.py": "capture",
    "app/routes/labeling.py": "labeling",
    "app/routes/training.py": "training_api",
    "app/routes/inference.py": "inference",
    "training/trainer.py": "training_worker",
    "training/hpo.py": "training_hpo",
    "app/services/event_publisher.py": "event_system",
    "app/services/event_store.py": "event_store",
    "app/models.py": "models",
}

STAGE_LABELS = {
    "capture": "Image Capture",
    "labeling": "Labeling",
    "training_api": "Training (API)",
    "training_worker": "Training (Worker)",
    "training_hpo": "Training (HPO)",
    "inference": "Inference",
    "event_system": "Event Publisher",
    "event_store": "Event Store",
    "models": "Data Models",
}

STAGE_ORDER = [
    "capture", "labeling", "training_api", "training_worker",
    "training_hpo", "inference", "event_system", "event_store", "models",
]


def parse_provenance(project_path: Path) -> dict:
    """Parse all relevant files and return a provenance map.

    Returns:
        {
            "stages": [
                {
                    "id": "capture",
                    "label": "Image Capture",
                    "file": "app/routes/capture.py",
                    "hash_computations": ["SHA256 of image bytes → file_hash"],
                    "hash_fields_stored": ["AuditRecord.file_hash"],
                    "events_published": [
                        {"event_type": "ImageCaptured", "hash_fields": ["file_hash"], "other_fields": [...]}
                    ],
                    "hash_reads": ["AuditRecord.file_hash"],
                    "chain_hashes": [],
                }
            ],
            "model_hash_columns": [
                {"model": "AuditRecord", "column": "file_hash"},
                {"model": "Event", "column": "integrity_hash"},
            ],
            "chain_flow": [
                {"from": "capture", "to": "inference", "via": "file_hash"},
                {"from": "training_worker", "to": "inference", "via": "training_chain_hash"},
            ]
        }
    """
    result = {"stages": [], "model_hash_columns": [], "chain_flow": []}

    for rel_path, stage_id in STAGE_RULES.items():
        full_path = project_path / rel_path
        if not full_path.is_file():
            continue

        try:
            source = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        stage = {
            "id": stage_id,
            "label": STAGE_LABELS.get(stage_id, stage_id),
            "file": rel_path,
            "hash_computations": _find_hash_computations(source),
            "hash_fields_stored": _find_hash_stores(source),
            "events_published": _find_event_payloads(source),
            "hash_reads": _find_hash_reads(source),
            "chain_hashes": _find_chain_hashes(source),
        }
        result["stages"].append(stage)

    # Parse models for hash columns
    models_path = project_path / "app" / "models.py"
    if models_path.is_file():
        result["model_hash_columns"] = _find_model_hash_columns(models_path)

    # Sort stages by lifecycle order
    order_map = {s: i for i, s in enumerate(STAGE_ORDER)}
    result["stages"].sort(key=lambda s: order_map.get(s["id"], 99))

    # Build chain flow from cross-references
    result["chain_flow"] = _build_chain_flow(result["stages"])

    return result


def _find_hash_computations(source: str) -> list[str]:
    """Find where hashes are computed (hashlib.sha256, compute_*_hash, etc.)."""
    results = []

    # hashlib.sha256(...).hexdigest()
    for match in re.finditer(
        r'hashlib\.sha256\(([^)]+)\)\.hexdigest\(\)',
        source,
    ):
        arg = match.group(1).strip()
        # Try to find what variable it's assigned to
        line_start = source.rfind("\n", 0, match.start()) + 1
        line = source[line_start:source.find("\n", match.end())]
        assign_match = re.match(r'\s*(\w+)\s*=', line)
        var_name = assign_match.group(1) if assign_match else "hash"
        results.append(f"SHA256({_simplify_arg(arg)}) → {var_name}")

    # compute_*_hash() calls
    for match in re.finditer(
        r'(\w+)\s*=\s*(compute_\w+_hash)\(([^)]*)\)',
        source,
    ):
        var_name = match.group(1)
        func_name = match.group(2)
        results.append(f"{func_name}() → {var_name}")

    # create_dataset_manifest
    for match in re.finditer(r'(\w+)\s*=\s*create_dataset_manifest\(', source):
        results.append(f"create_dataset_manifest() → {match.group(1)}")

    return results


def _find_hash_stores(source: str) -> list[str]:
    """Find where hash values are written to database models."""
    results = []

    # Pattern: model.field = hash_var (where field contains 'hash')
    for match in re.finditer(
        r'(\w+)\.([\w]*hash[\w]*)\s*=\s*(\w+)',
        source, re.IGNORECASE,
    ):
        results.append(f"{match.group(1)}.{match.group(2)} = {match.group(3)}")

    # Pattern: field=hash_var in constructor (where field contains 'hash')
    for match in re.finditer(
        r'([\w]*hash[\w]*)\s*=\s*(\w+)',
        source, re.IGNORECASE,
    ):
        field = match.group(1)
        # Skip imports and function definitions
        line_start = source.rfind("\n", 0, match.start()) + 1
        line = source[line_start:match.end()]
        if "def " in line or "import " in line or "class " in line:
            continue
        if field not in [r.split("=")[0].split(".")[-1].strip() for r in results]:
            # Avoid duplicates from the first pattern
            context = source[max(0, match.start() - 100):match.start()]
            if any(kw in context for kw in ("AuditRecord", "Event", "TrainingJob", "LabeledImage")):
                results.append(f"{field} = {match.group(2)}")

    return list(set(results))


def _find_event_payloads(source: str) -> list[dict]:
    """Find publish_event calls and extract payload fields, especially hash-related ones."""
    events = []

    for match in re.finditer(
        r'publish_(?:training_)?event\s*\(',
        source,
    ):
        # Look at context: 600 chars before (for variable defs) and 800 after
        before = source[max(0, match.start() - 600):match.start()]
        after = source[match.start():min(match.start() + 800, len(source))]

        type_match = re.search(r'"(\w+)"', after)
        if not type_match:
            continue
        event_type = type_match.group(1)

        all_fields = []
        hash_fields = []

        # Search inline dict in the call
        for key_match in re.finditer(r'"(\w+)":', after):
            field = key_match.group(1)
            all_fields.append(field)
            if any(h in field.lower() for h in ("hash", "chain", "integrity")):
                hash_fields.append(field)

        # If the payload is passed as a variable, look for its dict definition nearby
        # Pattern: var_name = { "key": ... } in the before-context
        if not all_fields:
            # Try to find the variable name that's the payload argument
            for var_match in re.finditer(r'(\w+)\s*=\s*\{', before):
                var_name = var_match.group(1)
                # Find the dict content
                dict_start = var_match.start()
                dict_source = before[dict_start:]
                for key_match in re.finditer(r'"(\w+)":', dict_source):
                    field = key_match.group(1)
                    if field not in all_fields:
                        all_fields.append(field)
                    if any(h in field.lower() for h in ("hash", "chain", "integrity")):
                        if field not in hash_fields:
                            hash_fields.append(field)

        if event_type and (all_fields or hash_fields):
            events.append({
                "event_type": event_type,
                "hash_fields": hash_fields,
                "payload_fields": all_fields,
            })

    return events


def _find_hash_reads(source: str) -> list[str]:
    """Find where hash values are read from the database."""
    results = []

    # Pattern: var = record.file_hash or record.integrity_hash
    for match in re.finditer(
        r'(\w+)\s*=\s*(\w+)\.([\w]*hash[\w]*)',
        source, re.IGNORECASE,
    ):
        results.append(f"{match.group(1)} ← {match.group(2)}.{match.group(3)}")

    # Pattern: .get("*hash*")
    for match in re.finditer(
        r'\.get\(\s*"([\w]*hash[\w]*)"\s*\)',
        source, re.IGNORECASE,
    ):
        results.append(f'.get("{match.group(1)}")')

    return list(set(results))


def _find_chain_hashes(source: str) -> list[str]:
    """Find chain hash computations (combining multiple hashes)."""
    results = []

    # f-string with multiple hash variables joined by |
    for match in re.finditer(
        r'f"([^"]*\{[^}]*hash[^}]*\}[^"]*\|[^"]*)"',
        source, re.IGNORECASE,
    ):
        results.append(f"chain: {match.group(1)[:80]}")

    # Direct string concatenation for chain
    for match in re.finditer(
        r'chain_input\s*=\s*(.+)',
        source,
    ):
        results.append(f"chain_input = {match.group(1).strip()[:80]}")

    return results


def _find_model_hash_columns(models_path: Path) -> list[dict]:
    """Find model columns that store hashes."""
    try:
        source = models_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    results = []
    current_model = None

    for line in source.splitlines():
        class_match = re.match(r'^class (\w+)\(', line)
        if class_match:
            current_model = class_match.group(1)
            continue

        if current_model:
            col_match = re.match(r'\s+([\w]*hash[\w]*)\s*=\s*Column\((\w+)', line, re.IGNORECASE)
            if col_match:
                results.append({
                    "model": current_model,
                    "column": col_match.group(1),
                    "type": col_match.group(2),
                })

    return results


def _build_chain_flow(stages: list[dict]) -> list[dict]:
    """Build connections between stages based on shared hash variables."""
    flows = []

    # Build a map of which stages produce vs consume each hash field
    producers = {}  # hash_name → stage_id
    consumers = {}  # hash_name → [stage_ids]

    hash_keywords = [
        "file_hash", "training_chain_hash", "inference_chain_hash",
        "dataset_total_hash", "model_weights_hash", "integrity_hash",
    ]

    for stage in stages:
        sid = stage["id"]
        # Productions: hash computations
        for comp in stage["hash_computations"]:
            for kw in hash_keywords:
                if kw in comp:
                    producers[kw] = sid

        # Productions: event payload hash fields
        for evt in stage["events_published"]:
            for hf in evt["hash_fields"]:
                if hf not in producers:
                    producers[hf] = sid

        # Consumptions: hash reads
        for read in stage["hash_reads"]:
            for kw in hash_keywords:
                if kw in read:
                    consumers.setdefault(kw, []).append(sid)

        # Consumptions: chain hash inputs
        for chain in stage["chain_hashes"]:
            for kw in hash_keywords:
                if kw in chain:
                    consumers.setdefault(kw, []).append(sid)

    # Build edges (deduplicated)
    seen = set()
    for hash_name, producer in producers.items():
        for consumer in consumers.get(hash_name, []):
            if producer != consumer:
                key = (producer, consumer, hash_name)
                if key not in seen:
                    seen.add(key)
                    flows.append({
                        "from_stage": producer,
                        "to_stage": consumer,
                        "via": hash_name,
                    })

    return flows


def _simplify_arg(arg: str) -> str:
    """Simplify a function argument for display."""
    arg = arg.strip()
    if len(arg) > 30:
        return arg[:27] + "..."
    return arg
