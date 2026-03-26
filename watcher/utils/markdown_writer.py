from datetime import datetime


def heading(level: int, text: str) -> str:
    return f"{'#' * level} {text}\n\n"


def table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        # Pad row to match headers
        padded = row + [""] * (len(headers) - len(row))
        lines.append("| " + " | ".join(str(c) for c in padded) + " |")
    return "\n".join(lines) + "\n\n"


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def divider() -> str:
    return "---\n\n"


def code_block(content: str, lang: str = "") -> str:
    return f"```{lang}\n{content}\n```\n\n"


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) + "\n\n"


def bold(text: str) -> str:
    return f"**{text}**"


def mermaid(content: str) -> str:
    """Wrap content in a Mermaid diagram code block."""
    return f"```mermaid\n{content}\n```\n\n"


def mermaid_graph_td(nodes: list[str], edges: list[tuple[str, str, str]]) -> str:
    """Build a top-down Mermaid graph.

    nodes: list of "id[Label]" or "id([Label])" strings
    edges: list of (from_id, to_id, label) tuples
    """
    lines = ["graph TD"]
    for node in nodes:
        lines.append(f"    {node}")
    for src, dst, label in edges:
        if label:
            lines.append(f"    {src} -->|{label}| {dst}")
        else:
            lines.append(f"    {src} --> {dst}")
    return mermaid("\n".join(lines))


def mermaid_sequence(participants: list[str], messages: list[tuple[str, str, str]]) -> str:
    """Build a Mermaid sequence diagram.

    participants: list of "participant X as Label" strings
    messages: list of (from, to, message) tuples
    """
    lines = ["sequenceDiagram"]
    for p in participants:
        lines.append(f"    {p}")
    for src, dst, msg in messages:
        lines.append(f"    {src}->>+{dst}: {msg}")
    return mermaid("\n".join(lines))


def bar_chart_text(data: dict[str, int], max_width: int = 30) -> str:
    """Render a simple text-based horizontal bar chart."""
    if not data:
        return ""
    max_val = max(data.values()) if data.values() else 1
    lines = ["```"]
    max_label = max(len(k) for k in data) if data else 0
    for label, value in data.items():
        bar_len = int((value / max_val) * max_width) if max_val > 0 else 0
        bar = "█" * bar_len
        lines.append(f"  {label:<{max_label}} │ {bar} {value}")
    lines.append("```")
    return "\n".join(lines) + "\n\n"
