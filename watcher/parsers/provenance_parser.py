"""
Provenance Parser — Extracts hash chain and cryptographic operations from source code.

Scans Python files for:
- hashlib.sha256 computations
- file_hash / chain_hash variable usage
- publish_event() payload fields (especially hash-related ones)
- Model columns that store hashes

Works generically on any project — auto-discovers files with hash operations.
"""

import os
import re
from pathlib import Path


# Directories to skip
SKIP_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    "dist", "build", ".tox", ".mypy_cache",
}


def parse_provenance(project_path: Path) -> dict:
    """Parse all relevant files and return a provenance map.

    Auto-discovers Python files containing hash operations instead of
    using hardcoded file paths.
    """
    result = {"stages": [], "model_hash_columns": [], "chain_flow": []}

    # Auto-discover files with hash operations
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            try:
                source = open(fpath, encoding="utf-8", errors="replace").read()
            except OSError:
                continue

            # Check if file has hash-related operations
            if not any(kw in source for kw in [
                "hashlib", "sha256", "sha1", "md5",
                "file_hash", "chain_hash", "integrity_hash",
                "compute_hash", "compute_", "_hash",
            ]):
                continue

            rel_path = os.path.relpath(fpath, project_path)
            stage_id = _path_to_stage_id(rel_path)
            stage_label = _path_to_label(rel_path)

            stage = {
                "id": stage_id,
                "label": stage_label,
                "file": rel_path,
                "hash_computations": _find_hash_computations(source),
                "hash_fields_stored": _find_hash_stores(source),
                "events_published": _find_event_payloads(source),
                "hash_reads": _find_hash_reads(source),
                "chain_hashes": _find_chain_hashes(source),
            }

            # Only include if there's actual hash activity
            has_activity = (
                stage["hash_computations"] or stage["hash_fields_stored"] or
                any(e["hash_fields"] for e in stage["events_published"]) or
                stage["hash_reads"] or stage["chain_hashes"]
            )
            if has_activity:
                result["stages"].append(stage)

    # Parse all model files for hash columns
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if fname in ("models.py", "model.py") or fname.endswith("_models.py"):
                fpath = os.path.join(root, fname)
                cols = _find_model_hash_columns(Path(fpath))
                result["model_hash_columns"].extend(cols)

    # Build chain flow from cross-references
    result["chain_flow"] = _build_chain_flow(result["stages"])

    return result


def _path_to_stage_id(rel_path: str) -> str:
    """Convert a file path to a stage ID."""
    # Use the filename stem, with parent dir prefix for disambiguation
    parts = rel_path.replace("\\", "/").split("/")
    stem = os.path.splitext(parts[-1])[0]
    if len(parts) > 1:
        return f"{parts[-2]}_{stem}".replace("-", "_")
    return stem.replace("-", "_")


def _path_to_label(rel_path: str) -> str:
    """Convert a file path to a display label."""
    parts = rel_path.replace("\\", "/").split("/")
    stem = os.path.splitext(parts[-1])[0]
    label = stem.replace("_", " ").title()
    if len(parts) > 1:
        parent = parts[-2].replace("_", " ").title()
        return f"{parent} — {label}"
    return label


def _find_hash_computations(source: str) -> list[str]:
    """Find where hashes are computed."""
    results = []

    for match in re.finditer(
        r'hashlib\.sha256\(([^)]+)\)\.hexdigest\(\)',
        source,
    ):
        arg = match.group(1).strip()
        line_start = source.rfind("\n", 0, match.start()) + 1
        line = source[line_start:source.find("\n", match.end())]
        assign_match = re.match(r'\s*(\w+)\s*=', line)
        var_name = assign_match.group(1) if assign_match else "hash"
        results.append(f"SHA256({_simplify_arg(arg)}) → {var_name}")

    for match in re.finditer(
        r'(\w+)\s*=\s*(compute_\w+_hash)\(([^)]*)\)',
        source,
    ):
        var_name = match.group(1)
        func_name = match.group(2)
        results.append(f"{func_name}() → {var_name}")

    for match in re.finditer(r'(\w+)\s*=\s*create_dataset_manifest\(', source):
        results.append(f"create_dataset_manifest() → {match.group(1)}")

    return results


