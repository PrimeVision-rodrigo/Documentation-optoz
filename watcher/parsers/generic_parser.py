from pathlib import Path


def parse_generic_file(path: Path) -> dict:
    """Fallback parser: basic stats for any file type."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"line_count": 0, "size_bytes": 0, "extension": path.suffix}

    return {
        "line_count": content.count("\n") + 1,
        "size_bytes": path.stat().st_size,
        "extension": path.suffix,
        "non_empty_lines": sum(1 for line in content.splitlines() if line.strip()),
    }
