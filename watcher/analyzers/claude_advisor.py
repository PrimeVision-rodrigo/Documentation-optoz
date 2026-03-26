"""
Claude Advisor — LLM-powered analysis integration.

Plug in your Anthropic API key to get intelligent, context-aware recommendations
for each section of the dashboard. Without an API key, falls back gracefully to
rule-based analysis only.

Usage:
    1. Set ANTHROPIC_API_KEY environment variable (or in config.yaml)
    2. Drop a file named '.analyze' in the output directory to trigger analysis
    3. Results are cached in 'claude_analysis.json' and persist across restarts

The advisor sends a compressed summary of the code analysis to Claude and gets
back per-section recommendations that augment the rule-based findings.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger("doc-watcher")

CACHE_FILE = "claude_analysis.json"
TRIGGER_FILE = ".analyze"


def check_and_run(output_path: Path, analysis: dict, dashboard_data: dict) -> dict | None:
    """Check for trigger file, run Claude analysis if triggered, return cached results.

    Args:
        output_path: where output files live (check for trigger, write cache)
        analysis: the rule-based analysis dict from code_analyzer
        dashboard_data: the full dashboard data for context

    Returns:
        Dict of section_id → claude_annotation, or None if no analysis available.
    """
    trigger = output_path / TRIGGER_FILE
    cache = output_path / CACHE_FILE

    # Check if triggered
    if trigger.is_file():
        log.info("Claude analysis triggered — checking for API key...")
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")

        if api_key:
            result = _run_analysis(api_key, analysis, dashboard_data)
            if result:
                _write_cache(cache, result)
                trigger.unlink(missing_ok=True)
                log.info("Claude analysis complete — results cached")
                return result
            else:
                log.warning("Claude analysis failed — using cached results if available")
                trigger.unlink(missing_ok=True)
        else:
            log.info("No ANTHROPIC_API_KEY set — skipping Claude analysis. "
                     "Set the env var and touch .analyze again to enable.")
            trigger.unlink(missing_ok=True)

    # Return cached results if available
    return _read_cache(cache)


def _run_analysis(api_key: str, analysis: dict, dashboard_data: dict) -> dict | None:
    """Call Claude API with project summary and get recommendations."""
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic package not installed. Run: pip install anthropic")
        return None

    # Build compact summary for Claude
    summary = _build_summary(analysis, dashboard_data)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": f"""You are analyzing an AI inspection platform called Optoz.
Based on the following code analysis summary, provide specific, actionable recommendations
for each section. Focus on:
- Security gaps
- Performance risks
- Compliance completeness (21 CFR Part 11)
- Architecture improvements
- Missing test coverage

Return ONLY valid JSON in this format:
{{
    "section_id": {{
        "claude_summary": "one sentence assessment",
        "recommendations": ["specific action 1", "specific action 2"]
    }}
}}

Use these section IDs: user_journey, code_distribution, system_architecture,
database_models, api_endpoints, data_pipeline, coverage_matrix, triple_write,
provenance, event_registry, page_sizes, route_map

Analysis summary:
{summary}"""
            }],
        )

        response_text = message.content[0].text
        # Extract JSON from response
        start = response_text.index("{")
        end = response_text.rindex("}") + 1
        return json.loads(response_text[start:end])

    except Exception as e:
        log.error(f"Claude API call failed: {e}")
        return None


def _build_summary(analysis: dict, dashboard_data: dict) -> str:
    """Build a compact text summary for Claude."""
    lines = []
    lines.append(f"Endpoints: {len(dashboard_data.get('endpoints', []))}")
    lines.append(f"Models: {len(dashboard_data.get('models', []))}")
    lines.append(f"Pages: {len(dashboard_data.get('pages', []))}")
    lines.append(f"Event types: {len(dashboard_data.get('event_types', []))}")
    lines.append("")

    for section_id, data in analysis.items():
        lines.append(f"[{section_id}]")
        lines.append(f"  Status: {data['status']} ({data['progress']}%)")
        lines.append(f"  Summary: {data['summary']}")
        if data.get("findings"):
            for f in data["findings"][:3]:
                lines.append(f"  Finding: {f}")
        if data.get("recommendations"):
            for r in data["recommendations"][:2]:
                lines.append(f"  Current rec: {r}")
        lines.append("")

    return "\n".join(lines)


def _write_cache(path: Path, data: dict):
    """Write analysis results to cache file."""
    cache = {
        "generated_at": datetime.now().isoformat(),
        "sections": data,
    }
    path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _read_cache(path: Path) -> dict | None:
    """Read cached analysis results."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("sections")
    except (json.JSONDecodeError, OSError):
        return None
