# Development Log & Progress

> Auto-generated on 2026-03-24 04:21:34 | Optoz AI Documentation Watcher

---

## Git History

```
41becf3 Fix MinIO networking: move all services to optoz-net bridge (Option 1)
6622f88 Update all docs to reflect V11.0 /api/ prefix and new features
6a503a4 V11.0: Fix all 12 implementation issues — API prefix, shared client, modular training
6e77fe2 Fix HPO: export best-trial model for inference + per-trial audit trail
0f38e55 Add Service Health monitoring page with platform-wide health endpoint
ab5187c Fix training progress display, live log capture, and job cancellation
ae0e030 Fix HPO model name resolution and add 3 missing models (FRE, WinCLIP, DSR)
210afe3 Fix PaDiM HPO: mask name mismatch and n_features overflow
88208ba Fix HPO training: apply learning rate, per-trial detail, richer trial table
01af2ce Fix HPO/training monitoring: live logs, real-time polling, HPO charts
e867734 Fix all 18 Anomalib models: class names, compatibility, model-specific configs
828ea16 V11: SAM2 fixes, 14 new models, version audit, calibration, validation UX, deployment gating
c73dbec Fix white screen: use type-only imports, downgrade react-konva to v18
ecc7f33 V10: Segmentation labeling, SAM2, per-defect validation, HPO with Optuna
063961a V9: Capture context gate, sidebar workflow reorg, training audit trail fix
c29d5c2 V8: Event-sourced audit trail, settings page, validation dataset analysis
32c602f V7: Modular architecture, event sourcing, Docker infrastructure
d387c26 V6: Fix Anomalib 2.2.0 training, real metrics capture, enriched job detail view
5e1afa0 V5: Fix training pipeline, add validation workspace, enhance labeling & audit trail
5de8a49 labeling done
a75489b Refactor: Isolated camera service, updated frontend UI & navigation, and cleaned repo structure
101a9a8 Update codebase: add training scripts, checks, and ignore data dirs
b66b0da V4: Integrated AI Factory with Valkey Streams, MinIO Sorting, and Anomalib Background Training
db97c92 Fix: Synchronize PostgreSQL path after MinIO move
f854b90 V3 Actual: Implemented Valkey Streams for audit event management
4cfe753 V2: Added CORS and audit logs endpoint for React GUI
d353771 V2: Added CORS and audit logs endpoint for React GUI
05c2397 V1: Working Vimba capture with MinIO and mDNS
```

## Project File Statistics

| Category | Count |
| --- | --- |
| Markdown docs | 8 |
| Python files | 46 |
| React TSX files | 22 |
| TypeScript files | 4 |
| YAML configs | 1 |

```
  Markdown docs    │ █████ 8
  Python files     │ ██████████████████████████████ 46
  React TSX files  │ ██████████████ 22
  TypeScript files │ ██ 4
  YAML configs     │  1
```

## Code Distribution by Domain

```
  Frontend Pages      │ ██████████████████████████████ 7785
  Backend Routes      │ ███████████████████ 5046
  Python              │ █████████ 2353
  Training Worker     │ ████████ 2325
  Frontend Components │ ██████ 1579
  Backend Services    │ █████ 1441
  Database Models     │  223
  Frontend Core       │  202
  Application Core    │  187
  Frontend            │  118
  Database Migrations │  94
  API Schemas         │  84
  Authentication      │  72
  Database            │  17
  Configuration       │  13
```

## Change Log

_Watcher started at 2026-03-24 04:21:34. Changes will be logged below._

