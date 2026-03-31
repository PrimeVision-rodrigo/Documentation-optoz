# Documentation Watcher

A living documentation generator that watches any project directory and automatically produces architecture docs, dataflow diagrams, frontend maps, event system analysis, and an interactive HTML dashboard — all kept in sync as your code changes.

## How It Works

Point it at a folder. It auto-detects your languages, frameworks, models, routes, frontend, Docker services, and event systems — then generates 5 markdown documents and an interactive dashboard.

```
python3 -m watcher.main /path/to/your/project -o ./output -p 8080
```

Open `http://localhost:8080/dashboard.html` to view the dashboard.

## What It Generates

| Document | Contents |
|---|---|
| `01_DEVELOPMENT_LOG.md` | Git history, file stats, code distribution, change heatmap |
| `02_ARCHITECTURE.md` | Service dependency diagram, DB models, API endpoints, Docker infra |
| `03_DATAFLOW.md` | System overview, frontend-to-backend flows, storage layer |
| `04_AUDIT_TRAIL_DATAFLOW.md` | Event sources, event models, hash provenance chain |
| `05_VISUAL_DESIGN_LOG.md` | Route map, page components, shared components |
| `dashboard.html` | Interactive dark-themed dashboard with charts, treemaps, and analysis |

Tabs and sections adapt to what's detected — a backend-only project won't show frontend tabs, a project without events skips the events tab, etc.

## Auto-Detection

The watcher scans your project and discovers:

- **Languages** — Python, TypeScript, JavaScript, Go, Rust, Java, Ruby, Vue, Svelte
- **Frameworks** — React, Vue, Angular, FastAPI, Django, Flask, Express, SQLAlchemy, Prisma, Ant Design, Tailwind, and more
- **Project structure** — models, routes, services, frontend entry point, pages, components
- **Infrastructure** — Docker Compose services, Dockerfiles
- **Event systems** — publish_event, emit, dispatch, Celery tasks
- **Project name** — from package.json, pyproject.toml, setup.py, or directory name

## Usage

### CLI

```bash
# Minimal — point at a folder
python3 -m watcher.main /path/to/project

# Full options
python3 -m watcher.main /path/to/project \
  --output ./docs \
  --name "My Project" \
  --port 8080
```

### Environment Variables

```bash
export DOC_WATCHER_PROJECT_PATH=/path/to/project
export DOC_WATCHER_OUTPUT_PATH=./docs
export DOC_WATCHER_PORT=8080
python3 -m watcher.main
```

### Docker

```bash
PROJECT_PATH=/path/to/project docker compose up -d
```

Dashboard will be at `http://localhost:8080/dashboard.html`.

### config.yaml

All fields are optional — auto-detection fills in the gaps:

```yaml
project_path: /path/to/project
output_path: ./output
project_name: ""        # auto-detected if empty
port: 8080              # omit to disable HTTP server
flush_interval_seconds: 900
poll_interval_seconds: 3
domain_rules: {}        # auto-detected if empty
architecture_patterns: []
dataflow_patterns: []
audit_patterns: []
visual_patterns: []
```

## How It Runs

1. **Detect** — Scans the project to build a `ProjectProfile`
2. **Generate** — Produces all 6 output files from the current project state
3. **Watch** — Monitors for file changes using polling (Docker-compatible)
4. **Flush** — Every 15 minutes (configurable), regenerates only the docs affected by changes
5. **Serve** — Optionally serves the dashboard over HTTP

## Analysis Layers

The dashboard includes three tiers of analysis:

- **Rule-based** — Code distribution, large file detection, TODO scanning, coverage matrix
- **Manual annotations** — Drop a `manual_annotations.yaml` in the output dir with your own assessments
- **Claude AI** — Set `ANTHROPIC_API_KEY` and touch `.analyze` in the output dir to get LLM-powered recommendations

## Requirements

```
pip install watchdog pyyaml
```

Optional: `pip install anthropic` for Claude AI analysis.

## Project Structure

```
watcher/
  main.py                          # Entry point
  config.py                        # CLI / env / yaml config
  file_monitor.py                  # Watchdog file change handler
  change_tracker.py                # Change accumulator
  analyzers/
    project_detector.py            # Auto-detection engine
    code_analyzer.py               # Rule-based analysis
    claude_advisor.py              # LLM-powered analysis
    manual_annotations.py          # User-provided annotations
  generators/
    base_generator.py              # Abstract base
    dev_log_generator.py           # 01_DEVELOPMENT_LOG.md
    architecture_generator.py      # 02_ARCHITECTURE.md
    dataflow_generator.py          # 03_DATAFLOW.md
    audit_trail_generator.py       # 04_AUDIT_TRAIL_DATAFLOW.md
    visual_design_generator.py     # 05_VISUAL_DESIGN_LOG.md
    dashboard_generator.py         # dashboard.html
  parsers/
    python_parser.py               # AST-based Python parsing
    typescript_parser.py           # Regex-based TS/TSX parsing
    generic_parser.py              # Fallback line counting
    provenance_parser.py           # Hash chain detection
  utils/
    file_classifier.py             # Domain classification
    markdown_writer.py             # Markdown helpers
    hash_utils.py                  # SHA256 hashing
config.yaml                       # Default configuration
docker-compose.yml                # Docker deployment
Dockerfile                        # Container build
```
