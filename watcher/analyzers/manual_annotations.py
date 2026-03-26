"""
Manual Annotations — Hand-written summaries and recommendations by Claude.

These are context-aware assessments written after deep analysis of the Optoz
codebase (V12, 28 commits, ~27K lines). They supplement the rule-based
code_analyzer findings with domain-specific insights.

Update these whenever the codebase changes significantly.
Last reviewed: 2026-03-24 (V12)
"""

ANNOTATIONS = {

    # ================================================================
    # OVERVIEW TAB
    # ================================================================

    "user_journey": {
        "claude_summary": (
            "The core 10-step workflow (Login → Project → Setup → Capture → Label → "
            "Train → Queue → Validate → Deploy → Monitor) is fully navigable in the UI. "
            "The main gaps are post-deployment: there is no closed-loop feedback from "
            "production inference back to labeling priorities, and no factory floor "
            "integration to trigger captures automatically."
        ),
        "recommendations": [
            "Image Quality Gate: Add a quality validation step between Capture and Labeling. "
            "Currently images go straight to labeling with only lighting calibration — "
            "blur, exposure, and SNR checks would prevent bad training data from entering the pipeline.",

            "Active Learning Loop: After inference detects anomalies, surface the most uncertain "
            "predictions (scores near threshold) as suggested labeling candidates. This closes "
            "the feedback loop and improves model accuracy over time.",

            "Factory Integration: The Monitor step currently shows dashboards but has no "
            "outbound integration. Define a PLC/MES protocol for pass/fail signals so "
            "Optoz can drive reject gates or trigger re-inspection.",

            "Edge Deployment: The Deploy step creates metadata stubs, not real artifacts. "
            "Implement ONNX export from Anomalib and TensorRT conversion for Jetson deployment.",
        ],
    },

    "code_distribution": {
        "claude_summary": (
            "27K lines across 81 files is a well-scoped codebase for a V12 platform. "
            "Frontend Pages (7.8K lines) is the largest domain, which is expected for a "
            "data-rich inspection UI. The backend is cleanly split across 17 route modules. "
            "The main structural concern is zero test files — the entire platform has no "
            "automated test coverage."
        ),
        "recommendations": [
            "Add test coverage starting with the most critical paths: event publishing "
            "(verify triple-write atomicity), training pipeline (verify job state transitions), "
            "and inference (verify hash chain computation). Even 30% coverage on these three "
            "would catch most regressions.",

            "The training_script.py (2,237 lines) is the single largest file. Consider splitting "
            "it into trainer.py (core loop), metrics.py (metric collection), and manifest.py "
            "(dataset hashing) for maintainability.",
        ],
    },

    "git_history": {
        "claude_summary": (
            "28 commits from V1 to V12 show a disciplined development arc: foundational "
            "services (V1-V4), then feature expansion (V5-V8 added event sourcing, audit trail, "
            "labeling), then model breadth (V9-V11 added 18 Anomalib models, HPO, SAM2), "
            "then stabilization (V12 fixes). The commit messages are descriptive and version-tagged."
        ),
        "recommendations": [
            "Consider tagging releases in git (git tag v12.0) to make version boundaries explicit. "
            "This enables easy diffing between versions and rollback if needed.",
        ],
    },

    # ================================================================
    # ARCHITECTURE TAB
    # ================================================================

    "system_architecture": {
        "claude_summary": (
            "Clean three-layer architecture: React SPA → FastAPI REST API → PostgreSQL/MinIO/Valkey. "
            "The event-sourced design with hash-chained audit trail is the standout feature — "
            "it provides 21 CFR Part 11 compliance for the entire platform. The Training Worker "
            "runs as a separate process with its own event publishing, which is good isolation. "
            "All services run on a shared Docker bridge network (optoz-net)."
        ),
        "recommendations": [
            "Add health check endpoints to docker-compose.yml for each service (healthcheck: test). "
            "Currently only the backend has /health — PostgreSQL, MinIO, and Valkey should be "
            "checked at the compose level for proper restart behavior.",

            "The backend runs on port 8001 in dev (uvicorn) but 8000 in Docker. Standardize to "
            "one port to avoid confusion. The CORS configuration should also be reviewed to ensure "
            "it's locked down for production (currently allows broad origins for dev).",

            "Consider adding a reverse proxy (Nginx/Traefik) in front of the backend for "
            "production. This would handle TLS, rate limiting, and static file serving.",
        ],
    },

    "database_models": {
        "claude_summary": (
            "12 well-structured models with 117 columns. The Event model is the backbone — "
            "hash-chained with integrity_hash and previous_hash for tamper evidence. "
            "TrainingJob has 17 columns including metrics_json which stores chain hashes. "
            "The AuditRecord stores file_hash at capture time, creating the provenance anchor. "
            "LabeledImage supports mask annotations and dataset splits."
        ),
        "recommendations": [
            "Add database indexes on Event.project_id + Event.timestamp for efficient "
            "project-scoped audit queries. The current indexes are on individual columns "
            "but composite indexes would improve dashboard query performance.",

            "Consider adding a soft-delete pattern (is_deleted flag) to Project and TrainingJob "
            "instead of hard DELETE. This preserves audit trail integrity — currently deleting "
            "a project removes the FK reference from events.",

            "The CalibrationRecord model stores reference histograms as JSON text. For production "
            "with many calibration checks per day, consider a separate CalibrationCheck table "
            "to avoid bloating the main record.",
        ],
    },

    "docker_services": {
        "claude_summary": (
            "7 Docker services properly networked on optoz-net. PostgreSQL 15, Valkey 8 (Alpine), "
            "MinIO for S3 storage, plus build-context services for backend, training-worker, "
            "vision-service (camera), and frontend (Nginx). The compose file includes volume "
            "definitions for data persistence."
        ),
        "recommendations": [
            "Pin MinIO to a specific version tag instead of 'minio/minio' (latest). Version "
            "drift in object storage can cause subtle API compatibility issues.",

            "Add resource limits (mem_limit, cpus) to the training-worker service. Without limits, "
            "a large training job could starve other services of memory, especially on shared GPU "
            "machines.",
        ],
    },

    "api_endpoints": {
        "claude_summary": (
            "75 endpoints across 16 route modules — comprehensive REST API. Training module "
            "is the largest (14 endpoints covering simple training, HPO, exploratory search, "
            "job management). Labeling is second (10 endpoints). Auth uses OAuth2 token flow. "
            "6 modules (defect_validation, deployment, inference, production, sam2, system) "
            "don't publish audit events — these are read-heavy or utility endpoints."
        ),
        "recommendations": [
            "Add publish_event() to the deployment module — deployment package creation is a "
            "compliance-critical action that should be audited (DeploymentPackageCreated event "
            "exists but verify it fires on all code paths).",

            "The inference endpoints should log InferenceRun events consistently for both single "
            "and batch inference. Verify that scan_dataset publishes aggregate events, not just "
            "per-image tracking.",

            "Add rate limiting to auth/token endpoint to prevent brute-force attacks. "
            "Currently there's no throttling on failed login attempts.",

            "Consider adding OpenAPI tags and descriptions to all routes for auto-generated "
            "API documentation quality. The /docs endpoint would be much more useful for "
            "integration partners.",
        ],
    },

    # ================================================================
    # DATA FLOW TAB
    # ================================================================

    "data_pipeline": {
        "claude_summary": (
            "Four main data pipelines are operational: Capture (camera/upload → MinIO → audit), "
            "Training (queue → worker → GPU → model storage), Inference (image + model → score + heatmap), "
            "and Audit Events (triple-write to three sinks). The training pipeline is the most mature "
            "with 18 model support, HPO, and chain hashing. The inference pipeline computes "
            "inference_chain_hash linking image provenance to model provenance to results."
        ),
        "recommendations": [
            "Add an image quality validation pipeline between capture and labeling. This should "
            "compute blur score (Laplacian variance), exposure histogram, SNR estimate, and "
            "clipping percentage. Reject images below thresholds before they enter the training set.",

            "The training worker polls the database for QUEUED jobs. For production scale, "
            "consider switching to a Valkey-based job queue (BRPOP pattern) for lower latency "
            "and reduced database polling load.",

            "Inference results are returned inline but not persisted to a dedicated table. "
            "For production monitoring, add an InferenceResult model that stores scores, "
            "heatmap references, and chain hashes for historical trending.",
        ],
    },

    "coverage_matrix": {
        "claude_summary": (
            "7 of 15 pages have directly detected API calls. The 8 'disconnected' pages "
            "(AuditTrail, RuntimeMonitoring, ServiceMonitoring, Settings, TrainingQueue, etc.) "
            "likely use a shared API service layer or Ant Design's useRequest hook that the "
            "regex parser doesn't catch. This is a parser limitation, not an actual integration gap. "
            "7 backend modules show no frontend callers — production, sam2, sample_projects, and "
            "system are called indirectly or from non-page contexts."
        ),
        "recommendations": [
            "If the frontend uses a centralized API service file (e.g., src/services/api.ts), "
            "add it to the TypeScript parser's scan targets. This would resolve most of the "
            "'disconnected' pages and give a more accurate coverage matrix.",

            "The 'production' module has summary and report endpoints that are likely called "
            "by RuntimeMonitoring — verify this connection is working and add it to the parser.",
        ],
    },

    "frontend_api_calls": {
        "claude_summary": (
            "37 API calls detected across page files. LabelingScreen has the most (7 calls) "
            "reflecting its complexity as the annotation workspace. Validation has 8 calls "
            "spanning training jobs, camera, images, inference, and defect validation. "
            "Most calls use template literals with project-scoped URLs, which is correct."
        ),
        "recommendations": [
            "Centralize API calls into a service layer (src/services/) rather than making "
            "fetch calls directly in page components. This would make the API surface "
            "easier to test, mock, and audit. Several pages already use different patterns "
            "for the same endpoints.",
        ],
    },

    # ================================================================
    # AUDIT TRAIL TAB
    # ================================================================

    "triple_write": {
        "claude_summary": (
            "The triple-write architecture is the compliance cornerstone: every state change "
            "atomically writes to (1) a hash-chained Event Store for immutability, "
            "(2) SystemAuditLog for backward-compatible querying, and (3) Valkey Stream for "
            "real-time notifications. 36 event types cover project lifecycle, training, inference, "
            "user management, and calibration. The integrity chain uses SHA256(event_id + type + "
            "payload + previous_hash) which is solid for tamper detection."
        ),
        "recommendations": [
            "Add periodic integrity verification — a background job that walks the Event Store "
            "and recomputes hashes to detect any tampering. This should run daily and publish "
            "a SystemVersionSnapshot event with the verification result.",

            "The Valkey Stream sink is fire-and-forget (non-fatal on failure). For full compliance, "
            "consider adding a reconciliation job that detects if any events are in the Event Store "
            "but missing from the Valkey Stream, and replays them.",

            "Add electronic signature support (21 CFR Part 11 requirement) — currently the "
            "event store records user_id but doesn't require a signature (password re-entry + "
            "reason) for compliance-critical actions like training approval or deployment.",
        ],
    },

    "provenance": {
        "claude_summary": (
            "The hash provenance chain spans 5 active stages: Capture computes SHA256(image_bytes) → "
            "file_hash, Training Worker computes dataset_total_hash + model_weights_hash → "
            "training_chain_hash, and Inference combines all three into inference_chain_hash. "
            "The InferenceRun event publishes the complete chain. However, there is a gap at "
            "the Labeling stage — ImageLabeled events do not include file_hash, so there's no "
            "hash verification when an image transitions from captured to labeled."
        ),
        "recommendations": [
            "Add file_hash to the ImageLabeled event payload. Read it from AuditRecord.file_hash "
            "and include it in the publish_event() call in labeling.py. This closes the provenance "
            "gap and enables verifying that the labeled image is the same one that was captured.",

            "Add a hash verification step at training time — before training on a dataset, "
            "verify each image's current SHA256 against the stored file_hash in AuditRecord. "
            "This detects if any image was modified after capture (bit-rot, accidental overwrite, "
            "or tampering).",

            "Consider adding a ProvenanceChain table that explicitly links: "
            "capture_hash → label_event_id → training_job_id → inference_chain_hash. "
            "This would make provenance queries O(1) instead of requiring cross-table joins.",
        ],
    },

    "event_registry": {
        "claude_summary": (
            "36 event types fully mapped to audit actions. 10 route modules publish events "
            "covering project CRUD, image lifecycle, training pipeline, user management, and "
            "calibration. The training worker publishes 6 additional event types independently. "
            "Event types follow a clear naming convention (EntityAction: ProjectCreated, "
            "TrainingJobCompleted, etc.)."
        ),
        "recommendations": [
            "Add events for inference results (currently InferenceRun exists but batch scan "
            "completion could use a ScanCompleted event with aggregate stats).",

            "Consider adding a schema version to event payloads. If the payload structure of "
            "ImageCaptured changes in V13, old events need to be parseable. A 'schema_version' "
            "field in event_metadata would enable forward-compatible event processing.",
        ],
    },

    # ================================================================
    # FRONTEND TAB
    # ================================================================

    "page_sizes": {
        "claude_summary": (
            "15 pages totaling ~7.8K lines. TrainingQueue (1,347 lines) and Validation (1,163 lines) "
            "are the largest — both are data-heavy pages with tables, charts, and real-time polling. "
            "LabelingScreen (805 lines) includes an interactive canvas (AnnotationCanvas component). "
            "5 pages exceed 500 lines, which is the threshold where splitting into sub-components "
            "improves maintainability."
        ),
        "recommendations": [
            "Extract the training job detail drawer from TrainingQueue into a TrainingJobDetail "
            "component. The queue list and job detail views have distinct responsibilities and "
            "would benefit from separation.",

            "LabelingScreen's annotation canvas logic should be fully isolated in the existing "
            "AnnotationCanvas component. Verify that label state management, defect list handling, "
            "and SAM2 integration are not leaking back into the parent page.",

            "Add loading skeletons (Ant Design Skeleton component) to pages that fetch data on "
            "mount. Currently several pages show a plain Spin component, which doesn't communicate "
            "the page structure during loading.",
        ],
    },

    "ant_design_usage": {
        "claude_summary": (
            "26 Ant Design components used across 15 pages. Button, Card, and Space are universal. "
            "Table is heavily used (8 pages) for data display. Form appears in 6 pages for input. "
            "Specialized components like Timeline, Steps, Transfer, and Cascader are used sparingly. "
            "The component selection is appropriate for a data-intensive enterprise application."
        ),
        "recommendations": [
            "Consider using Ant Design's ConfigProvider to set a consistent theme (dark mode support, "
            "brand colors) across all pages instead of per-component styling.",

            "The Tag component is used in 10 pages — ensure a consistent color scheme for status "
            "tags (e.g., green=success, orange=running, red=failed) across Training, Deployment, "
            "and Audit pages.",
        ],
    },

    "route_map": {
        "claude_summary": (
            "16 routes with clean path structure. Project-scoped routes use /project/* prefix "
            "with ?id={projectId} query parameter for context. Global routes (/audit, /calibration, "
            "/services, /settings, /users) are project-independent. Login is the only unauthenticated "
            "route. The sidebar menu conditionally shows project-scoped items only when a project "
            "is selected, which is good UX."
        ),
        "recommendations": [
            "Add a 404 catch-all route to handle unknown paths gracefully instead of showing "
            "a blank page.",

            "Consider adding breadcrumb navigation using Ant Design's Breadcrumb component, "
            "especially for deep project flows (Project Hub → Setup → Capture → Labeling). "
            "This helps users orient themselves in the workflow.",
        ],
    },

    # ================================================================
    # CHANGES TAB
    # ================================================================

    "change_heatmap": {
        "claude_summary": (
            "The change heatmap starts empty and populates as the documentation watcher detects "
            "file changes in Optoz_v0.1. Over time, it reveals which domains are actively being "
            "modified — useful for identifying hot areas during development sprints and stable "
            "areas that may need regression testing after nearby changes."
        ),
        "recommendations": [
            "Watch for unexpected changes in stable domains (Database Models, Event System). "
            "Changes to models.py or event_publisher.py should trigger a review of migration "
            "scripts and event schema compatibility.",
        ],
    },

    "file_sizes": {
        "claude_summary": (
            "7 files exceed 800 lines: training_script.py (2,237), ANOMALIB_TRAINING_BLUEPRINT.md "
            "(2,013), TrainingQueue.tsx (1,347), Validation.tsx (1,163), training.py routes (838), "
            "inference.py routes (777), and LabelingScreen.tsx (805). The training-related files "
            "being large is expected given the complexity of 18-model support with HPO. The "
            "markdown docs are reference material that should stay consolidated."
        ),
        "recommendations": [
            "Split training_script.py into focused modules: core training loop, dataset manifest "
            "builder, metric collection, and chain hash computation. Each has a distinct "
            "responsibility and would be easier to test independently.",

            "The inference.py route file handles both single-image and batch inference with "
            "very different flows. Consider splitting into inference_single.py and inference_batch.py "
            "with a shared utils module.",
        ],
    },
}
