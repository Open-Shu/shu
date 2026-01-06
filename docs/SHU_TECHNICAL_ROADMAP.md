# Shu Technical Roadmap Overview

This living document captures Shu’ technical trajectory. The summary below links every roadmap feature to a detailed explanation outlining implementation intent and platform value.

- ## Current Status (code snapshot)
  - **Overall**: Core foundations are in place (auth/RBAC, KB ingestion/RAG, multi-provider LLM + streaming chat, plugin v1 with host capabilities, feeds scheduler and admin UI). Workflow engine, analytics, and marketplace/team layers are not implemented.
  - **Phase Alignment**: Phase 1 (Foundation Hardening) is largely complete; Phase 2 (Adaptive Deployment) is in progress with Docker Compose stack live and Kubernetes manifests drafted; Phase 3 (Agentic Platform) is mostly TODO with only host HTTP client and LLM parameter normalization complete; Phases 4-6 are aspirational with no active implementation.
  - **Auth & Security**: Google SSO + password auth; RBAC enforced; JWT/API key support; secrets/OAuth encryption present. Audit coverage for plugins/feeds is partial, and per-user “run as” enforcement for feeds still lacks full governance.
  - **LLM & Chat**: Streaming chat works with multiple providers. Chat-initiated plugin execution exists for declared `chat_callable_ops`; assistant-side function-calling/orchestration is not yet present.
  - **Knowledge Base & Ingestion**: KB ingestion and RAG with full-document escalation are live; attachments and plugin feeds ingest into KBs. Team/tenant KBs beyond per-user scope are not implemented.
  - **Plugins & Feeds**: Plugin loader/manifest enforcement, host capabilities (http/auth/identity/secrets/storage/kb/cache/ocr), and admin/feeds UIs are live. Gaps: feed health/observability, richer audit trails, schedule ownership guarantees, and per-op identity/data governance.
  - **Workflow/Agents**: Only demo-style sequential orchestration (e.g., Morning Briefing). No generalized workflow engine, approvals, or DSL. Experience Creator (SHU-148) will formalize orchestrations as configurable Experiences.
  - **Analytics/Observability**: Basic logging; no usage/cost dashboards or provider health probes.
  - **Product/UX scope**: Marketplace, setup wizards, team collaboration, and profile learning are not started. Signature experiences (Inbox Triage, Meeting Co-Pilot, Project Pulse, Morning Briefing) are exemplar first-party builds composed from plugins/KBs/agents/prompts/workflows/Experience Creator - not separate products or commitments.

