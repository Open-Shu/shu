# WORKFLOW_CONTRACT

Implementation Status: Partial

## Limitations / Known Issues
- Execution model will iterate based on MVP

## Purpose
Define contract for workflow templates and executions.

## Template Schema (Conceptual)
- id: string
- version: string
- steps: [
  { id, type, tool_ref?, input_schema?, approvals?: [role|user], conditions?: [expr], timeouts? }
]
- budgets: {max_run_ms, token_budget}

## Execution State
- run_id, user_id, status: pending|running|succeeded|failed|degraded
- started_at, finished_at, metrics
- step_states: [{step_id, status, started_at, finished_at, error?}]

## Example: Start Run Request
```json
POST /api/v1/workflows/morning_briefing_v1/run
{"user_id": "uuid"}
```

## Example: Run Status Response
```json
{
  "run_id": "uuid",
  "status": "running",
  "step_states": [
    {"step_id": "calendar", "status": "succeeded"},
    {"step_id": "email", "status": "running"}
  ],
  "metrics": {"elapsed_ms": 5400}
}
```

## Security
- RBAC checks at trigger and per step
- Audit events on state transitions

