# Vox Agent Organization Edition Docs

This folder contains the draft design package for repositioning Vox Agent as an organization-internal collaboration and work-state query system.

## Documents

1. [CEO + Eng Review](00_CEO_ENG_REVIEW.md)
2. [Product Design](01_PRODUCT_DESIGN.md)
3. [Technical Design](02_TECHNICAL_DESIGN.md)
4. [Architecture Diagrams](03_ARCHITECTURE_DIAGRAMS.md)
5. [Security And Permissions](04_SECURITY_AND_PERMISSIONS.md)
6. [TDD Development Plan](05_TDD_DEVELOPMENT_PLAN.md)
7. [Task Breakdown](06_TASK_BREAKDOWN.md)
8. [Pilot Runbook](07_PILOT_RUNBOOK.md)

The accepted mainline integration boundary and its authentication/migration
gates are recorded in
[ADR: Integrate org-collab into the shared mainline](../decisions/2026-07-17-org-collab-mainline.md).
The production engine now mounts principal-bound `/v1/org/*` routes from its
shared `AppContext`; tenant and actor identity come from the verified local or
Bearer principal. The older isolated `/org/*` API remains a test-only
compatibility harness and is not a separately deployable production sidecar.

## Design Summary

Vox Agent Organization Edition turns voice notes, coding sessions, agent execution, GitHub activity, and task progress into permission-aware team memory. The first release is scoped to a single private team focused on engineering work.

The core product rule is work-scope transparency:

> Work-scope raw context is visible to the team by default, while private spans, secrets, credentials, and non-work content remain protected and auditable.

## Pilot Readiness

Seed a repeatable local demo dataset:

```bash
python -m engine.org_demo_seed --data-dir .voxagent-demo
```

Validate the demo seed and org-collab slice:

```bash
python -m pytest tests/org_collab/test_demo_seed.py -q
python -m pytest tests/org_collab -q
```

Pilot setup, capture policy, admin setup, rollback, and pre-open-source safety checks are in [Pilot Runbook](07_PILOT_RUNBOOK.md).
