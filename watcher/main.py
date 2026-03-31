"""
Documentation Watcher — Entry Point

Monitors any project directory for file changes and maintains
living documentation files that stay in sync with the codebase.
"""

import functools
import http.server
import logging
import signal
import sys
import threading
import time

from watchdog.observers.polling import PollingObserver

from watcher.analyzers.project_detector import detect_project
from watcher.change_tracker import ChangeTracker
from watcher.config import Config
from watcher.file_monitor import ProjectFileHandler
from watcher.generators.architecture_generator import ArchitectureGenerator
from watcher.generators.audit_trail_generator import AuditTrailGenerator
from watcher.generators.dashboard_generator import DashboardGenerator
from watcher.generators.dataflow_generator import DataflowGenerator
from watcher.generators.dev_log_generator import DevLogGenerator
from watcher.generators.visual_design_generator import VisualDesignGenerator
from watcher.utils.file_classifier import FileClassifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("doc-watcher")

# Global flag for clean shutdown
_running = True


def main():
    global _running

    # Load config (supports CLI args, env vars, config.yaml)
    config = Config()

    log.info(f"Documentation Watcher starting")
    log.info(f"Project path: {config.project_path}")
    log.info(f"Output path:  {config.output_path}")

    # Auto-detect project structure
    log.info("Detecting project structure...")
    profile = detect_project(config.project_path, config.watch_excludes)
    config.apply_profile(profile)

    log.info(f"Project: {config.project_name}")
    log.info(f"Type: {profile.project_type}")
    log.info(f"Languages: {', '.join(f'{lang} ({lines:,} lines)' for lang, lines in sorted(profile.languages.items(), key=lambda x: -x[1])[:5])}")
    if profile.frameworks:
        log.info(f"Frameworks: {', '.join(profile.frameworks)}")
    log.info(f"Features: frontend={profile.has_frontend}, backend={profile.has_backend}, docker={profile.has_docker}, events={profile.has_event_system}")

    config.output_path.mkdir(parents=True, exist_ok=True)
    log.info(f"Flush interval: {config.flush_interval}s")

    # Set up classifier and tracker
    classifier = FileClassifier(config.domain_rules)
    tracker = ChangeTracker(classifier)

    # Set up file monitor
    handler = ProjectFileHandler(config, tracker)
    log.info("Building initial file hash cache...")
    handler.seed_hashes()
    log.info("Hash cache ready")

    # Set up generators
    generators = [
        DevLogGenerator(config),
        ArchitectureGenerator(config),
        DataflowGenerator(config),
        AuditTrailGenerator(config),
        VisualDesignGenerator(config),
        DashboardGenerator(config),
    ]

    # Initial scan — generate all docs from current state
    log.info("Running initial scan...")
    for gen in generators:
        try:
            content = gen.initial_scan()
            gen.write(content)
            log.info(f"  Generated {gen.filename}")
        except Exception as e:
            log.error(f"  Failed to generate {gen.filename}: {e}")

    log.info("Initial scan complete — all documents generated")

    # Start watchdog observer (polling for Docker bind mount compatibility)
    observer = PollingObserver(timeout=config.poll_interval)
    observer.schedule(handler, str(config.project_path), recursive=True)
    observer.start()
    log.info(f"Watching for changes (polling every {config.poll_interval}s)...")

    # Start HTTP server if port specified
    http_server = None
    if config.port:
        http_server = _start_http_server(config.output_path, config.port)

    # Signal handlers for clean shutdown
    def shutdown(signum, frame):
        global _running
        log.info(f"Received signal {signum}, shutting down...")
        _running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Main loop — flush changes at configured interval
    last_flush = time.time()

    while _running:
        try:
            time.sleep(1)
            now = time.time()

            if now - last_flush >= config.flush_interval:
                last_flush = now
                _flush(tracker, generators)

        except KeyboardInterrupt:
            break

    # Final flush before exit
    _flush(tracker, generators)

    observer.stop()
    observer.join()
    if http_server:
        http_server.shutdown()
        log.info("HTTP server stopped")
    log.info("Watcher stopped")


def _flush(tracker: ChangeTracker, generators: list):
    """Flush accumulated changes to all relevant generators."""
    changes = tracker.flush()

    if not changes:
        log.info("Flush: no changes since last interval")
        return

    changed_files = ChangeTracker.changed_files(changes)
    log.info(f"Flush: {len(changes)} change(s) in {len(changed_files)} file(s)")

    for gen in generators:
        if gen.should_update(changed_files):
            try:
                content = gen.update(changes)
                if content:
                    gen.write(content)
                    log.info(f"  Updated {gen.filename}")
            except Exception as e:
                log.error(f"  Failed to update {gen.filename}: {e}")


def _start_http_server(directory, port):
    """Start a background HTTP server serving the output directory."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    server = http.server.HTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(f"Dashboard available at http://localhost:{port}/dashboard.html")
    return server


if __name__ == "__main__":
    main()