- [Phase 1 - Foundation Hardening](#phase-1-foundation-hardening)
  - [Finalize FastAPI service boundaries](#foundation-hardening-finalize-fastapi-service-boundaries)
  - [Establish async SQLAlchemy usage](#foundation-hardening-establish-async-sqlalchemy-usage)
  - [Define pgvector schema](#foundation-hardening-define-pgvector-schema)
  - [Implement DB-backed scheduling](#foundation-hardening-implement-db-backed-scheduling)
  - [Enforce structured logging](#foundation-hardening-enforce-structured-logging)
  - [Provide unified audit trails](#foundation-hardening-provide-unified-audit-trails)
  - [Complete plugin manifest schema](#foundation-hardening-complete-plugin-manifest-schema)
  - [Lock deterministic execution sandbox](#foundation-hardening-lock-deterministic-execution-sandbox)
  - [Operate secrets and identity broker](#foundation-hardening-operate-secrets-and-identity-broker)
  - [Stabilize feed scheduler for bulk and delta syncs](#foundation-hardening-stabilize-feed-scheduler-for-bulk-and-delta-syncs)
  - [Publish reference workspace connectors](#foundation-hardening-publish-reference-workspace-connectors)
  - [Deliver OCR pipeline](#foundation-hardening-deliver-ocr-pipeline)
  - [Standardize sentence-transformer embeddings](#foundation-hardening-standardize-sentence-transformer-embeddings)
  - [Implement dedupe and classification stages](#foundation-hardening-implement-dedupe-and-classification-stages)
  - [Expose typed retrieval APIs](#foundation-hardening-expose-typed-retrieval-apis)
  - [Persist workflow state for resumability](#foundation-hardening-persist-workflow-state-for-resumability)
  - [Honor approval gates end-to-end](#foundation-hardening-honor-approval-gates-end-to-end)
  - [Enforce JSON-schema LLM outputs](#foundation-hardening-enforce-json-schema-llm-outputs)
  - [Emit audit events for orchestrated actions](#foundation-hardening-emit-audit-events-for-orchestrated-actions)
  - [Package single-machine install](#foundation-hardening-package-single-machine-install)
  - [Bundle Postgres, Redis, and Ollama option](#foundation-hardening-bundle-postgres-redis-and-ollama-option)
  - [Provide scheduler workers and health checks](#foundation-hardening-provide-scheduler-workers-and-health-checks)
- [Phase 1B - Shu RAG Intelligent Retrieval](#phase-1b-shu-rag-intelligent-retrieval)
  - [Phase 1B Document Profile Schema](#phase-1b-document-profile-schema)
  - [Phase 1B Document Profiling and Question Synthesis](#phase-1b-document-profiling-and-question-synthesis)
  - [Phase 1B Multi-Surface Retrieval and Score Fusion](#phase-1b-multi-surface-retrieval-and-score-fusion)
  - [Phase 1B Relational Context](#phase-1b-relational-context)
  - [Phase 1B Agentic Retrieval and Invocation Policy](#phase-1b-agentic-retrieval-and-invocation-policy)
- [Phase 2 - Adaptive Deployment Platform](#phase-2-adaptive-deployment-platform)
  - [Modularize services for multi-topology deploys](#adaptive-deployment-platform-modularize-services-for-multi-topology-deploys)
  - [Introduce dynamic work queues](#adaptive-deployment-platform-introduce-dynamic-work-queues)
  - [Introduce unified cache abstraction](#adaptive-deployment-platform-introduce-unified-cache-abstraction)
  - [Implement workload routing patterns](#adaptive-deployment-platform-implement-workload-routing-patterns)
  - [Publish containerized single-node dev stack](#adaptive-deployment-platform-publish-containerized-single-node-dev-stack)
  - [Publish bare-metal single-node installer](#adaptive-deployment-platform-publish-bare-metal-single-node-installer)
  - [Publish containerized packaging](#adaptive-deployment-platform-publish-containerized-packaging)
  - [Document migration between deployment modes](#adaptive-deployment-platform-document-migration-between-deployment-modes)
  - [Adopt queue abstraction for scheduler](#adaptive-deployment-platform-adopt-queue-abstraction-for-scheduler)
  - [Maintain configuration parity](#adaptive-deployment-platform-maintain-configuration-parity)
  - [Implement user API key system](#adaptive-deployment-platform-implement-user-api-key-system)
  - [Provide API key management endpoints](#adaptive-deployment-platform-provide-api-key-management-endpoints)
  - [Document SDK and APIs](#adaptive-deployment-platform-document-sdk-and-apis)
- [Phase 3 - Agentic Platform](#phase-3-agentic-platform)
  - Phase 3A - Worker and Observability Infrastructure
    - [Add event bus abstraction](#agentic-platform-add-event-bus-abstraction)
    - [Stand up dedicated worker pools](#agentic-platform-stand-up-dedicated-worker-pools)
    - [Export observability signals](#agentic-platform-export-observability-signals)
  - Phase 3B - Experience System and Workflows
    - [Define Experience schema and registry](#agentic-platform-define-experience-schema-and-registry)
    - [Build admin Experience management UI](#agentic-platform-build-admin-experience-management-ui)
    - [Migrate hard-coded orchestrations to Experience abstraction](#agentic-platform-migrate-hardcoded-orchestrations-to-experience-abstraction)
    - [Publish declarative workflow DSL](#agentic-platform-publish-declarative-workflow-dsl)
    - [Deliver workflow step library](#agentic-platform-deliver-workflow-step-library)
    - [Ship policy and approval interface](#agentic-platform-ship-policy-and-approval-interface)
    - [Activate profile graph entities](#agentic-platform-activate-profile-graph-entities)
    - [Expose profile read/write APIs](#agentic-platform-expose-profile-readwrite-apis)
  - Phase 3C - Plugin Governance and LLM Hardening
    - [Introduce capability-scoped plugin actions](#agentic-platform-introduce-capability-scoped-plugin-actions)
    - [Provide host-managed HTTP client](#agentic-platform-provide-host-managed-http-client)
    - [Implement plugin registry lifecycle](#agentic-platform-implement-plugin-registry-lifecycle)
    - [Capture plugin artifact lineage](#agentic-platform-capture-plugin-artifact-lineage)
    - [Consume external MCP servers](#agentic-platform-consume-external-mcp-servers)
    - [Publish Plugin SDK CLI](#agentic-platform-publish-plugin-sdk-cli)
    - [Publish Plugin SDK test harness](#agentic-platform-publish-plugin-sdk-test-harness)
    - [Implement plugin signing and allow-list](#agentic-platform-implement-plugin-signing-and-allow-list)
    - [Add provider health probes](#agentic-platform-add-provider-health-probes)
    - [Normalize LLM parameters](#agentic-platform-normalize-llm-parameters)
    - [Implement LLM fallback logic](#agentic-platform-implement-llm-fallback-logic)
    - [Enable local model pinning](#agentic-platform-enable-local-model-pinning)
    - [Track LLM usage and budgets](#agentic-platform-track-llm-usage-and-budgets)
- [Phase 4 - Intelligence Expansion](#phase-4-intelligence-expansion)
  - [Scale ingestion with stream processing](#intelligence-expansion-scale-ingestion-with-stream-processing)
  - [Apply tenant-aware rate limiting](#intelligence-expansion-apply-tenant-aware-rate-limiting)
  - [Add trace sampling controls](#intelligence-expansion-add-trace-sampling-controls)
  - [Release CRM connectors](#intelligence-expansion-release-crm-connectors)
  - [Release project tracking connectors](#intelligence-expansion-release-project-tracking-connectors)
  - [Release incident response connectors](#intelligence-expansion-release-incident-response-connectors)
  - [Release finance connectors](#intelligence-expansion-release-finance-connectors)
  - [Instrument plugin execution telemetry](#intelligence-expansion-instrument-plugin-execution-telemetry)
  - [Monitor SLAs for plugin workloads](#intelligence-expansion-monitor-slas-for-plugin-workloads)
  - [Create feature stores](#intelligence-expansion-create-feature-stores)
  - [Generate storyline synthesis pipelines](#intelligence-expansion-generate-storyline-synthesis-pipelines)
  - [Surface contextual intelligence APIs](#intelligence-expansion-surface-contextual-intelligence-apis)
  - [Support event-triggered workflows](#intelligence-expansion-support-event-triggered-workflows)
  - [Add compensation handlers](#intelligence-expansion-add-compensation-handlers)
  - [Enforce observability budgets](#intelligence-expansion-enforce-observability-budgets)
  - [Introduce workflow circuit breakers](#intelligence-expansion-introduce-workflow-circuit-breakers)
  - [Build LLM regression suites](#intelligence-expansion-build-llm-regression-suites)
  - [Detect LLM determinism drift](#intelligence-expansion-detect-llm-determinism-drift)
  - [Deploy redaction filters](#intelligence-expansion-deploy-redaction-filters)
  - [Provide fine-tuning hooks](#intelligence-expansion-provide-fine-tuning-hooks)
  - [Launch feed health dashboards](#intelligence-expansion-launch-feed-health-dashboards)
  - [Detect data and policy drift](#intelligence-expansion-detect-data-and-policy-drift)
  - [Run chaos drills for worker pools](#intelligence-expansion-run-chaos-drills-for-worker-pools)
- [Phase 5 - Operating-System Scale](#phase-5-operating-system-scale)
  - [Support multi-region tenancy](#operating-system-scale-support-multi-region-tenancy)
  - [Implement storage sharding strategies](#operating-system-scale-implement-storage-sharding-strategies)
  - [Automate secrets rotation](#operating-system-scale-automate-secrets-rotation)
  - [Offer pluggable storage backends](#operating-system-scale-offer-pluggable-storage-backends)
  - [Launch plugin marketplace surfaces](#operating-system-scale-launch-plugin-marketplace-surfaces)
  - [Deliver plugin recommendation engine](#operating-system-scale-deliver-plugin-recommendation-engine)
  - [Model plugin dependency graph](#operating-system-scale-model-plugin-dependency-graph)
  - [Run auto-upgrade channels](#operating-system-scale-run-auto-upgrade-channels)
  - [Filter discovery with policy awareness](#operating-system-scale-filter-discovery-with-policy-awareness)
  - [Expose organizational memory APIs](#operating-system-scale-expose-organizational-memory-apis)
  - [Provide cross-source analytics surfaces](#operating-system-scale-provide-cross-source-analytics-surfaces)
  - [Emit anomaly alerts](#operating-system-scale-emit-anomaly-alerts)
  - [Automate workflow contract testing](#operating-system-scale-automate-workflow-contract-testing)
  - [Manage collaboration states](#operating-system-scale-manage-collaboration-states)
  - [Maintain commitments ledger](#operating-system-scale-maintain-commitments-ledger)
  - [Balance LLM latency, cost, and accuracy](#operating-system-scale-balance-llm-latency-cost-and-accuracy)
  - [Enable parallel tool planning](#operating-system-scale-enable-parallel-tool-planning)
  - [Ship guardrail templates](#operating-system-scale-ship-guardrail-templates)
  - [Publish multi-cluster reference architectures](#operating-system-scale-publish-multi-cluster-reference-architectures)
  - [Provide SRE runbooks](#operating-system-scale-provide-sre-runbooks)
  - [Author upgrade playbooks](#operating-system-scale-author-upgrade-playbooks)
  - [Compile compliance reporting packs](#operating-system-scale-compile-compliance-reporting-packs)
- [Phase 6 - Ecosystem & Governance](#phase-6-ecosystem-governance)
  - [Guarantee API stability windows](#ecosystem-governance-guarantee-api-stability-windows)
  - [Publish extension hooks](#ecosystem-governance-publish-extension-hooks)
  - [Introduce plugin revenue sharing](#ecosystem-governance-introduce-plugin-revenue-sharing)
  - [Issue certification badges](#ecosystem-governance-issue-certification-badges)
  - [Support plugin attestation flows](#ecosystem-governance-support-plugin-attestation-flows)
  - [Offer telemetry opt-in/out controls](#ecosystem-governance-offer-telemetry-opt-inout-controls)
  - [Expose partner observability APIs](#ecosystem-governance-expose-partner-observability-apis)
  - [Document federated ingestion patterns](#ecosystem-governance-document-federated-ingestion-patterns)
  - [Enforce data residency controls](#ecosystem-governance-enforce-data-residency-controls)
  - [Integrate policy DSL with GRC systems](#ecosystem-governance-integrate-policy-dsl-with-grc-systems)
  - [Provide auditor APIs](#ecosystem-governance-provide-auditor-apis)
  - [Automate rollback verification](#ecosystem-governance-automate-rollback-verification)
  - [Support specialty LLM integrations](#ecosystem-governance-support-specialty-llm-integrations)
  - [Tune hardware acceleration profiles](#ecosystem-governance-tune-hardware-acceleration-profiles)
  - [Deliver differential privacy tooling](#ecosystem-governance-deliver-differential-privacy-tooling)
  - [Create enterprise installers](#ecosystem-governance-create-enterprise-installers)
  - [Build managed service marketplace pipelines](#ecosystem-governance-build-managed-service-marketplace-pipelines)
- [Cross-Cutting Initiatives](#cross-cutting-initiatives)
  - [Run continuous threat modeling](#cross-cutting-run-continuous-threat-modeling)
  - [Conduct penetration testing cycles](#cross-cutting-conduct-penetration-testing-cycles)
  - [Enforce secrets hygiene](#cross-cutting-enforce-secrets-hygiene)
  - [Align with regulatory frameworks](#cross-cutting-align-with-regulatory-frameworks)
  - [Maintain contract test suites](#cross-cutting-maintain-contract-test-suites)
  - [Expand integration testing](#cross-cutting-expand-integration-testing)
  - [Generate synthetic datasets](#cross-cutting-generate-synthetic-datasets)
  - [Automate load and chaos testing](#cross-cutting-automate-load-and-chaos-testing)
  - [Publish system diagrams](#cross-cutting-publish-system-diagrams)
  - [Provide migration guides](#cross-cutting-provide-migration-guides)
  - [Set contribution standards](#cross-cutting-set-contribution-standards)
  - [Monitor risk telemetry](#cross-cutting-monitor-risk-telemetry)
  - [Stage incremental rollouts](#cross-cutting-stage-incremental-rollouts)

## Experiences

Experiences are configurable, named compositions of prompts, plugins, agents, and (future) workflows that deliver a specific user-facing outcome (for example, Morning Briefing, Inbox Triage, or Meeting Co-Pilot).

Implementation Status: Partial
- Morning Briefing exists as a hard-coded orchestrator plus plugin calls.
- There is no general-purpose Experience registry/definition model or editor yet.
- Workflow engine integration, approval gates, and execution history for experiences are still TODO.

Near-term plan:
- Define an Experience schema and registry tied to plugins, prompts, and workflow definitions.
- Surface an admin-only Experience management UI (create/update/publish/retire experiences).
- Migrate existing flows (Morning Briefing, Inbox Triage sketches) onto the Experience abstraction.

--

## Roadmap Status

Status legend: **Complete** (implemented in current codebase), **In Progress** (partially implemented or implemented with notable gaps), **TODO** (no implementation found).

### Phase 1 - Foundation Hardening - Status

| Feature | Status | Related Tasks |
| -- | -- | -- |
| [Finalize FastAPI service boundaries](#foundation-hardening-finalize-fastapi-service-boundaries) | Complete | [SHU-249 API Routers SoC](./tasks/SHU-28-CODE-QUALITY/SHU-249-Separation-of-Concerns-API-Routers-and-Schemas.md) |
| [Establish async SQLAlchemy usage](#foundation-hardening-establish-async-sqlalchemy-usage) | Complete | None documented |
| [Define pgvector schema](#foundation-hardening-define-pgvector-schema) | Complete | [SHU-157 KB RAG](./tasks/SHU-2-KNOWLEDGE-BASE/SHU-157-KB-RAG-Full-Document-Escalation.md) |
| [Implement DB-backed scheduling](#foundation-hardening-implement-db-backed-scheduling) | Complete | [SHU-234 In-Process Scheduler](./tasks/SHU-27-BACKGROUND-SCHEDULER/SHU-234-Plugin-Feeds-In-Process-Scheduler.md) |
| [Enforce structured logging](#foundation-hardening-enforce-structured-logging) | Complete | [SHU-242 Tracebacks Always On](./tasks/SHU-28-CODE-QUALITY/SHU-242-Tracebacks-Always-On.md) |
| [Provide unified audit trails](#foundation-hardening-provide-unified-audit-trails) | TODO | [SHU-61 Granular Plugin Permissions](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-61-Granular-Plugin-Permissions-and-Data-Governance.md), [SHU-55 Feed Audit Events](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-55-Feed-Audit-Events-Plugin-Writes-and-Transitions.md) |
| [Complete plugin manifest schema](#foundation-hardening-complete-plugin-manifest-schema) | In Progress | [SHU-328 Plugin Schema v1](./tasks/SHU-6-PLATFORM-CONTRACTS/SHU-328-Plugin-Schema-v1.md), [SHU-53 Core Plugin Interface](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-53-Core-Plugin-Interface.md) |
| [Lock deterministic execution sandbox](#foundation-hardening-lock-deterministic-execution-sandbox) | Complete | [SHU-74 Plugin Isolation](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-74-Plugin-Isolation-Modal-Probes-Refactor.md), [SHU-323 Host Capabilities Enforcement](./tasks/SHU-6-PLATFORM-CONTRACTS/SHU-323-HostCapabilities-and-Loader-Enforcement.md) |
| [Operate secrets and identity broker](#foundation-hardening-operate-secrets-and-identity-broker) | Complete | [SHU-42 Host Capabilities Modularization](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-42-Host-Capabilities-Modularization-and-Cleanup.md) |
| [Stabilize feed scheduler for bulk and delta syncs](#foundation-hardening-stabilize-feed-scheduler-for-bulk-and-delta-syncs) | In Progress | [SHU-234 Feeds Scheduler](./tasks/SHU-27-BACKGROUND-SCHEDULER/SHU-234-Plugin-Feeds-In-Process-Scheduler.md), [SHU-231 Concurrency & Idempotency](./tasks/SHU-27-BACKGROUND-SCHEDULER/SHU-231-Concurrency-Claims-and-Idempotency.md) |
| [Publish reference workspace connectors](#foundation-hardening-publish-reference-workspace-connectors) | Complete | [SHU-62 Migrate Gmail Preview](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-62-Migrate-Preview-To-Plugin-Ops-Remove-Gmail-Router-Processor.md), [SHU-39 GChat Digest](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-39-GChat-Digest-Plugin.md), [SHU-43 Calendar Plugin](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-43-Calendar-Integration-Plugin.md) |
| [Deliver OCR pipeline](#foundation-hardening-deliver-ocr-pipeline) | Complete | [SHU-156 Document Ingestion & OCR](./tasks/SHU-2-KNOWLEDGE-BASE/SHU-156-Document-Ingestion-and-OCR-Pipeline.md) |
| [Standardize sentence-transformer embeddings](#foundation-hardening-standardize-sentence-transformer-embeddings) | Complete | [SHU-333 Multi-Provider LLM Integration](./tasks/SHU-7-PLATFORM-INFRA/SHU-333-Multi-Provider-LLM-Integration.md) |
| [Implement dedupe and classification stages](#foundation-hardening-implement-dedupe-and-classification-stages) | In Progress | [SHU-155 KB Indexing Non-RAG](./tasks/SHU-2-KNOWLEDGE-BASE/SHU-155-KB-Indexing-Non-RAG-Retrieval-v0.md) |
| [Expose typed retrieval APIs](#foundation-hardening-expose-typed-retrieval-apis) | Complete | [SHU-157 KB RAG](./tasks/SHU-2-KNOWLEDGE-BASE/SHU-157-KB-RAG-Full-Document-Escalation.md) |
| [Persist workflow state for resumability](#foundation-hardening-persist-workflow-state-for-resumability) | TODO | [SHU-207 Workflow Execution Engine](./tasks/SHU-23-WORKFLOW-ENGINE/SHU-207-Workflow-Execution-Engine.md) |
| [Honor approval gates end-to-end](#foundation-hardening-honor-approval-gates-end-to-end) | TODO | [SHU-61 Granular Plugin Permissions](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-61-Granular-Plugin-Permissions-and-Data-Governance.md), [SHU-203 Human-In-The-Loop](./tasks/SHU-23-WORKFLOW-ENGINE/SHU-203-Human-In-The-Loop-Integration.md) |
| [Enforce JSON-schema LLM outputs](#foundation-hardening-enforce-json-schema-llm-outputs) | TODO | [SHU-327 Prompt Contract v1](./tasks/SHU-6-PLATFORM-CONTRACTS/SHU-327-Prompt-Contract-v1.md) |
| [Emit audit events for orchestrated actions](#foundation-hardening-emit-audit-events-for-orchestrated-actions) | TODO | [SHU-135 Agent Orchestration Engine](./tasks/SHU-18-AGENT-FOUNDATION/SHU-135-Agent-Orchestration-Engine.md) |
| [Package single-machine install](#foundation-hardening-package-single-machine-install) | Complete | [SHU-178 Docker Compose Install](./tasks/SHU-21-USABILITY/SHU-178-Docker-Compose-from-scratch-install.md) |
| [Bundle Postgres, Redis, and Ollama option](#foundation-hardening-bundle-postgres-redis-and-ollama-option) | In Progress | [SHU-98 Kubernetes Production](./tasks/SHU-14-PRODUCTION-DEPLOYMENT/SHU-98-Kubernetes-Production.md), [SHU-208 Docker Compose Alignment](./tasks/SHU-24-adaptive-deployment-platform/SHU-208-Align-Docker-Compose-dev-stack-with-adaptive-deployment-architecture.md) |
| [Provide scheduler workers and health checks](#foundation-hardening-provide-scheduler-workers-and-health-checks) | In Progress | [SHU-232 Observability & Limits](./tasks/SHU-27-BACKGROUND-SCHEDULER/SHU-232-Observability-and-Limits.md), [SHU-228 Worker Mode](./tasks/SHU-27-BACKGROUND-SCHEDULER/SHU-228-Worker-Mode-Compatibility-and-Modularity.md) |

### Phase 1B - Shu RAG Intelligent Retrieval - Status

| Feature | Status | Related Tasks |
| -- | -- | -- |
| [Phase 1B Document Profile Schema](#phase-1b-document-profile-schema) | Complete | [SHU-342 Document Profile Schema](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-342-Document-Profile-Schema.md), [SHU-355 Relational Context Schema](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-355-Relational-Context-Schema.md) |
| [Phase 1B Document Profiling and Question Synthesis](#phase-1b-document-profiling-and-question-synthesis) | In Progress | [SHU-343 Document Profiling Service](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-343-Document-Profiling-Service.md) (code complete), [SHU-344 Ingestion Pipeline Integration](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-344-Ingestion-Pipeline-Integration.md) (code complete), [SHU-359 Synopsis Embedding](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-359-Synopsis-Embedding.md), [SHU-353 Question Synthesis Service](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-353-Question-Synthesis-Service.md), [SHU-351 Question Embedding and Storage](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-351-Question-Embedding-and-Storage.md), [SHU-352 Question-Match Retrieval Surface](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-352-Question-Match-Retrieval-Surface.md) |
| [Phase 1B Multi-Surface Retrieval and Score Fusion](#phase-1b-multi-surface-retrieval-and-score-fusion) | TODO | [SHU-350 Query Classification Service](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-350-Query-Classification-Service.md), [SHU-348 Multi-Surface Query Router](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-348-Multi-Surface-Query-Router.md), [SHU-358 Score Fusion Service](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-358-Score-Fusion-Service.md), [SHU-347 Manifest-Based Filtering](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-347-Manifest-Based-Filtering.md) |
| [Phase 1B Relational Context](#phase-1b-relational-context) | TODO | [SHU-341 Document Participant Extraction](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-341-Document-Participant-Extraction.md), [SHU-349 Project Association Extraction](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-349-Project-Association-Extraction.md), [SHU-354 Relational Boost Scoring](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-354-Relational-Boost-Scoring.md), [SHU-360 Temporal Relevance Scoring](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-360-Temporal-Relevance-Scoring.md); depends on [SHU-100 Extractors](./tasks/SHU-15-PROFILE-LEARNING/SHU-100-Extractors-and-Scoring.md), [SHU-103 Feature Schema](./tasks/SHU-15-PROFILE-LEARNING/SHU-103-Feature-Schema-and-Storage.md), [SHU-108 Serving APIs & Explainability](./tasks/SHU-15-PROFILE-LEARNING/SHU-108-Serving-APIs-and-Explainability.md) |
| [Phase 1B Agentic Retrieval and Invocation Policy](#phase-1b-agentic-retrieval-and-invocation-policy) | TODO | [SHU-357 Retrieval Tool Definitions](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-357-Retrieval-Tool-Definitions.md), [SHU-345 Invocation Policy Service](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-345-Invocation-Policy-Service.md), [SHU-346 Iterative Refinement Logic](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-346-Iterative-Refinement-Logic.md), [SHU-356 Retrieval Feedback Loop](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-356-Retrieval-Feedback-Loop.md); depends on [SHU-132 RAG-Plugin Integration](./tasks/SHU-18-AGENT-FOUNDATION/SHU-132-RAG-Plugin-Integration.md), [SHU-134 Agent Orchestration Service](./tasks/SHU-18-AGENT-FOUNDATION/SHU-134-Agent-Orchestration-Service.md), [SHU-140 Agent Reasoning Framework](./tasks/SHU-18-AGENT-FOUNDATION/SHU-140-Agent-Reasoning-Framework.md), [SHU-102 Learning Feedback Loop System](./tasks/SHU-15-PROFILE-LEARNING/SHU-102-Learning-Feedback-Loop-System.md) |

### Phase 2 - Adaptive Deployment Platform - Status

| Feature | Status | Related Tasks |
| -- | -- | -- |
| [Modularize services for multi-topology deploys](#adaptive-deployment-platform-modularize-services-for-multi-topology-deploys) | TODO | [SHU-212 Repository layer](./tasks/SHU-24-adaptive-deployment-platform/SHU-212-Repository-layer-for-KB-scheduler-and-workflows.md), [SHU-213 Repository rollout](./tasks/SHU-24-adaptive-deployment-platform/SHU-213-Repository-pattern-rollout-across-remaining-application-data-access.md), [SHU-214 VectorStore interface](./tasks/SHU-24-adaptive-deployment-platform/SHU-214-VectorStore-interface-for-Postgres-and-SQLite.md) |
| [Introduce dynamic work queues](#adaptive-deployment-platform-introduce-dynamic-work-queues) | TODO | [SHU-211 Queue interface](./tasks/SHU-24-Adaptive-Deployment-Platform/SHU-211-Queue-and-cache-interfaces-with-Redis-and-in-memory-backends.md), [SHU-215 Workload routing](./tasks/SHU-24-Adaptive-Deployment-Platform/SHU-215-Workload-routing-and-worker-roles-for-queues.md) |
| [Introduce unified cache abstraction](#adaptive-deployment-platform-introduce-unified-cache-abstraction) | TODO | [Unified Cache Interface](./tasks/SHU-24-Adaptive-Deployment-Platform/SHU-414-Unified-Cache-Interface-with-Redis-and-In-Memory-Backends.md) |
| [Implement workload routing patterns](#adaptive-deployment-platform-implement-workload-routing-patterns) | TODO | [SHU-215 Workload routing](./tasks/SHU-24-adaptive-deployment-platform/SHU-215-Workload-routing-and-worker-roles-for-queues.md) |
| [Publish containerized single-node dev stack](#adaptive-deployment-platform-publish-containerized-single-node-dev-stack) | Complete | [SHU-208 Docker Compose alignment](./tasks/SHU-24-adaptive-deployment-platform/SHU-208-Align-Docker-Compose-dev-stack-with-adaptive-deployment-architecture.md), [SHU-178 Docker Compose](./tasks/SHU-21-USABILITY/SHU-178-Docker-Compose-from-scratch-install.md) |
| [Publish bare-metal single-node installer](#adaptive-deployment-platform-publish-bare-metal-single-node-installer) | TODO | [SHU-209 Bare-metal installer](./tasks/SHU-24-adaptive-deployment-platform/SHU-209-Bare-metal-single-node-install-initialization-and-packaging.md) |
| [Publish containerized packaging](#adaptive-deployment-platform-publish-containerized-packaging) | In Progress | [SHU-218 Kubernetes Alpha Deployment](./tasks/SHU-25-ALPHA-USABILITY/SHU-218-Kubernetes-Alpha-Deployment.md), [SHU-98 Kubernetes Production](./tasks/SHU-14-PRODUCTION-DEPLOYMENT/SHU-98-Kubernetes-Production.md) |
| [Document migration between deployment modes](#adaptive-deployment-platform-document-migration-between-deployment-modes) | TODO | [SHU-210 Datasource migration](./tasks/SHU-24-adaptive-deployment-platform/SHU-210-Datasource-migration-and-configuration-parity-across-deployment-modes.md) |
| [Adopt queue abstraction for scheduler](#adaptive-deployment-platform-adopt-queue-abstraction-for-scheduler) | TODO | [SHU-211 Queue interface](./tasks/SHU-24-Adaptive-Deployment-Platform/SHU-211-Queue-and-cache-interfaces-with-Redis-and-in-memory-backends.md) |
| [Maintain configuration parity](#adaptive-deployment-platform-maintain-configuration-parity) | In Progress | [SHU-210 Datasource migration](./tasks/SHU-24-adaptive-deployment-platform/SHU-210-Datasource-migration-and-configuration-parity-across-deployment-modes.md), [SHU-335 Config Source Priorities](./tasks/SHU-7-PLATFORM-INFRA/SHU-335-Config-Source-Priorities.md) |
| [Implement user API key system](#adaptive-deployment-platform-implement-user-api-key-system) | In Progress | [SHU-121 User API Key System](./tasks/SHU-17-SECURITY-HARDENING/SHU-121-User-API-Key-System.md), [SHU-120 API Key Database Schema](./tasks/SHU-17-SECURITY-HARDENING/SHU-120-API-Key-Database-Schema.md) |
| [Provide API key management endpoints](#adaptive-deployment-platform-provide-api-key-management-endpoints) | TODO | [SHU-118 API Key Management Endpoints](./tasks/SHU-17-SECURITY-HARDENING/SHU-118-API-Key-Management-Endpoints.md) |
| [Document SDK and APIs](#adaptive-deployment-platform-document-sdk-and-apis) | In Progress | [EXTERNAL_CLIENT_INTEGRATION.md](./EXTERNAL_CLIENT_INTEGRATION.md) |

### Phase 3 - Agentic Platform - Status

Phase 3 is divided into three focused sub-initiatives to reduce coupling and enable parallel progress.

#### Phase 3A - Worker and Observability Infrastructure

| Feature | Status | Related Tasks |
| -- | -- | -- |
| [Add event bus abstraction](#agentic-platform-add-event-bus-abstraction) | TODO | [SHU-130 Event-Driven Architecture](./tasks/SHU-18-AGENT-FOUNDATION/SHU-130-Event-Driven-Architecture.md) |
| [Stand up dedicated worker pools](#agentic-platform-stand-up-dedicated-worker-pools) | TODO | [SHU-228 Worker Mode](./tasks/SHU-27-BACKGROUND-SCHEDULER/SHU-228-Worker-Mode-Compatibility-and-Modularity.md) |
| [Export observability signals](#agentic-platform-export-observability-signals) | TODO | [SHU-330 SRE & Scalability Baseline](./tasks/SHU-7-PLATFORM-INFRA/SHU-330-SRE-and-Scalability-Baseline.md) |

#### Phase 3B - Experience System and Workflows

| Feature | Status | Related Tasks |
| -- | -- | -- |
| [Define Experience schema and registry](#agentic-platform-define-experience-schema-and-registry) | TODO | [SHU-148 Experience Creator v0](./tasks/SHU-19-EXPERIENCE-PLATFORM/SHU-148-Experience-Creator-v0.md) |
| [Build admin Experience management UI](#agentic-platform-build-admin-experience-management-ui) | TODO | [SHU-148 Experience Creator v0](./tasks/SHU-19-EXPERIENCE-PLATFORM/SHU-148-Experience-Creator-v0.md) |
| [Migrate hard-coded orchestrations to Experience abstraction](#agentic-platform-migrate-hardcoded-orchestrations-to-experience-abstraction) | TODO | [SHU-149 Morning Briefing Experience](./tasks/SHU-19-EXPERIENCE-PLATFORM/SHU-149-Morning-Briefing-Experience.md) |
| [Publish declarative workflow DSL](#agentic-platform-publish-declarative-workflow-dsl) | TODO | [SHU-202 Workflow Definition System](./tasks/SHU-23-WORKFLOW-ENGINE/SHU-202-Workflow-Definition-System.md) |
| [Deliver workflow step library](#agentic-platform-deliver-workflow-step-library) | TODO | [SHU-207 Workflow Execution Engine](./tasks/SHU-23-WORKFLOW-ENGINE/SHU-207-Workflow-Execution-Engine.md) |
| [Ship policy and approval interface](#agentic-platform-ship-policy-and-approval-interface) | TODO | [SHU-338 Policy Controller](./tasks/SHU-8-PLATFORM-REGISTRIES-POLICY/SHU-338-Policy-Controller.md), [SHU-203 Human-In-The-Loop](./tasks/SHU-23-WORKFLOW-ENGINE/SHU-203-Human-In-The-Loop-Integration.md) |
| [Activate profile graph entities](#agentic-platform-activate-profile-graph-entities) | TODO | [SHU-103 Profile Feature Schema](./tasks/SHU-15-PROFILE-LEARNING/SHU-103-Feature-Schema-and-Storage.md) |
| [Expose profile read/write APIs](#agentic-platform-expose-profile-readwrite-apis) | TODO | [SHU-108 Serving APIs & Explainability](./tasks/SHU-15-PROFILE-LEARNING/SHU-108-Serving-APIs-and-Explainability.md) |

#### Phase 3C - Plugin Governance and LLM Hardening

| Feature | Status | Related Tasks |
| -- | -- | -- |
| [Introduce capability-scoped plugin actions](#agentic-platform-introduce-capability-scoped-plugin-actions) | TODO | [SHU-64 Plugin Permission System](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-64-Plugin-Permission-System.md) |
| [Provide host-managed HTTP client](#agentic-platform-provide-host-managed-http-client) | Complete | [SHU-42 Host Capabilities Modularization](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-42-Host-Capabilities-Modularization-and-Cleanup.md) |
| [Implement plugin registry lifecycle](#agentic-platform-implement-plugin-registry-lifecycle) | In Progress | [SHU-337 Plugin Registry CRUD](./tasks/SHU-8-PLATFORM-REGISTRIES-POLICY/SHU-337-Plugin-Registry-CRUD.md), [SHU-48 Plugin Package Upload](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-48-Plugin-Package-Upload-and-Installation.md) |
| [Capture plugin artifact lineage](#agentic-platform-capture-plugin-artifact-lineage) | TODO | None documented |
| [Consume external MCP servers](#agentic-platform-consume-external-mcp-servers) | TODO | [SHU-87 MCP Server Consumption Adapter](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-87-MCP-Server-Consumption-Adapter.md) |
| [Publish Plugin SDK CLI](#agentic-platform-publish-plugin-sdk-cli) | TODO | [Plugin Ingestion Interface](./tasks/SHU-9-PLUGIN-SDK/Plugin-Ingestion-Interface.md), [CLI Scaffold and Validators](./tasks/SHU-9-PLUGIN-SDK/CLI-Scaffold-and-Validators.md), [SDK Utilities Library](./tasks/SHU-9-PLUGIN-SDK/SDK-Utilities-Library.md) |
| [Publish Plugin SDK test harness](#agentic-platform-publish-plugin-sdk-test-harness) | TODO | [Contract Test Runner](./tasks/SHU-9-PLUGIN-SDK/Contract-Test-Runner.md) |
| [Implement plugin signing and allow-list](#agentic-platform-implement-plugin-signing-and-allow-list) | TODO | [Plugin Signing and Allow-List](./tasks/SHU-9-PLUGIN-SDK/Plugin-Signing-and-Allow-List.md) |
| [Add provider health probes](#agentic-platform-add-provider-health-probes) | TODO | None documented |
| [Normalize LLM parameters](#agentic-platform-normalize-llm-parameters) | Complete | [SHU-285 Parameter Normalization](./tasks/SHU-3-LLM-PROVIDER-GENERALIZATION/SHU-285-Parameter-Normalization-and-Mapping-Layer.md) |
| [Implement LLM fallback logic](#agentic-platform-implement-llm-fallback-logic) | In Progress | [SHU-288 UnifiedLLMClient Routing Updates](./tasks/SHU-3-LLM-PROVIDER-GENERALIZATION/SHU-288-UnifiedLLMClient-Routing-Updates.md) |
| [Enable local model pinning](#agentic-platform-enable-local-model-pinning) | TODO | None documented |
| [Track LLM usage and budgets](#agentic-platform-track-llm-usage-and-budgets) | TODO | [SHU-222 Limits & Quotas Monitoring](./tasks/SHU-26-ANALYTICS-MONITORING/SHU-222-Limits-Quotas-Aggregation-and-Monitoring.md) |

### Phase 4 - Intelligence Expansion - Status

| Feature | Status | Related Tasks |
| -- | -- | -- |
| [Scale ingestion with stream processing](#intelligence-expansion-scale-ingestion-with-stream-processing) | TODO | None documented |
| [Apply tenant-aware rate limiting](#intelligence-expansion-apply-tenant-aware-rate-limiting) | TODO | [SHU-330 SRE & Scalability Baseline](./tasks/SHU-7-PLATFORM-INFRA/SHU-330-SRE-and-Scalability-Baseline.md) |
| [Add trace sampling controls](#intelligence-expansion-add-trace-sampling-controls) | TODO | None documented |
| [Release CRM connectors](#intelligence-expansion-release-crm-connectors) | TODO | None documented |
| [Release project tracking connectors](#intelligence-expansion-release-project-tracking-connectors) | TODO | [SHU-113 Jira Connector Plugin](./tasks/SHU-16-REFERENCE-PLUGINS/SHU-113-Jira-Connector-Plugin.md) |
| [Release incident response connectors](#intelligence-expansion-release-incident-response-connectors) | TODO | None documented |
| [Release finance connectors](#intelligence-expansion-release-finance-connectors) | TODO | None documented |
| [Instrument plugin execution telemetry](#intelligence-expansion-instrument-plugin-execution-telemetry) | TODO | [SHU-232 Observability & Limits](./tasks/SHU-27-BACKGROUND-SCHEDULER/SHU-232-Observability-and-Limits.md) |
| [Monitor SLAs for plugin workloads](#intelligence-expansion-monitor-slas-for-plugin-workloads) | TODO | [SHU-330 SRE & Scalability Baseline](./tasks/SHU-7-PLATFORM-INFRA/SHU-330-SRE-and-Scalability-Baseline.md) |
| [Create feature stores](#intelligence-expansion-create-feature-stores) | TODO | [SHU-103 Profile Feature Schema](./tasks/SHU-15-PROFILE-LEARNING/SHU-103-Feature-Schema-and-Storage.md) |
| [Generate storyline synthesis pipelines](#intelligence-expansion-generate-storyline-synthesis-pipelines) | TODO | [SHU-144 Multi-Modal Content Understanding](./tasks/SHU-19-EXPERIENCE-PLATFORM/SHU-144-Multi-Modal-Content-Understanding.md) |
| [Surface contextual intelligence APIs](#intelligence-expansion-surface-contextual-intelligence-apis) | TODO | [SHU-108 Serving APIs & Explainability](./tasks/SHU-15-PROFILE-LEARNING/SHU-108-Serving-APIs-and-Explainability.md) |
| [Support event-triggered workflows](#intelligence-expansion-support-event-triggered-workflows) | TODO | [SHU-205 Workflow Triggers & Scheduling](./tasks/SHU-23-WORKFLOW-ENGINE/SHU-205-Workflow-Triggers-Scheduling.md) |
| [Add compensation handlers](#intelligence-expansion-add-compensation-handlers) | TODO | None documented |
| [Enforce observability budgets](#intelligence-expansion-enforce-observability-budgets) | TODO | None documented |
| [Introduce workflow circuit breakers](#intelligence-expansion-introduce-workflow-circuit-breakers) | TODO | None documented |
| [Build LLM regression suites](#intelligence-expansion-build-llm-regression-suites) | TODO | [SHU-287 Testing Mapping/Streaming](./tasks/SHU-3-LLM-PROVIDER-GENERALIZATION/SHU-287-Testing-Mapping-Discovery-Streaming.md) |
| [Detect LLM determinism drift](#intelligence-expansion-detect-llm-determinism-drift) | TODO | None documented |
| [Deploy redaction filters](#intelligence-expansion-deploy-redaction-filters) | TODO | None documented |
| [Provide fine-tuning hooks](#intelligence-expansion-provide-fine-tuning-hooks) | TODO | None documented |
| [Launch feed health dashboards](#intelligence-expansion-launch-feed-health-dashboards) | TODO | [SHU-44 Feed Health Monitoring](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-44-Feed-Health-Monitoring-and-Alerts.md) |
| [Detect data and policy drift](#intelligence-expansion-detect-data-and-policy-drift) | TODO | None documented |
| [Run chaos drills for worker pools](#intelligence-expansion-run-chaos-drills-for-worker-pools) | TODO | None documented |

### Phase 5 - Operating-System Scale - Status

| Feature | Status | Related Tasks |
| -- | -- | -- |
| [Support multi-region tenancy](#operating-system-scale-support-multi-region-tenancy) | TODO | None documented |
| [Implement storage sharding strategies](#operating-system-scale-implement-storage-sharding-strategies) | TODO | None documented |
| [Automate secrets rotation](#operating-system-scale-automate-secrets-rotation) | TODO | [SHU-121 User API Key System](./tasks/SHU-17-SECURITY-HARDENING/SHU-121-User-API-Key-System.md) |
| [Offer pluggable storage backends](#operating-system-scale-offer-pluggable-storage-backends) | TODO | [SHU-214 VectorStore interface](./tasks/SHU-24-adaptive-deployment-platform/SHU-214-VectorStore-interface-for-Postgres-and-SQLite.md) |
| [Launch plugin marketplace surfaces](#operating-system-scale-launch-plugin-marketplace-surfaces) | TODO | [SHU-92 Plugin Discovery & Marketplace](./tasks/SHU-12-PLUGIN-MARKETPLACE/SHU-92-Plugin-Discovery-and-Marketplace.md), [SHU-88 Setup Wizard Framework](./tasks/SHU-12-PLUGIN-MARKETPLACE/SHU-88-Setup-Wizard-Framework.md) |
| [Deliver plugin recommendation engine](#operating-system-scale-deliver-plugin-recommendation-engine) | TODO | [SHU-89 Plugin Recommendation Engine](./tasks/SHU-12-PLUGIN-MARKETPLACE/SHU-89-Plugin-Recommendation-Engine.md) |
| [Model plugin dependency graph](#operating-system-scale-model-plugin-dependency-graph) | TODO | None documented |
| [Run auto-upgrade channels](#operating-system-scale-run-auto-upgrade-channels) | TODO | None documented |
| [Filter discovery with policy awareness](#operating-system-scale-filter-discovery-with-policy-awareness) | TODO | [SHU-64 Plugin Permission System](./tasks/SHU-11-PLUGIN-ECOSYSTEM/SHU-64-Plugin-Permission-System.md) |
| [Expose organizational memory APIs](#operating-system-scale-expose-organizational-memory-apis) | TODO | [SHU-108 Serving APIs & Explainability](./tasks/SHU-15-PROFILE-LEARNING/SHU-108-Serving-APIs-and-Explainability.md) |
| [Provide cross-source analytics surfaces](#operating-system-scale-provide-cross-source-analytics-surfaces) | TODO | [SHU-224 User Behavior Tracking](./tasks/SHU-26-ANALYTICS-MONITORING/SHU-224-User-Behavior-Tracking.md), [SHU-166 Team Analytics Dashboard](./tasks/SHU-20-TEAM-COLLABORATION/SHU-166-Team-Analytics-Dashboard.md) |
| [Emit anomaly alerts](#operating-system-scale-emit-anomaly-alerts) | TODO | None documented |
| [Automate workflow contract testing](#operating-system-scale-automate-workflow-contract-testing) | TODO | None documented |
| [Manage collaboration states](#operating-system-scale-manage-collaboration-states) | TODO | [SHU-165 Team Context & Membership](./tasks/SHU-20-TEAM-COLLABORATION/SHU-165-Team-Context-and-Membership-System.md), [SHU-142 Collaborative AI Workspace](./tasks/SHU-19-EXPERIENCE-PLATFORM/SHU-142-Collaborative-AI-Workspace.md) |
| [Maintain commitments ledger](#operating-system-scale-maintain-commitments-ledger) | TODO | None documented |
| [Balance LLM latency, cost, and accuracy](#operating-system-scale-balance-llm-latency-cost-and-accuracy) | TODO | [SHU-330 SRE & Scalability Baseline](./tasks/SHU-7-PLATFORM-INFRA/SHU-330-SRE-and-Scalability-Baseline.md) |
| [Enable parallel tool planning](#operating-system-scale-enable-parallel-tool-planning) | TODO | None documented |
| [Ship guardrail templates](#operating-system-scale-ship-guardrail-templates) | TODO | [SHU-338 Policy Controller](./tasks/SHU-8-PLATFORM-REGISTRIES-POLICY/SHU-338-Policy-Controller.md) |
| [Publish multi-cluster reference architectures](#operating-system-scale-publish-multi-cluster-reference-architectures) | TODO | [SHU-98 Kubernetes Production](./tasks/SHU-14-PRODUCTION-DEPLOYMENT/SHU-98-Kubernetes-Production.md) |
| [Provide SRE runbooks](#operating-system-scale-provide-sre-runbooks) | TODO | [SHU-330 SRE & Scalability Baseline](./tasks/SHU-7-PLATFORM-INFRA/SHU-330-SRE-and-Scalability-Baseline.md) |
| [Author upgrade playbooks](#operating-system-scale-author-upgrade-playbooks) | TODO | None documented |
| [Compile compliance reporting packs](#operating-system-scale-compile-compliance-reporting-packs) | TODO | None documented |

### Phase 6 - Ecosystem Governance - Status

| Feature | Status | Related Tasks |
| -- | -- | -- |
| [Guarantee API stability windows](#ecosystem-governance-guarantee-api-stability-windows) | TODO | None documented |
| [Publish extension hooks](#ecosystem-governance-publish-extension-hooks) | TODO | None documented |
| [Introduce plugin revenue sharing](#ecosystem-governance-introduce-plugin-revenue-sharing) | TODO | None documented |
| [Issue certification badges](#ecosystem-governance-issue-certification-badges) | TODO | None documented |
| [Support plugin attestation flows](#ecosystem-governance-support-plugin-attestation-flows) | TODO | None documented |
| [Offer telemetry opt-in/out controls](#ecosystem-governance-offer-telemetry-opt-inout-controls) | TODO | None documented |
| [Expose partner observability APIs](#ecosystem-governance-expose-partner-observability-apis) | TODO | None documented |
| [Document federated ingestion patterns](#ecosystem-governance-document-federated-ingestion-patterns) | TODO | None documented |
| [Enforce data residency controls](#ecosystem-governance-enforce-data-residency-controls) | TODO | None documented |
| [Integrate policy DSL with GRC systems](#ecosystem-governance-integrate-policy-dsl-with-grc-systems) | TODO | None documented |
| [Provide auditor APIs](#ecosystem-governance-provide-auditor-apis) | TODO | None documented |
| [Automate rollback verification](#ecosystem-governance-automate-rollback-verification) | TODO | None documented |
| [Support specialty LLM integrations](#ecosystem-governance-support-specialty-llm-integrations) | TODO | None documented |
| [Tune hardware acceleration profiles](#ecosystem-governance-tune-hardware-acceleration-profiles) | TODO | None documented |
| [Deliver differential privacy tooling](#ecosystem-governance-deliver-differential-privacy-tooling) | TODO | None documented |
| [Create enterprise installers](#ecosystem-governance-create-enterprise-installers) | TODO | [SHU-209 Bare-metal installer](./tasks/SHU-24-adaptive-deployment-platform/SHU-209-Bare-metal-single-node-install-initialization-and-packaging.md) |
| [Build managed service marketplace pipelines](#ecosystem-governance-build-managed-service-marketplace-pipelines) | TODO | None documented |

### Cross-Cutting Status

| Feature | Status | Related Tasks |
| -- | -- | -- |
| [Run continuous threat modeling](#cross-cutting-run-continuous-threat-modeling) | TODO | None documented |
| [Conduct penetration testing cycles](#cross-cutting-conduct-penetration-testing-cycles) | TODO | None documented |
| [Enforce secrets hygiene](#cross-cutting-enforce-secrets-hygiene) | In Progress | [SHU-121 User API Key System](./tasks/SHU-17-SECURITY-HARDENING/SHU-121-User-API-Key-System.md) |
| [Align with regulatory frameworks](#cross-cutting-align-with-regulatory-frameworks) | TODO | [SHU-13-PRIVACY-ARCHITECTURE EPIC](./tasks/SHU-13-PRIVACY-ARCHITECTURE/EPIC.md) |
| [Maintain contract test suites](#cross-cutting-maintain-contract-test-suites) | In Progress | [SHU-253 Integration Test Framework](./tasks/SHU-28-CODE-QUALITY/SHU-253-Integration-Test-Framework.md) |
| [Expand integration testing](#cross-cutting-expand-integration-testing) | In Progress | [SHU-235 Pytest Fixture Design](./tasks/SHU-28-CODE-QUALITY/SHU-235-Pytest-Fixture-Design.md), [SHU-243 Frontend E2E](./tasks/SHU-28-CODE-QUALITY/SHU-243-Frontend-E2E-Testing-Framework.md) |
| [Generate synthetic datasets](#cross-cutting-generate-synthetic-datasets) | TODO | None documented |
| [Automate load and chaos testing](#cross-cutting-automate-load-and-chaos-testing) | TODO | None documented |
| [Publish system diagrams](#cross-cutting-publish-system-diagrams) | In Progress | None documented |
| [Provide migration guides](#cross-cutting-provide-migration-guides) | TODO | [SHU-210 Datasource migration](./tasks/SHU-24-adaptive-deployment-platform/SHU-210-Datasource-migration-and-configuration-parity-across-deployment-modes.md) |
| [Set contribution standards](#cross-cutting-set-contribution-standards) | TODO | None documented |
| [Monitor risk telemetry](#cross-cutting-monitor-risk-telemetry) | TODO | None documented |
| [Stage incremental rollouts](#cross-cutting-stage-incremental-rollouts) | TODO | None documented |

### Roadmap Gap Log (Next Audit Targets)

**Phase 1 Gaps:**
- [Enforce JSON-schema LLM outputs](#foundation-hardening-enforce-json-schema-llm-outputs): LLM service lacks JSON-schema enforcement; only plugin executor validates.
- [Provide unified audit trails](#foundation-hardening-provide-unified-audit-trails): No centralized audit event pipeline.
- [Bundle Postgres, Redis, and Ollama option](#foundation-hardening-bundle-postgres-redis-and-ollama-option): Ollama service not bundled in Compose stack.

**Phase 2 Gaps:**
- [Introduce dynamic work queues](#adaptive-deployment-platform-introduce-dynamic-work-queues): SHU-211 defines QueueBackend interface with Redis and in-memory backends; implementation not started.
- [Introduce unified cache abstraction](#adaptive-deployment-platform-introduce-unified-cache-abstraction): Unified-Cache-Interface task defines CacheBackend interface to consolidate all cache implementations; implementation not started.
- [Adopt queue abstraction for scheduler](#adaptive-deployment-platform-adopt-queue-abstraction-for-scheduler): Migrate Phase 1 DB-backed scheduler to QueueBackend interface.
- [Implement workload routing patterns](#adaptive-deployment-platform-implement-workload-routing-patterns): No workload tagging or routing layer.
- [Document migration between deployment modes](#adaptive-deployment-platform-document-migration-between-deployment-modes): No migration documentation.

**Phase 3 Gaps:**
- Phase 3A: [Add event bus abstraction](#agentic-platform-add-event-bus-abstraction), [Stand up dedicated worker pools](#agentic-platform-stand-up-dedicated-worker-pools), [Export observability signals](#agentic-platform-export-observability-signals) - all TODO.
- Phase 3B: [Define Experience schema and registry](#agentic-platform-define-experience-schema-and-registry), [Build admin Experience management UI](#agentic-platform-build-admin-experience-management-ui), [Migrate hard-coded orchestrations to Experience abstraction](#agentic-platform-migrate-hardcoded-orchestrations-to-experience-abstraction) - all TODO; Morning Briefing is hard-coded.
- Phase 3C: [Capture plugin artifact lineage](#agentic-platform-capture-plugin-artifact-lineage), [Consume external MCP servers](#agentic-platform-consume-external-mcp-servers), [Add provider health probes](#agentic-platform-add-provider-health-probes), [Enable local model pinning](#agentic-platform-enable-local-model-pinning) - all TODO.

**Phase 4 Gaps (all TODO):**
- Stream ingestion, telemetry instrumentation, CRM/incident/finance connectors, compensation handlers, circuit breakers, drift detection, redaction filters, fine-tuning hooks.

**Phase 5 Gaps (all TODO):**
- Multi-region tenancy, storage sharding, secrets rotation automation, plugin dependency graph, auto-upgrade channels, anomaly alerts, workflow contract testing, commitments ledger, upgrade/compliance runbooks.

**Phase 6 Gaps (all TODO):**
- API stability policy, extension hooks, revenue/certification programs, telemetry consent, auditor APIs, rollback verification, specialty LLM onboarding, differential privacy tooling, enterprise installers, managed service distribution.

--

# More Information

<a id="shu-rag-intelligent-retrieval"></a>
<a id="phase-1b-shu-rag-intelligent-retrieval"></a>
## Phase 1B - Shu RAG Intelligent Retrieval

Phase 1B introduces Shu RAG intelligent retrieval as described in the
[SHU-RAG-WHITEPAPER](./whitepapers/SHU-RAG-WHITEPAPER.md) and implemented in
[SHU-339 Shu RAG Intelligent Retrieval](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/EPIC.md).

The EPIC defines the canonical Shu RAG phase breakdown and milestones (Phases
0-5, milestones M0-M5). This roadmap section groups related work into five
Phase 1B slices for readability and links those slices back to the underlying
epic phases and tasks.

<a id="phase-1b-document-profile-schema"></a>
### Phase 1B Document Profile Schema
Define and migrate schema changes for document synopsis, capability manifests, document questions, and relational context tables so Shu RAG can attach profiling and relationship metadata to each document. See [SHU-342 Document Profile Schema](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-342-Document-Profile-Schema.md) and [SHU-355 Relational Context Schema](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-355-Relational-Context-Schema.md).

<a id="phase-1b-document-profiling-and-question-synthesis"></a>
### Phase 1B Document Profiling and Question Synthesis
Use the side-caller model at ingestion time to generate document synopses, capability manifests, and hypothetical questions, wiring these calls into the KB ingestion pipeline and persisting embeddings for synopsis and questions. See [SHU-343 Document Profiling Service](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-343-Document-Profiling-Service.md), [SHU-344 Ingestion Pipeline Integration](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-344-Ingestion-Pipeline-Integration.md), [SHU-359 Synopsis Embedding](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-359-Synopsis-Embedding.md), [SHU-353 Question Synthesis Service](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-353-Question-Synthesis-Service.md), [SHU-351 Question Embedding and Storage](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-351-Question-Embedding-and-Storage.md), and [SHU-352 Question-Match Retrieval Surface](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-352-Question-Match-Retrieval-Surface.md).

<a id="phase-1b-multi-surface-retrieval-and-score-fusion"></a>
### Phase 1B Multi-Surface Retrieval and Score Fusion
Classify incoming queries, route them across question, synopsis, manifest, and chunk retrieval surfaces, and combine scores using config-driven weights so Shu can answer both direct factual and interpretive questions. See [SHU-350 Query Classification Service](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-350-Query-Classification-Service.md), [SHU-348 Multi-Surface Query Router](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-348-Multi-Surface-Query-Router.md), [SHU-358 Score Fusion Service](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-358-Score-Fusion-Service.md), and [SHU-347 Manifest-Based Filtering](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-347-Manifest-Based-Filtering.md).

<a id="phase-1b-relational-context"></a>
### Phase 1B Relational Context
Extract document participants and project associations, link them to profile entities, and apply relational and temporal boosts during retrieval so results reflect the user's relationships and current work. See [SHU-341 Document Participant Extraction](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-341-Document-Participant-Extraction.md), [SHU-349 Project Association Extraction](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-349-Project-Association-Extraction.md), [SHU-354 Relational Boost Scoring](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-354-Relational-Boost-Scoring.md), and [SHU-360 Temporal Relevance Scoring](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-360-Temporal-Relevance-Scoring.md).

<a id="phase-1b-agentic-retrieval-and-invocation-policy"></a>
### Phase 1B Agentic Retrieval and Invocation Policy
Expose Shu RAG retrieval surfaces as agent tools, implement an invocation policy that selects between traditional RAG, static Shu RAG, and agentic modes, and add iterative refinement and feedback loops bounded by configurable budgets. See [SHU-357 Retrieval Tool Definitions](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-357-Retrieval-Tool-Definitions.md), [SHU-345 Invocation Policy Service](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-345-Invocation-Policy-Service.md), [SHU-346 Iterative Refinement Logic](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-346-Iterative-Refinement-Logic.md), and [SHU-356 Retrieval Feedback Loop](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-356-Retrieval-Feedback-Loop.md).

<a id="phase-1-foundation-hardening"></a>
## Phase 1 - Foundation Hardening

<a id="foundation-hardening-finalize-fastapi-service-boundaries"></a>
### Foundation Hardening Finalize FastAPI service boundaries
Define clear module boundaries for every API surface (auth, plugins, workflows, knowledge, admin) to prevent inter-service coupling and make future modularization predictable.

<a id="foundation-hardening-establish-async-sqlalchemy-usage"></a>
### Foundation Hardening Establish async SQLAlchemy usage
Adopt consistent asynchronous session management patterns to maximize database throughput and avoid blocking the event loop, enabling higher concurrency.

<a id="foundation-hardening-define-pgvector-schema"></a>
### Foundation Hardening Define pgvector schema
Design pgvector-backed tables and indexes that support semantic search and embeddings with predictable performance, laying the groundwork for advanced retrieval.

<a id="foundation-hardening-implement-db-backed-scheduling"></a>
### Foundation Hardening Implement DB-backed scheduling
Use database row locks (`SELECT ... FOR UPDATE SKIP LOCKED`) to coordinate feed execution and background jobs in single-node deployments. Sufficient for initial deployments; queue abstraction added in Phase 2 enables horizontal scaling.

<a id="foundation-hardening-enforce-structured-logging"></a>
### Foundation Hardening Enforce structured logging
Emit logs in structured formats (JSON/text with fields) so observability stacks can parse, search, and correlate events across services from day one.

<a id="foundation-hardening-provide-unified-audit-trails"></a>
### Foundation Hardening Provide unified audit trails
Centralize audit event emission to capture who initiated actions, what data moved, and outcomes, satisfying compliance requirements and future governance hooks.

<a id="foundation-hardening-complete-plugin-manifest-schema"></a>
### Foundation Hardening Complete plugin manifest schema
Finalize manifest fields (capabilities, permissions, config) to guarantee that every plugin exposes a machine-readable contract the host can validate.

<a id="foundation-hardening-lock-deterministic-execution-sandbox"></a>
### Foundation Hardening Lock deterministic execution sandbox
Run plugin code inside a predictable sandbox with resource limits and deterministic execution paths to prevent runaway tasks and security regressions.

<a id="foundation-hardening-operate-secrets-and-identity-broker"></a>
### Foundation Hardening Operate secrets and identity broker
Provide host-managed storage and retrieval for credentials, tokens, and account mappings so plugins never handle raw secrets directly, reducing leak risk.

<a id="foundation-hardening-stabilize-feed-scheduler-for-bulk-and-delta-syncs"></a>
### Foundation Hardening Stabilize feed scheduler for bulk and delta syncs
Ensure feeds support initial backfills and watermark-based incremental sync, providing consistent freshness without duplicate processing.

<a id="foundation-hardening-publish-reference-workspace-connectors"></a>
### Foundation Hardening Publish reference workspace connectors
Ship exemplar plugins (email, calendar, files, chat) demonstrating canonical patterns and serving as testbeds for host orchestrations.

<a id="foundation-hardening-deliver-ocr-pipeline"></a>
### Foundation Hardening Deliver OCR pipeline
Integrate EasyOCR with Tesseract fallback to extract text from PDFs and images, enabling ingestion of unstructured documents with reliable accuracy.

<a id="foundation-hardening-standardize-sentence-transformer-embeddings"></a>
### Foundation Hardening Standardize sentence-transformer embeddings
Adopt a consistent embedding model (`all-MiniLM-L6-v2` or equivalent) with configurable execution modes to ensure uniform vector representations across the system.

<a id="foundation-hardening-implement-dedupe-and-classification-stages"></a>
### Foundation Hardening Implement dedupe and classification stages
Add pipeline stages that detect duplicate artifacts and tag document types, keeping the knowledge base clean and searchable.

<a id="foundation-hardening-expose-typed-retrieval-apis"></a>
### Foundation Hardening Expose typed retrieval APIs
Provide versioned APIs that return strongly typed knowledge responses so downstream experiences can depend on stable schemas.

<a id="foundation-hardening-persist-workflow-state-for-resumability"></a>
### Foundation Hardening Persist workflow state for resumability
Store orchestrator state transitions durably, enabling workflows to resume after failures or restarts without manual intervention.

<a id="foundation-hardening-honor-approval-gates-end-to-end"></a>
### Foundation Hardening Honor approval gates end-to-end
Require explicit human approvals for privileged actions and surface approval status in APIs, preventing unauthorized automation.

<a id="foundation-hardening-enforce-json-schema-llm-outputs"></a>
### Foundation Hardening Enforce JSON-schema LLM outputs
Wrap LLM calls with schema validation to guarantee deterministic payloads before workflows or plugins consume them.

<a id="foundation-hardening-emit-audit-events-for-orchestrated-actions"></a>
### Foundation Hardening Emit audit events for orchestrated actions
Log every workflow decision, tool invocation, and approval outcome to the audit stream, supporting later governance features.

<a id="foundation-hardening-package-single-machine-install"></a>
### Foundation Hardening Package single-machine install
Bundle Shu services behind a straightforward script or Compose stack to minimize friction for local development and pilot deployments.

<a id="foundation-hardening-bundle-postgres-redis-and-ollama-option"></a>
### Foundation Hardening Bundle Postgres, Redis, and Ollama option
Include preconfigured Postgres (with pgvector), Redis, and optional Ollama deployments so operators do not have to provision supporting systems separately for evaluation.

<a id="foundation-hardening-provide-scheduler-workers-and-health-checks"></a>
### Foundation Hardening Provide scheduler workers and health checks
Start background workers automatically and expose health endpoints, enabling simple monitoring even in single-node environments.

<a id="phase-2-adaptive-deployment-platform"></a>
## Phase 2 - Adaptive Deployment Platform

Phase 2 enables Shu to run in three deployment profiles from a single codebase: a packaged single-node install for non-technical users, a Docker Compose stack for developers, and a horizontally scalable Kubernetes deployment for organizations.

<a id="adaptive-deployment-platform-modularize-services-for-multi-topology-deploys"></a>
### Adaptive Deployment Modularize services for multi-topology deploys
Separate runtime components (API, workers, ingestion) into modules with stable interfaces, allowing the same code to run as a single process or across multiple containers.

<a id="adaptive-deployment-platform-introduce-dynamic-work-queues"></a>
### Adaptive Deployment Introduce dynamic work queues
Provide a queue abstraction with Redis and in-memory backends so background jobs work identically in single-node and distributed deployments.

<a id="adaptive-deployment-platform-introduce-unified-cache-abstraction"></a>
### Adaptive Deployment Introduce unified cache abstraction
Provide a cache abstraction with Redis and in-memory backends that consolidates all existing cache implementations (plugin host cache, config cache, rate limiting) into a single CacheBackend interface, enabling the platform to run without Redis while maintaining full functionality.

<a id="adaptive-deployment-platform-implement-workload-routing-patterns"></a>
### Adaptive Deployment Implement workload routing patterns
Tag jobs by workload type (feeds, LLM workflows, maintenance) and route them to named queues, enabling horizontal scaling by adding worker replicas per role.

<a id="adaptive-deployment-platform-publish-containerized-single-node-dev-stack"></a>
### Adaptive Deployment Publish containerized single-node dev stack
Finalize Docker Compose as the canonical developer profile with Postgres, Redis, and the same abstractions used in other modes.

<a id="adaptive-deployment-platform-publish-bare-metal-single-node-installer"></a>
### Adaptive Deployment Publish bare-metal single-node installer
Deliver a packaged installer (Homebrew, Chocolatey) that bundles the API, workers, and frontend behind a single CLI entry point.

<a id="adaptive-deployment-platform-publish-containerized-packaging"></a>
### Adaptive Deployment Publish containerized packaging
Provide Kubernetes manifests and charts for production deployments with health probes, rolling updates, and worker scaling.

<a id="adaptive-deployment-platform-document-migration-between-deployment-modes"></a>
### Adaptive Deployment Document migration between deployment modes
Document how to migrate between deployment profiles, including database and queue backend transitions.

<a id="adaptive-deployment-platform-adopt-queue-abstraction-for-scheduler"></a>
### Adaptive Deployment Adopt queue abstraction for scheduler
Migrate the Phase 1 DB-backed scheduler to use the QueueBackend interface, enabling distributed worker pools and Redis-backed job queues for horizontal scaling.

<a id="adaptive-deployment-platform-maintain-configuration-parity"></a>
### Adaptive Deployment Maintain configuration parity
Keep environment variables and config files consistent across all deployment modes so features behave identically everywhere.

<a id="adaptive-deployment-platform-implement-user-api-key-system"></a>
### Adaptive Deployment Implement user API key system
Provide per-user API keys with scopes, expiration, and rate limits to enable external client integration beyond the global Tier 0 key. This is the core enabler for third-party applications, scripts, and automation to access Shu APIs securely.

<a id="adaptive-deployment-platform-provide-api-key-management-endpoints"></a>
### Adaptive Deployment Provide API key management endpoints
Expose REST endpoints for users to create, list, revoke, and rotate their API keys, and for admins to manage keys across users.

<a id="adaptive-deployment-platform-document-sdk-and-apis"></a>
### Adaptive Deployment Document SDK and APIs
Maintain developer references that explain external client integration, authentication methods, available endpoints, error codes, streaming formats, and usage patterns for scripts and third-party applications.

<a id="phase-3-agentic-platform"></a>
## Phase 3 - Agentic Platform

Phase 3 is divided into three focused sub-initiatives to reduce coupling and enable parallel progress.

<a id="agentic-platform-add-event-bus-abstraction"></a>
### Agentic Platform Add event bus abstraction
Integrate an event bus (e.g., NATS, Kafka) interface so services can publish/subscribe to domain events without point-to-point coupling.

<a id="agentic-platform-stand-up-dedicated-worker-pools"></a>
### Agentic Platform Stand up dedicated worker pools
Deploy specialized worker groups (feeds, workflows, embeddings) to isolate workloads and scale them independently based on demand.

<a id="agentic-platform-export-observability-signals"></a>
### Agentic Platform Export observability signals
Emit metrics and traces via OpenTelemetry/Grafana integrations, enabling visibility into latency, error budgets, and throughput.

<a id="agentic-platform-define-experience-schema-and-registry"></a>
### Agentic Platform Define Experience schema and registry
Create a data model for Experiences (named compositions of prompts, plugins, agents, and workflows) with versioning, visibility controls, and execution history.

<a id="agentic-platform-build-admin-experience-management-ui"></a>
### Agentic Platform Build admin Experience management UI
Provide an admin-only screen to create, edit, publish, and retire Experiences with a step builder, template editor, and run history view.

<a id="agentic-platform-migrate-hardcoded-orchestrations-to-experience-abstraction"></a>
### Agentic Platform Migrate hard-coded orchestrations to Experience abstraction
Convert Morning Briefing and similar hard-coded orchestrators into saved Experiences, enabling configuration without code changes.

<a id="agentic-platform-introduce-capability-scoped-plugin-actions"></a>
### Agentic Platform Introduce capability-scoped plugin actions
Allow plugins to declare fine-grained actions with RBAC scopes, keeping least-privilege control while permitting rich interactions with external systems.

<a id="agentic-platform-provide-host-managed-http-client"></a>
### Agentic Platform Provide host-managed HTTP client
Centralize HTTP calls through a host client that enforces retries, backoffs, and rate limits, preventing plugins from overwhelming third-party APIs.

<a id="agentic-platform-implement-plugin-registry-lifecycle"></a>
### Agentic Platform Implement plugin registry lifecycle
Build CRUD, package signing, and version management workflows for plugins so operators can install, upgrade, and trust components safely.

<a id="agentic-platform-capture-plugin-artifact-lineage"></a>
### Agentic Platform Capture plugin artifact lineage
Record how each artifact was produced (inputs, version, plugin) to aid debugging, compliance, and repeatability.

<a id="agentic-platform-consume-external-mcp-servers"></a>
### Agentic Platform Consume external MCP servers
Expose external MCP (Model Context Protocol) servers as first-class Shu plugins via a new adapter type, starting with stdio transport for local tools and WebSocket for remote servers in later iterations.

<a id="agentic-platform-publish-plugin-sdk-cli"></a>
### Agentic Platform Publish Plugin SDK CLI
Provide a command-line toolkit (`shu-plugin`) for plugin developers with `init` (scaffolding), `validate` (manifest/schema validation), and `check` (static contract analysis) commands. Includes the Plugin Ingestion Interface contract and SDK utilities library for result envelopes, retry/backoff, and diagnostics.

<a id="agentic-platform-publish-plugin-sdk-test-harness"></a>
### Agentic Platform Publish Plugin SDK test harness
Build a contract test runner that validates plugins against golden outputs, latency thresholds, and cost tracking. Provides mock host capabilities for isolated testing and enables CI integration for plugin development workflows.

<a id="agentic-platform-implement-plugin-signing-and-allow-list"></a>
### Agentic Platform Implement plugin signing and allow-list
Add cryptographic signing for plugin packages and signature verification in the plugin loader. Implement admin-configurable allow-lists for trusted plugins and signing keys, with audit-only and enforce modes for gradual rollout.

<a id="agentic-platform-activate-profile-graph-entities"></a>
### Agentic Platform Activate profile graph entities
Populate people, team, project, and account nodes that accumulate signals over time, forming the backbone for contextual intelligence.

<a id="agentic-platform-expose-profile-readwrite-apis"></a>
### Agentic Platform Expose profile read/write APIs
Offer APIs for agents and plugins to read context and contribute new signals, turning Shu into a living knowledge graph.

<a id="agentic-platform-publish-declarative-workflow-dsl"></a>
### Agentic Platform Publish declarative workflow DSL
Define a typed language for composing workflows so engineers can build automations declaratively rather than writing bespoke code.

<a id="agentic-platform-deliver-workflow-step-library"></a>
### Agentic Platform Deliver workflow step library
Provide reusable steps for common actions (fetch context, call plugin, request approval) to accelerate automation assembly.

<a id="agentic-platform-ship-policy-and-approval-interface"></a>
### Agentic Platform Ship policy and approval interface
Create administrative UIs and APIs to view approvals, override decisions, and audit policy outcomes, keeping humans in control.

<a id="agentic-platform-add-provider-health-probes"></a>
### Agentic Platform Add provider health probes
Continuously test LLM provider endpoints for latency and availability, feeding routing decisions with real-time data.

<a id="agentic-platform-normalize-llm-parameters"></a>
### Agentic Platform Normalize LLM parameters
Translate Shu-specific configuration into provider-specific payloads so teams can swap models without rewriting workflows.

<a id="agentic-platform-implement-llm-fallback-logic"></a>
### Agentic Platform Implement LLM fallback logic
Define automatic fallback sequences when providers fail or exceed quotas, keeping workflows resilient.

<a id="agentic-platform-enable-local-model-pinning"></a>
### Agentic Platform Enable local model pinning
Allow sensitive workloads to force execution on self-hosted models (e.g., Ollama), satisfying data sovereignty demands.

<a id="agentic-platform-track-llm-usage-and-budgets"></a>
### Agentic Platform Track LLM usage and budgets
Collect cost and token metrics per tenant or workflow to enforce spend limits and support chargeback models.

<a id="phase-4-intelligence-expansion"></a>
## Phase 4 - Intelligence Expansion

<a id="intelligence-expansion-scale-ingestion-with-stream-processing"></a>
### Intelligence Expansion Scale ingestion with stream processing
Adopt streaming ingestion frameworks or batch microservices to handle high-volume connectors without bottlenecking core services.

<a id="intelligence-expansion-apply-tenant-aware-rate-limiting"></a>
### Intelligence Expansion Apply tenant-aware rate limiting
Throttle ingestion and workflow triggers per tenant to guarantee fairness and prevent noisy neighbors from impacting others.

<a id="intelligence-expansion-add-trace-sampling-controls"></a>
### Intelligence Expansion Add trace sampling controls
Control the volume of traces emitted during peak load, balancing observability fidelity with cost and overhead.

<a id="intelligence-expansion-release-crm-connectors"></a>
### Intelligence Expansion Release CRM connectors
Integrate CRM systems (Salesforce, HubSpot) providing revenue and relationship context crucial for strategic briefings.

<a id="intelligence-expansion-release-project-tracking-connectors"></a>
### Intelligence Expansion Release project tracking connectors
Connect to Jira, Linear, or Asana to capture delivery status, blockers, and ownership for engineering and product teams.

<a id="intelligence-expansion-release-incident-response-connectors"></a>
### Intelligence Expansion Release incident response connectors
Ingest PagerDuty, Opsgenie, or similar signals to keep operations intelligence current.

<a id="intelligence-expansion-release-finance-connectors"></a>
### Intelligence Expansion Release finance connectors
Integrate finance tools (NetSuite, QuickBooks, Stripe) to surface budget, spend, and forecast signals for leadership.

<a id="intelligence-expansion-instrument-plugin-execution-telemetry"></a>
### Intelligence Expansion Instrument plugin execution telemetry
Log execution duration, success/failure, and payload sizes to analyze plugin reliability and performance.

<a id="intelligence-expansion-monitor-slas-for-plugin-workloads"></a>
### Intelligence Expansion Monitor SLAs for plugin workloads
Define service level objectives (latency, success rate) and alert when plugins fall out of compliance, prompting triage.

<a id="intelligence-expansion-create-feature-stores"></a>
### Intelligence Expansion Create feature stores
Persist derived metrics (risk scores, momentum, sentiment) with time-series history to power proactive analytics.

<a id="intelligence-expansion-generate-storyline-synthesis-pipelines"></a>
### Intelligence Expansion Generate storyline synthesis pipelines
Build batch jobs that convert raw features into narrative summaries for briefings or project pulse updates.

<a id="intelligence-expansion-surface-contextual-intelligence-apis"></a>
### Intelligence Expansion Surface contextual intelligence APIs
Offer APIs that deliver curated insights (“top risks today”) for embedding into UIs and workflows.

<a id="intelligence-expansion-support-event-triggered-workflows"></a>
### Intelligence Expansion Support event-triggered workflows
Allow workflows to start in response to events (e.g., new risk detected) instead of manual or scheduled triggers.

<a id="intelligence-expansion-add-compensation-handlers"></a>
### Intelligence Expansion Add compensation handlers
Give workflows the ability to undo earlier steps when later steps fail, preventing partial updates.

<a id="intelligence-expansion-enforce-observability-budgets"></a>
### Intelligence Expansion Enforce observability budgets
Cap telemetry emitted per workflow or tenant to keep observability costs predictable.

<a id="intelligence-expansion-introduce-workflow-circuit-breakers"></a>
### Intelligence Expansion Introduce workflow circuit breakers
Automatically halt workflows when error rates spike, signaling operators before widespread impact.

<a id="intelligence-expansion-build-llm-regression-suites"></a>
### Intelligence Expansion Build LLM regression suites
Maintain prompt/response baselines to detect accuracy regressions when models, prompts, or contexts change.

<a id="intelligence-expansion-detect-llm-determinism-drift"></a>
### Intelligence Expansion Detect LLM determinism drift
Monitor output variance for deterministic tasks, flagging when models behave unpredictably.

<a id="intelligence-expansion-deploy-redaction-filters"></a>
### Intelligence Expansion Deploy redaction filters
Strip sensitive data (PII, PHI) from prompts and outputs according to policy, supporting compliance audits.

<a id="intelligence-expansion-provide-fine-tuning-hooks"></a>
### Intelligence Expansion Provide fine-tuning hooks
Allow organizations to fine-tune on-prem models with sanctioned datasets to improve domain specificity.

<a id="intelligence-expansion-launch-feed-health-dashboards"></a>
### Intelligence Expansion Launch feed health dashboards
Expose dashboards tracking feed status, lag, and error trends so operators can intervene quickly.

<a id="intelligence-expansion-detect-data-and-policy-drift"></a>
### Intelligence Expansion Detect data and policy drift
Alert when connector schemas or policy rules change, prompting revalidation of workflows and plugins.

<a id="intelligence-expansion-run-chaos-drills-for-worker-pools"></a>
### Intelligence Expansion Run chaos drills for worker pools
Regularly simulate worker failures to ensure failover logic and operational playbooks remain effective.

<a id="phase-5-operating-system-scale"></a>
## Phase 5 - Operating-System Scale

<a id="operating-system-scale-support-multi-region-tenancy"></a>
### Operating-System Scale Support multi-region tenancy
Partition tenants across regions with locality controls, meeting latency and regulatory requirements.

<a id="operating-system-scale-implement-storage-sharding-strategies"></a>
### Operating-System Scale Implement storage sharding strategies
Shard databases and queues to distribute load and remove single points of scaling failure.

<a id="operating-system-scale-automate-secrets-rotation"></a>
### Operating-System Scale Automate secrets rotation
Rotate credentials and encryption keys programmatically, reducing operational toil and exposure windows.

<a id="operating-system-scale-offer-pluggable-storage-backends"></a>
### Operating-System Scale Offer pluggable storage backends
Support alternative databases or object stores (e.g., Aurora, Spanner, S3-compatible) to meet enterprise preferences.

<a id="operating-system-scale-launch-plugin-marketplace-surfaces"></a>
### Operating-System Scale Launch plugin marketplace surfaces
Build UI/API experiences for discovering, publishing, and managing plugins inside Shu.

<a id="operating-system-scale-deliver-plugin-recommendation-engine"></a>
### Operating-System Scale Deliver plugin recommendation engine
Recommend plugins based on tenant usage patterns and goals, accelerating adoption of relevant capabilities.

<a id="operating-system-scale-model-plugin-dependency-graph"></a>
### Operating-System Scale Model plugin dependency graph
Track inter-plugin dependencies to warn about incompatible upgrades or missing prerequisites.

<a id="operating-system-scale-run-auto-upgrade-channels"></a>
### Operating-System Scale Run auto-upgrade channels
Allow tenants to opt into auto-updating plugins with staged rollouts and rollback safety nets.

<a id="operating-system-scale-filter-discovery-with-policy-awareness"></a>
### Operating-System Scale Filter discovery with policy awareness
Respect compliance policies when showing available plugins, hiding those that violate tenant rules.

<a id="operating-system-scale-expose-organizational-memory-apis"></a>
### Operating-System Scale Expose organizational memory APIs
Provide APIs that aggregate knowledge across teams, respecting privacy boundaries, so experiences can query company-wide insights.

<a id="operating-system-scale-provide-cross-source-analytics-surfaces"></a>
### Operating-System Scale Provide cross-source analytics surfaces
Deliver dashboards visualizing trends across emails, projects, incidents, and finances for leadership.

<a id="operating-system-scale-emit-anomaly-alerts"></a>
### Operating-System Scale Emit anomaly alerts
Automatically detect unusual patterns (spike in incidents, drop in commits) and notify stakeholders.

<a id="operating-system-scale-automate-workflow-contract-testing"></a>
### Operating-System Scale Automate workflow contract testing
Run contract tests for workflows when schemas or plugins change, catching regressions before deployment.

<a id="operating-system-scale-manage-collaboration-states"></a>
### Operating-System Scale Manage collaboration states
Model shared state for multi-user workflows (e.g., co-owned tasks) so Shu can coordinate teams, not just individuals.

<a id="operating-system-scale-maintain-commitments-ledger"></a>
### Operating-System Scale Maintain commitments ledger
Track promises, SLAs, and follow-ups with evidence, enabling accountability and automatic reminders.

<a id="operating-system-scale-balance-llm-latency-cost-and-accuracy"></a>
### Operating-System Scale Balance LLM latency, cost, and accuracy
Implement routing heuristics that select models based on workload needs, optimizing for budget and response time.

<a id="operating-system-scale-enable-parallel-tool-planning"></a>
### Operating-System Scale Enable parallel tool planning
Allow the orchestrator to plan multiple tool invocations concurrently when dependencies allow, improving throughput.

<a id="operating-system-scale-ship-guardrail-templates"></a>
### Operating-System Scale Ship guardrail templates
Provide reusable policy templates (privacy, compliance, tone) so tenants can enforce standards quickly.

<a id="operating-system-scale-publish-multi-cluster-reference-architectures"></a>
### Operating-System Scale Publish multi-cluster reference architectures
Document how to deploy Shu across multiple clusters, including networking, observability, and DR considerations.

<a id="operating-system-scale-provide-sre-runbooks"></a>
### Operating-System Scale Provide SRE runbooks
Create runbooks covering incident response, scaling events, and maintenance tasks to standardize operations.

<a id="operating-system-scale-author-upgrade-playbooks"></a>
### Operating-System Scale Author upgrade playbooks
Outline step-by-step upgrade procedures for Shu core, plugins, and workflows, minimizing downtime.

<a id="operating-system-scale-compile-compliance-reporting-packs"></a>
### Operating-System Scale Compile compliance reporting packs
Generate evidence bundles (audit logs, access reports) to support certifications like SOC2 or ISO.

<a id="phase-6-ecosystem-governance"></a>
## Phase 6 - Ecosystem & Governance

<a id="ecosystem-governance-guarantee-api-stability-windows"></a>
### Ecosystem-Governance Guarantee API stability windows
Define support windows and deprecation policies so partners know how long APIs will remain stable.

<a id="ecosystem-governance-publish-extension-hooks"></a>
### Ecosystem-Governance Publish extension hooks
Expose extension points (webhooks, GraphQL, SDK callbacks) that third parties can rely on without modifying core code.

<a id="ecosystem-governance-introduce-plugin-revenue-sharing"></a>
### Ecosystem-Governance Introduce plugin revenue sharing
Enable monetization options for plugin authors, encouraging ongoing investment in the ecosystem.

<a id="ecosystem-governance-issue-certification-badges"></a>
### Ecosystem-Governance Issue certification badges
Provide verification programs that attest to security and quality standards, building tenant trust.

<a id="ecosystem-governance-support-plugin-attestation-flows"></a>
### Ecosystem-Governance Support plugin attestation flows
Allow enterprises to review attestations (security scans, compliance checks) before installing plugins.

<a id="ecosystem-governance-offer-telemetry-opt-inout-controls"></a>
### Ecosystem-Governance Offer telemetry opt-in/out controls
Let tenants choose what telemetry leaves their environment, balancing diagnostics with privacy.

<a id="ecosystem-governance-expose-partner-observability-apis"></a>
### Ecosystem-Governance Expose partner observability APIs
Share health and usage metrics with partner systems so Shu can integrate into broader monitoring strategies.

<a id="ecosystem-governance-document-federated-ingestion-patterns"></a>
### Ecosystem-Governance Document federated ingestion patterns
Describe how to ingest data from subsidiaries or divisions with separate governance, keeping boundaries intact.

<a id="ecosystem-governance-enforce-data-residency-controls"></a>
### Ecosystem-Governance Enforce data residency controls
Guarantee data remains within specified geographic or regulatory zones, unlocking regulated industries.

<a id="ecosystem-governance-integrate-policy-dsl-with-grc-systems"></a>
### Ecosystem-Governance Integrate policy DSL with GRC systems
Sync Shu policy definitions with enterprise governance tools, enabling centralized oversight.

<a id="ecosystem-governance-provide-auditor-apis"></a>
### Ecosystem-Governance Provide auditor APIs
Give auditors read-only programmatic access to logs, configs, and evidence, reducing manual reporting burdens.

<a id="ecosystem-governance-automate-rollback-verification"></a>
### Ecosystem-Governance Automate rollback verification
Automatically test and document rollback paths after changes, proving recoverability to compliance teams.

<a id="ecosystem-governance-support-specialty-llm-integrations"></a>
### Ecosystem-Governance Support specialty LLM integrations
Integrate niche industry models (legal, medical, financial) to deliver domain-specific intelligence.

<a id="ecosystem-governance-tune-hardware-acceleration-profiles"></a>
### Ecosystem-Governance Tune hardware acceleration profiles
Optimize Shu for GPU/TPU or specialized accelerators, reducing cost-per-inference for heavy workloads.

<a id="ecosystem-governance-deliver-differential-privacy-tooling"></a>
### Ecosystem-Governance Deliver differential privacy tooling
Provide mechanisms to add noise or aggregate data so insights can be shared without exposing individual records.

<a id="ecosystem-governance-create-enterprise-installers"></a>
### Ecosystem-Governance Create enterprise installers
Build guided installers for air-gapped or highly regulated environments, reducing time-to-value.

<a id="ecosystem-governance-build-managed-service-marketplace-pipelines"></a>
### Ecosystem-Governance Build managed service marketplace pipelines
Set up distribution pipelines for hosting Shu as a managed service or via marketplaces, expanding adoption channels.

<a id="cross-cutting-initiatives"></a>
## Cross-Cutting Initiatives

<a id="cross-cutting-run-continuous-threat-modeling"></a>
### Cross-Cutting Run continuous threat modeling
Regularly evaluate attack surfaces as new systems are added, informing secure design decisions.

<a id="cross-cutting-conduct-penetration-testing-cycles"></a>
### Cross-Cutting Conduct penetration testing cycles
Schedule recurring third-party tests to validate defenses and catch regressions.

<a id="cross-cutting-enforce-secrets-hygiene"></a>
### Cross-Cutting Enforce secrets hygiene
Mandate rotation, scanning, and least-privilege handling of secrets across development and production.

<a id="cross-cutting-align-with-regulatory-frameworks"></a>
### Cross-Cutting Align with regulatory frameworks
Track compliance requirements (SOC2, ISO, HIPAA) and bake them into engineering processes.

<a id="cross-cutting-maintain-contract-test-suites"></a>
### Cross-Cutting Maintain contract test suites
Keep automated tests that verify contracts between services, preventing breaking changes from shipping.

<a id="cross-cutting-expand-integration-testing"></a>
### Cross-Cutting Expand integration testing
Grow end-to-end test coverage for critical flows so complex regressions surface before release.

<a id="cross-cutting-generate-synthetic-datasets"></a>
### Cross-Cutting Generate synthetic datasets
Produce sanitized datasets for testing and demos without leaking sensitive information.

<a id="cross-cutting-automate-load-and-chaos-testing"></a>
### Cross-Cutting Automate load and chaos testing
Run regular stress and failure-scenario tests to ensure elasticity and resilience claims hold under pressure.

<a id="cross-cutting-publish-system-diagrams"></a>
### Cross-Cutting Publish system diagrams
Document architecture, data flow, and dependency diagrams to align teams and aid onboarding.

<a id="cross-cutting-provide-migration-guides"></a>
### Cross-Cutting Provide migration guides
Guide users through breaking changes or major upgrades, reducing operational friction.

<a id="cross-cutting-set-contribution-standards"></a>
### Cross-Cutting Set contribution standards
Define code review, testing, and documentation expectations so community contributions stay high quality.

<a id="cross-cutting-monitor-risk-telemetry"></a>
### Cross-Cutting Monitor risk telemetry
Instrument dashboards tracking key risk indicators (policy violations, quota breaches) for early warning.

<a id="cross-cutting-stage-incremental-rollouts"></a>
### Cross-Cutting Stage incremental rollouts
Adopt canary releases and feature flags to reduce blast radius when shipping new capabilities.
