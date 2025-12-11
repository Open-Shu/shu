# What is Shu?

## Preface: Vision and End-State

This document describes the target, end-state vision for Shu rather than the current implementation.
It is intentionally forward-looking and uses future-tense language for capabilities that are not yet fully built.
For the current implementation status and known gaps, see:
- docs/SHU_TECHNICAL_ROADMAP.md

## Executive Summary

Shu is an open-source, living operating system for work: proactive organizational intelligence that maps your teams, initiatives, and customers, stitches signals from every tool you connect, and executes through self-hosted (including Ollama) or cloud LLMs while keeping data ownership and auditability firmly in your hands.

Plugins are the heartbeat of Shu. With a standard contract and workflow engine, teams will be able to wire in CRM, source control, finance, compliance, HRIS, or bespoke internal platforms and watch Shu build continuously refreshed profiles across people, projects, and accounts. Those profiles will fuel agents that deliver decision-ready context, nudges, and automations right where work happens—Slack, email, dashboards, or custom surfaces. A cross-functional coordination experience is just one expression; the same architecture will also power sales deal desks, engineering program control towers, or compliance command centers by swapping in the relevant connectors and playbooks.

Shu will treat governance as code. Every action will route through typed policies, role-based access, and reversible approvals, and teams will choose the intelligence substrate that fits their sovereignty model: keep sensitive workloads on Ollama or other on-prem models, burst to Anthropic or Azure OpenAI when policy allows, and log every decision through an auditable memory system. Because it will be open source, teams will be able to inspect the pipeline, extend the SDK, and deploy Shu in environments ranging from a single founder’s lab to a regulated enterprise cluster.

As the community contributes more plugins, Shu will compound into a company-wide intelligence fabric. Project Pulse will blend Jira, GitHub, and Slack to preempt blockers. Delegation Helper will capture cross-functional threads and route tasks with SLA monitoring. Focus Sentinel will balance calendars against incident queues and revenue milestones. Compliance Guardrails will spot risky moves and propose policy-safe alternatives. Shu will not just answer questions—it will anticipate them, align execution with strategy, and give every team a programmable intelligence layer they can trust, customize, and own.

Signature experiences (e.g., Inbox Triage, Meeting Co-Pilot, Project Pulse, Morning Briefing) are exemplar first-party showcases built from the same modular components—plugins, KBs, agents, prompts, workflows, and the Experience Creator paths—rather than separate products.

## Technical Summary

### Runtime Orchestration

Shu's backend is a Python 3 service mesh fronted by FastAPI, with asynchronous SQLAlchemy over PostgreSQL (pgvector-enabled) and Redis for queues, caching, and feed coordination. Alembic will drive schema evolution (squashed release migrations), while structured logging (text or JSON) and a unified audit bus will instrument every action. The orchestrator layer will hydrate context from the profile store, route tool calls to plugin runtimes, manage LLM invocations, and persist workflow state so services remain loosely coupled yet compositionally aligned.

### Plugin & Ingestion Plane

Plugins declare manifests that advertise capabilities, will implement RBAC scopes, and host resources (identity broker, secrets vault, HTTP client, object storage). The host will spin up deterministic runtimes, inject tenancy-scoped credentials, and enforce JSON-schema outputs with redaction. A Redis-backed scheduler handles bulk ingest and watermark deltas, pushing artifacts through OCR (EasyOCR with Tesseract fallback), embedding (sentence-transformers all-MiniLM-L6-v2, thread/process execution), dedupe, and classification before landing them in the knowledge store. Future connectors—CRM, DevOps, finance, compliance—will reuse the same pipeline, providing consistent policy enforcement and observability.

### Knowledge, Profiles, and Memory

The knowledge service will maintain a graph of people, teams, projects, and accounts built from ingested artifacts, tracking provenance, decay, and confidence scores for each signal. Conversation memory will be scoped per user or tenant, while organizational memory will aggregate policy-permitted facts for cross-team workflows. Planned feature stores will support risk/opportunity detection, storyline synthesis, and anomaly alerts so higher-level automations can reason about cadence, commitments, and blockers.

### Workflow, Agents, and Policy

Agents will be declarative bundles of prompts, tool bindings, workflow templates, and guardrails executed via a resumable state machine. Workflows will call plugins, LLM tools, or system transforms; conditions will express branching; policy annotations will require approvals or set confidentiality levels. Policy controllers will evaluate each step against RBAC, data classification, rate budgets, and cost ceilings, with escalation paths or auto-generated alternatives on violation. The roadmap extends to event-triggered workflows, compensation handlers, and observability budgets so automations are monitored like core services.

### LLM Execution Fabric

A provider abstraction will normalize parameters, streaming behavior, token accounting, and failure semantics across cloud models (OpenAI, Anthropic, Azure) and self-hosted runtimes such as Ollama. Sensitive workloads will be able to pin to on-prem GPUs or CPU-backed models, while less sensitive jobs will burst to external endpoints. JSON-schema enforcement will keep outputs deterministic, enabling safe tool chaining. Routing logic will factor latency distributions, provider health probes, usage quotas, and redaction filters to maintain compliance and reliability.

### Operational Surfaces

The React 18 admin console (Material UI + React Query + Axios) will expose plugin lifecycle management, feed telemetry, workflow inspection, connected-account scopes, and health dashboards. REST endpoints and CLI tooling will provide automation hooks. Observability will integrate structured logs, metrics, and traces ready for Prometheus/Grafana or third-party APM, while negative-test harnesses and sandbox tenants will vet new connectors and workflows before production rollout.

### Deployment Modalities

Single-machine installs will run via Docker Compose or bare processes: FastAPI API, Redis, Postgres+pgvector, optional Ollama instance, scheduler/workflow workers, and the React build served statically or via the API gateway. This mode will give small teams sovereign control without distributed infrastructure. Enterprise deployments will containerize services in Kubernetes/Nomad with ingress controllers, managed Postgres/Redis, and auto-scaled worker pools per workload type; hybrid patterns will keep sensitive plugin executions and LLM inference on private nodes while bursting stateless tasks to cloud pools. Configuration will use environment cascades with optional secrets managers to suit both modes.

### Open Ecosystem

Shu is open source with typed contracts for plugins, workflows, profiles, and models. Engineers will be able to extend the plugin SDK, replace storage engines, integrate alternative identity providers, or add new observability pipelines. Contribution standards will emphasize deterministic interfaces, reproducible tests, and policy compliance so community modules can drop into deployments without breaking the architectural guarantees.
