# AGENT_CONFIGURATION

Implementation Status: Partial

## Limitations / Known Issues
- Extends existing ModelConfiguration; final fields TBD

## Purpose
Define configuration contract for agents.

## Schema (Conceptual)
- id, name, description
- base_model_ref (existing ModelConfiguration ref)
- personality: {system_prompt_ref, style, constraints}
- capabilities: [tool_refs]
- memory: {enabled, retention_days, scope}
- policies: {rbac_policies, approval_requirements}

## Example: Agent Configuration
```json
{
  "id": "morning_briefing_agent",
  "base_model_ref": "gpt-4o-mini",
  "personality": {"system_prompt_ref": "prompts/morning-briefing.md", "style": "concise", "constraints": ["cite sources"]},
  "capabilities": ["tool:gmail", "tool:calendar", "tool:web_search"],
  "memory": {"enabled": true, "retention_days": 14, "scope": "per-user"},
  "policies": {"rbac_policies": ["briefing:view:self"], "approval_requirements": ["action:send_email"]}
}
```

## Security
- Policies must be enforced by orchestration layer

