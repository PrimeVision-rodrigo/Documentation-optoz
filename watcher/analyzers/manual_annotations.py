"""
Manual Annotations — Optional hand-written summaries and recommendations.

For generic projects, this starts empty. Users can add their own annotations
by editing this file or providing a manual_annotations.yaml file in the
output directory.

Format:
    ANNOTATIONS = {
        "section_id": {
            "claude_summary": "One-paragraph assessment",
            "recommendations": ["Specific action 1", "Specific action 2"],
        },
    }
"""

import json
import yaml
from pathlib import Path


# Start empty for generic projects
ANNOTATIONS: dict[str, dict] = {}


def load_annotations(output_path: Path) -> dict[str, dict]:
    """Load annotations from file if available, otherwise return built-in ones."""
    # Check for user-provided annotations in output directory
    for fname in ["manual_annotations.yaml", "manual_annotations.yml", "manual_annotations.json"]:
        ann_file = output_path / fname
        if ann_file.is_file():
            try:
                content = ann_file.read_text(encoding="utf-8")
                if fname.endswith(".json"):
                    return json.loads(content)
                else:
                    return yaml.safe_load(content) or {}
            except (json.JSONDecodeError, yaml.YAMLError, OSError):
                pass

    return ANNOTATIONS
