# Personal Profile & Base-Context Contract

Implementation Status: Partial

Limitations / Known Issues
- No persistent Profile store exists; profile context is not saved or reused across runs
- No continuous monitoring or scheduled intake; all current signals are ad-hoc tool executions
- No agreed schema for profile features (identity graph, relationships, projects, interests, working style)
- No scoring/weighting logic; no decay or confidence handling
- No privacy redaction pipeline; raw email bodies can be sent to LLMs
- No RBAC-scoped profile slices; multi-tenant considerations unimplemented
- Morning Briefing currently assembles context on the fly and does not update any profile state

Security Vulnerabilities
- PII exposure risk: email bodies are included in prompts; lacking redaction/anonymization
- Secret leakage risk from forwarded content; no classifier or filter stage
- Data retention undefined for profile features and raw ingested content
- Auditability incomplete: no event log of profile updates or model reads of sensitive data
- OAuth/Domain Delegation scopes must be least-privilege and verified per source

---

Goal
Make the Personal Profile a first-class, always-present base context for every agent. The profile is continuously learned from integrations (Gmail, Chat, Calendar, Jira, HubSpot, …), persisted, and served to agents as part of their initial context along with model configuration prompts and KB retrievals.

Scope (Currently supports vs. Target)
- Currently supports: ad-hoc Gmail and Calendar digests feeding into Morning Briefing; model-configuration-driven prompts; RAG context from attached KBs
- Target: persistent profile with daily/continuous updates; feature schema; learning loop; profile served to all agents; cross-source enrichment

Contract Components
1) Data Sources (intake)
- Gmail (messages/threads, bodies+metadata)
- Calendar (events, attendees, times; later descriptions)
- Chat/DM (planned)
- Jira (planned)
- HubSpot/CRM (planned)

2) Profile Feature Schema (target outline)
- Identity & Roles: names, emails/handles, departments, titles
- Relationships: contact graph (affinity, recency, reciprocity), teams
- Projects: active themes/initiatives, tags, milestones
- Work Patterns: meeting cadence, working hours, responsiveness
- Interests & Topics: extracted entities/keywords with confidence/decay
- Preferences: format/tone expectations, decision styles, priorities
- Availability & Constraints: calendar-derived, quiet hours, travel
- Knowledge Links: associated KB documents and sources
Note: This schema is not implemented. It is the target to guide storage and extractors.

3) Learning & Update Loop (target)
- Intake: poll or webhook per source; map to internal events
- Extract: run source-specific extractors to update features (e.g., topic/entity extraction, contact affinity)
- Persist: merge into Profile store with versioning and timestamps
- Publish: produce a Profile Snapshot for serving to agents
- Observe: metrics and audit logs for updates

4) Serving API (contract)
- get_profile_context(user_id, *, format="text|json", max_chars=None) -> str|dict
  - Returns a compact profile snapshot suitable for LLM context (text) or downstream processing (json)
  - Must be included by default in all agent contexts before RAG
- get_profile_document(user_id) -> DocumentRef
  - Returns a pointer to a chunked profile document in the KB for full-document retrieval when allowed
Note: These APIs are not implemented. Current flows do not include a Profile service.

5) Context Assembly Policy
- Always include: ModelConfiguration system prompt, Profile snapshot, then RAG results, then ephemeral tool outputs
- Token management: when approaching capacity, degrade RAG before dropping Profile; escalate to full-document retrieval per FULL_DOC policy when appropriate
- Current behavior (as of this commit): Morning Briefing includes full email bodies for the selected window and calendar titles/times; no Profile snapshot is included because it does not exist yet

6) Privacy & RBAC
- Scope each source by user and tenant; enforce RBAC when building the Profile and when serving it
- Provide redaction rules (e.g., credentials, secrets, financial/health identifiers)
- Maintain audit logs for profile updates and prompt assembly containing profile data