def _find_hash_stores(source: str) -> list[str]:
    """Find where hash values are written to database models."""
    results = []

    for match in re.finditer(
        r'(\w+)\.([\w]*hash[\w]*)\s*=\s*(\w+)',
        source, re.IGNORECASE,
    ):
        results.append(f"{match.group(1)}.{match.group(2)} = {match.group(3)}")

    return list(set(results))


def _find_event_payloads(source: str) -> list[dict]:
    """Find publish_event calls and extract payload fields."""
    events = []

    for match in re.finditer(
        r'publish_(?:training_)?event\s*\(',
        source,
    ):
        before = source[max(0, match.start() - 600):match.start()]
        after = source[match.start():min(match.start() + 800, len(source))]

        type_match = re.search(r'"(\w+)"', after)
        if not type_match:
            continue
        event_type = type_match.group(1)

        all_fields = []
        hash_fields = []

        for key_match in re.finditer(r'"(\w+)":', after):
            field = key_match.group(1)
            all_fields.append(field)
            if any(h in field.lower() for h in ("hash", "chain", "integrity")):
                hash_fields.append(field)

        if not all_fields:
            for var_match in re.finditer(r'(\w+)\s*=\s*\{', before):
                dict_source = before[var_match.start():]
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

    for match in re.finditer(
        r'(\w+)\s*=\s*(\w+)\.([\w]*hash[\w]*)',
        source, re.IGNORECASE,
    ):
        results.append(f"{match.group(1)} ← {match.group(2)}.{match.group(3)}")

    for match in re.finditer(
        r'\.get\(\s*"([\w]*hash[\w]*)"\s*\)',
        source, re.IGNORECASE,
    ):
        results.append(f'.get("{match.group(1)}")')

    return list(set(results))


def _find_chain_hashes(source: str) -> list[str]:
    """Find chain hash computations."""
    results = []

    for match in re.finditer(
        r'f"([^"]*\{[^}]*hash[^}]*\}[^"]*\|[^"]*)"',
        source, re.IGNORECASE,
    ):
        results.append(f"chain: {match.group(1)[:80]}")

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
    producers = {}
    consumers = {}

    # Dynamically discover hash keywords from the stages
    hash_keywords = set()
    for stage in stages:
        for comp in stage["hash_computations"]:
            for word in re.findall(r'\w*hash\w*', comp, re.IGNORECASE):
                hash_keywords.add(word)
        for evt in stage["events_published"]:
            for hf in evt["hash_fields"]:
                hash_keywords.add(hf)
        for read in stage["hash_reads"]:
            for word in re.findall(r'\w*hash\w*', read, re.IGNORECASE):
                hash_keywords.add(word)
        for chain in stage["chain_hashes"]:
            for word in re.findall(r'\w*hash\w*', chain, re.IGNORECASE):
                hash_keywords.add(word)

    for stage in stages:
        sid = stage["id"]
        for comp in stage["hash_computations"]:
            for kw in hash_keywords:
                if kw in comp:
                    producers[kw] = sid

        for evt in stage["events_published"]:
            for hf in evt["hash_fields"]:
                if hf not in producers:
                    producers[hf] = sid

        for read in stage["hash_reads"]:
            for kw in hash_keywords:
                if kw in read:
                    consumers.setdefault(kw, []).append(sid)

        for chain in stage["chain_hashes"]:
            for kw in hash_keywords:
                if kw in chain:
                    consumers.setdefault(kw, []).append(sid)

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
    arg = arg.strip()
    if len(arg) > 30:
        return arg[:27] + "..."
    return arg