Current Code References (evidence)
- Gmail ingestion: src/shu/processors/gmail_processor.py (GmailMessage with body_text, metadata)
- Gmail digest tool (now includes body_text in artifacts): src/shu/agent/tools/gmail_digest.py
- Calendar ingestion: src/shu/processors/calendar_processor.py (event titles/times)
- Morning Briefing orchestrator: src/shu/agent/orchestrator.py (injects full email bodies into LLM context; uses ChatService to build base messages)
- Chat context builder re-use: src/shu/services/chat_service.py (model-configuration-driven prompts & RAG)

Planned Tasks to Realize This Contract
- Define Profile schema and storage (DB + ORM) [EPIC-PROFILE-LEARNING]
- Implement ProfileService (persist, merge, snapshot) and Feature Extractors for Gmail/Calendar first
- Wire a scheduled intake (or triggers) to keep the profile fresh
- Expose Serving API and integrate into ChatService context assembly
- Add redaction/anonymization pass for Profile snapshots
- Add audit logging and metrics for profile updates and prompt usage
- UI to inspect and correct the Profile; per-source toggles

Notes
- Do not introduce parallel context systems. All agents should consume the same Profile snapshot via the Serving API
- Prefer deterministic, explainable feature extraction over opaque embeddings where possible; include rationale in stored features



---

Future Implementation Notes (Context Engine alignment)

Context Package schema (planned for ContextAssemblyService)
- version: string (e.g., "v1")
- messages: [ { role: system|user|assistant, content: str, segment_id: str } ]
- segments: [
  - { id, type: system_prompt|profile|rag_chunk|tool_output|history, content_ref, tokens_estimate }
  ]
- provenance: { [segment_id]: { source_type: kb|email|calendar|tool|profile|system,
                                 source_id, title, uri, kb_id, chunk_range, score, recency,
                                 model_configuration_id } }
- token_budget: { model_max_tokens, reserved_for_response, available_for_context,
                  used_by_profile, used_by_rag, used_by_tools, used_by_history,
                  headroom, drop_decisions: [ {segment_id, reason} ] }
- policy: { precedence: [system_prompt, profile, rag_chunk, tool_output, history],
           drop_order: [tool_output, rag_chunk, history],
           full_doc_escalation_allowed: bool, escalation_reason?: str }
- deterministic: { enabled: bool, seed?: int }

Deterministic mode and Observability (planned)
- Deterministic toggle fixes any non-deterministic ordering/scoring in retrieval/assembly for tests.
- Context trace ("context card") structure emitted when debug flag enabled:
  - request_id, user_id (hashed), conversation_id
  - ranked_candidates: [{ source_id, type, scores: {semantic, keyword, recency}, selected: bool }]
  - included_segments: [{ segment_id, type, tokens, provenance_id }]
  - budgeting: { input_budget, used_by_component, drops }
  - redaction: { applied: bool, rules: [...] }
- Default: off in production; on in tests/dev via config.

Hybrid retrieval controls (planned; config names indicative)
- settings.CONTEXT.hybrid.enabled: bool
- settings.CONTEXT.hybrid.recency_boost: float (0..1)
- settings.CONTEXT.hybrid.keyword_weight: float (0..1)
- settings.CONTEXT.hybrid.semantic_weight: float (0..1)
- settings.CONTEXT.hybrid.max_results: int
- settings.CONTEXT.dedup.enabled: bool
- settings.CONTEXT.dedup.min_overlap_chars: int
- ModelConfiguration overrides of the above allowed per model/KB.

Assembly policy details (clarification)
- Always include in this order: system prompt → Profile snapshot → RAG chunks → tool outputs → recent history.
- Under token pressure, drop in this order: tool outputs → RAG chunks → history; Profile is last to drop.
- Full-document escalation is allowed only when KB policy permits and provenance is preserved in context.

Related Tasks
- TASK-210: Context Assembly Service (EPIC-AGENT-FOUNDATION)
- TASK-506: Hybrid Retrieval and Ranking (EPIC-CONTEXTUAL-INTELLIGENCE)
- TASK-507: Profile Snapshot Integration (EPIC-CONTEXTUAL-INTELLIGENCE)
- TASK-508: Context Observability & Deterministic Mode (EPIC-CONTEXTUAL-INTELLIGENCE)
