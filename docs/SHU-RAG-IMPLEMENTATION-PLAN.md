# Shu RAG Implementation Plan

**Status**: Active
**Created**: 2025-12-02
**Updated**: 2025-12-16
**Source**: [SHU-RAG-WHITEPAPER.md](./whitepapers/SHU-RAG-WHITEPAPER.md)
**Epic**: [SHU-339 - Shu RAG Intelligent Retrieval](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/EPIC.md)

---

## Executive Summary

This document provides a phased implementation plan for the Shu RAG architecture. The plan sequences work across three existing epics (SHU-2, SHU-15, SHU-18) and introduces a dedicated epic (SHU-339 Shu RAG Intelligent Retrieval) to coordinate the novel components.

**Key Principle**: Traditional RAG remains the default. Shu RAG features are additive and configurable. Each phase delivers independently testable, releasable functionality.

**Task Files**: All Shu RAG tasks are documented under [tasks/SHU-339-SHU-RAG-Intelligent-Retrieval](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/) and are Jira-synced as SHU-341–SHU-360.

---

## 1. Epic and Task Reconciliation

### 1.1 Existing Epic Mapping

| Whitepaper Component | Primary Epic | Key Tasks | Gap Analysis |
|---------------------|--------------|-----------|--------------|
| Document Profiling (Section 2) | SHU-2 | SHU-160 (Knowledge Graph) | Partial - needs synopsis, capability manifest, question synthesis |
| Relational Context (Section 3) | SHU-15 | SHU-100 (Extractors), SHU-103 (Schema) | Partial - needs document-side extraction, not just user-side |
| User Profile Integration (Section 3.3) | SHU-15 | SHU-108 (Serving APIs) | Good fit - extend for retrieval scoring |
| Multi-Surface Retrieval (Section 4) | SHU-2 | SHU-155 (Non-RAG Retrieval) | Partial - needs question/manifest surfaces |
| Agentic Orchestration (Section 5) | SHU-18 | SHU-132 (RAG-Plugin), SHU-134 (Orchestration), SHU-140 (Reasoning) | Good fit - extend with retrieval tools |
| Context Assembly (Section 5) | SHU-18 | SHU-138 (Context Assembly) | Good fit - integrate profile + retrieval |

### 1.2 Tasks to Extend (Not Duplicate)

| Existing Task | Extension Needed |
|---------------|------------------|
| SHU-100 (Extractors) | Add document-side participant extraction (not just user behavior) |
| SHU-103 (Feature Schema) | Add ParticipantLink, ProjectLink schemas for documents |
| SHU-108 (Serving APIs) | Add retrieval scoring endpoint for relational boost |
| SHU-132 (RAG-Plugin) | Expose multi-surface retrieval as agent tools |
| SHU-155 (Non-RAG Retrieval) | Add question/manifest query surfaces |
| SHU-160 (Knowledge Graph) | Integrate entity extraction with document profiling |

### 1.3 New Tasks Required

| New Area | Epic | Jira Tasks | Description |
|----------|------|------------|-------------|
| Document Profiling Pipeline | SHU-339 | SHU-342, SHU-343, SHU-344, SHU-359 | LLM-based synopsis, capability manifest, document type at ingestion and profile persistence |
| Question Synthesis | SHU-339 | SHU-353, SHU-351, SHU-352 | Generate, embed, and retrieve via hypothetical document questions |
| Schema and Manifest Storage | SHU-339 | SHU-342, SHU-355 | DB schema for document profiles, capability manifests, and relational context |
| Multi-Surface Retrieval | SHU-339 | SHU-350, SHU-348, SHU-358, SHU-347 | Query classification, surface routing, score fusion, and manifest-based filtering |
| Relational and Temporal Scoring | SHU-339 | SHU-341, SHU-349, SHU-354, SHU-360 | Participant/project extraction plus relational and temporal boosts for retrieval |
| Retrieval Tool Definitions | SHU-339 | SHU-357 | Agent tools for each retrieval surface |
| Invocation Policy & Feedback | SHU-339 | SHU-345, SHU-346, SHU-356 | When to use static pipeline vs. agentic mode, plus retrieval feedback loop |

---

## 2. Phased Implementation

Canonical Shu RAG phase definitions, goals, and milestones now live in the
[SHU-339 EPIC](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/EPIC.md).

This section keeps a light-weight summary for roadmap alignment and
cross-epic planning. For task-level details, always refer to the epic and the
individual task files under `tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/`.

### Phase 0: Schema Foundation

- Establish database schemas for document profiles, chunk profiles, synthesized queries, and
  relational context without changing ingestion behavior.
- Primary tasks: SHU-342 Document Profile Schema (includes chunk schema), SHU-355 Relational Context
  Schema.

### Phase 1: Document and Chunk Profiling

- Generate document profiles (synopsis, document type, capability manifest) and chunk profiles
  (summary, keywords, topics) at ingestion time while keeping traditional RAG behavior unchanged.
- Chunk profiling enables fast keyword/topic filtering before vector search and provides an
  LLM-scannable index for agentic retrieval. Chunk profiles are **always computed** for every
  document regardless of size.
- Document-level profiling uses a **hybrid strategy** based on document size:
  - **Small documents** (`doc_tokens <= PROFILING_FULL_DOC_MAX_TOKENS`): Profile the full
    document directly in a single LLM call for higher quality.
  - **Large documents**: Use chunk-first aggregation to derive document-level fields from
    chunk profiles.
- No single LLM call should exceed `PROFILING_MAX_INPUT_TOKENS`.
- Primary tasks: SHU-343 Document and Chunk Profiling Service, SHU-344 Ingestion
  Pipeline Integration, SHU-359 Synopsis Embedding.

### Phase 2: Query Synthesis

- Generate hypothetical queries per document (questions, imperatives, declarative searches),
  embed them, and enable query-match retrieval as an additional surface.
- Primary tasks: SHU-353 Query Synthesis Service, SHU-351 Query
  Embedding and Storage, SHU-352 Query-Match Retrieval Surface.

### Phase 3: Multi-Surface Retrieval

- Implement query classification, multi-surface routing, manifest-based
  filtering, keyword pre-filtering, and score fusion across queries, synopses, manifests, and
  chunks.
- Primary tasks: SHU-350 Query Classification Service, SHU-348 Multi-Surface
  Query Router, SHU-358 Score Fusion Service, SHU-347 Manifest-Based
  Filtering.

### Phase 4: Relational Context

- Extract participants and project associations from documents and integrate
  them with SHU-15 profile data to apply relational and temporal boosts.
- Primary tasks: SHU-341 Document Participant Extraction, SHU-349 Project
  Association Extraction, SHU-354 Relational Boost Scoring, SHU-360 Temporal
  Relevance Scoring.

### Phase 5: Agentic Orchestration

- Expose Shu RAG retrieval surfaces as agent tools, decide between traditional
  RAG, static Shu RAG, and agentic modes, and implement iterative refinement
  and feedback loops under configurable budgets.
- Primary tasks: SHU-357 Retrieval Tool Definitions, SHU-345 Invocation Policy
  Service, SHU-346 Iterative Refinement Logic, SHU-356 Retrieval Feedback
  Loop.

---

## 3. Dependency Graph

```
Phase 0: Schema Foundation
    |
    v
Phase 1: Document Profiling
    |
    v
Phase 2: Question Synthesis
    |
    v
Phase 3: Multi-Surface Retrieval
    |
    +---> Phase 4: Relational Context (requires SHU-15)
    |         |
    v         v
Phase 5: Agentic Orchestration (requires SHU-18)
```

**Critical Path**: Phases 0-3 can proceed independently of SHU-15/SHU-18.
**Parallel Work**: SHU-15 and SHU-18 can progress in parallel with Phases 0-3.

---

## 4. Integration Points

### 4.1 Ingestion Pipeline Integration

**Current State** (ingestion_service.py):
- Documents ingested via ingest_document, ingest_email, ingest_text, ingest_thread
- Text extraction -> Document record -> Chunk generation -> Embedding

**Shu RAG Extension**:
- After chunk generation, trigger async document profiling
- Profiling generates: synopsis, capability manifest, synthesized questions
- Store profile artifacts in new tables
- Document immediately searchable via chunks; profile surfaces available after profiling completes
 - A small DB-aware profiling orchestrator (SHU-343) owns loading documents/chunks, managing
   `profiling_status`, and delegating to the ProfilingService; ingestion (SHU-344) only enqueues
   work to this orchestrator.

**Configuration**:
```python
# config.py additions
ENABLE_DOCUMENT_PROFILING: bool = False  # Feature flag
PROFILING_LLM_MODEL: str = "gpt-4o-mini"  # Model for profiling
PROFILING_FULL_DOC_MAX_TOKENS: int = 4000  # Routing threshold: documents at or below
                                            # this size use full-doc profiling; larger
                                            # docs use chunk-first aggregation
PROFILING_MAX_INPUT_TOKENS: int = 8000  # Hard ceiling on any single profiling LLM call
                                        # (full-doc or aggregate). Never truncate to
                                        # satisfy; route to chunk-agg or partition.
PROFILING_QUESTION_COUNT: int = 10  # Questions per document (Phase 2: Query Synthesis)
PROFILING_ASYNC: bool = True  # Background processing
```

### 4.2 Plugin System Integration

**Current State**:
- Plugins use host.kb capability for ingestion
- RAG retrieval exposed via existing endpoints

**Shu RAG Extension**:
- Extend host.kb with profile-aware retrieval methods
- New capability: host.kb.query_with_profile(query, user_id)
- Returns results with multi-surface scores and provenance

### 4.3 User Profile Integration (SHU-15)

**Required from SHU-15**:
- SHU-103: Feature schema for user profiles
- SHU-100: Extractors for relationships, topics, commitments
- SHU-108: Serving APIs for profile data

**Shu RAG Consumption**:
- Query SHU-108 APIs for user's affinity scores, active projects
- Apply relational boost based on profile data
- Profile data cached per-request for performance

### 4.4 Agent Foundation Integration (SHU-18)

**Required from SHU-18**:
- SHU-132: RAG-Plugin Integration (KB as agent tool)
- SHU-134: Agent Orchestration Service
- SHU-140: Agent Reasoning Framework

**Shu RAG Extension**:
- Register retrieval tools with agent orchestration
- Implement invocation policy for static vs. agentic mode
- Extend reasoning framework with retrieval-specific logic

---

## 5. Architecture Constraints

### 5.1 Traditional RAG Preserved

- All existing RAG functionality remains unchanged
- Shu RAG features are additive, not replacements
- Feature flags control activation per KB or globally
- Fallback to chunk-only retrieval if profiling disabled

### 5.2 Incremental Adoption

- Each phase delivers standalone value
- Phase 1 (profiling) useful without Phase 5 (agentic)
- Organizations can adopt phases based on needs and resources

### 5.3 Performance Bounds

- Ingestion: Profiling runs async; no blocking
- Query: Target <100ms additional latency over standard RAG
- Storage: ~25KB overhead per document + ~1KB per chunk + ~1.5KB per synthesized query
- For 100K documents with 50 chunks and 10 queries each: ~8GB additional storage

### 5.4 Configuration-Driven

- All thresholds, weights, and behaviors configurable
- Per-KB configuration overrides global defaults
- No hardcoded values per DEVELOPMENT_STANDARDS.md

---

## 6. Recommendations

### 6.1 Epic Structure

**Option A: Single New Epic (SHU-RAG)**
- Create SHU-RAG epic for all new tasks
- Extend existing SHU-2, SHU-15, SHU-18 tasks as needed
- Clear ownership of novel components

**Option B: Distribute Across Existing Epics**
- Add profiling tasks to SHU-2
- Add relational tasks to SHU-15
- Add agentic tasks to SHU-18
- Risk: coordination complexity

**Recommendation**: Option A - Create SHU-RAG epic with explicit dependencies on SHU-2, SHU-15, SHU-18.

### 6.2 Sequencing Priority

1. **Immediate**: Phase 0 (Schema) - unblocks all subsequent work
2. **High**: Phase 1-2 (Profiling, Questions) - core innovation, minimal dependencies
3. **Medium**: Phase 3 (Multi-Surface) - requires Phase 2
4. **Medium**: Phase 4 (Relational) - requires SHU-15 progress
5. **Lower**: Phase 5 (Agentic) - requires SHU-18 progress

### 6.3 Roadmap Alignment

Update SHU_TECHNICAL_ROADMAP.md to reflect:
- Shu RAG as foundational to Phase 3B (Experience System) and Phase 4 (Intelligence)
- SHU-2, SHU-15, SHU-18 sequenced as Shu RAG dependencies
- Phase 0-2 can begin immediately; Phase 4-5 await epic progress

---

## 7. Task Summary

All tasks are documented under [tasks/SHU-339-SHU-RAG-Intelligent-Retrieval](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/).

| # | Jira | Name | Phase | Effort | Task File |
|---|------|------|-------|--------|-----------|
| 1 | SHU-342 | Document Profile Schema (incl. chunk) | 0 | 2-3 days | [SHU-342-Document-Profile-Schema.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-342-Document-Profile-Schema.md) |
| 2 | SHU-355 | Relational Context Schema | 0 | 2-3 days | [SHU-355-Relational-Context-Schema.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-355-Relational-Context-Schema.md) |
| 3 | SHU-343 | Document and Chunk Profiling Service | 1 | 4-5 days | [SHU-343-Document-Profiling-Service.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-343-Document-Profiling-Service.md) |
| 4 | SHU-344 | Ingestion Pipeline Integration | 1 | 2-3 days | [SHU-344-Ingestion-Pipeline-Integration.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-344-Ingestion-Pipeline-Integration.md) |
| 5 | SHU-359 | Synopsis Embedding | 1 | 1-2 days | [SHU-359-Synopsis-Embedding.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-359-Synopsis-Embedding.md) |
| 6 | SHU-361 | Admin UI and Configuration for Profiling | 1 | 3-4 days | [SHU-361-Admin-Configuration-for-Shu-RAG-Ingestion-Time-Intelligence.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-361-Admin-Configuration-for-Shu-RAG-Ingestion-Time-Intelligence.md) |
| 7 | SHU-353 | Query Synthesis Service | 2 | 3-4 days | [SHU-353-Question-Synthesis-Service.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-353-Question-Synthesis-Service.md) |
| 8 | SHU-351 | Query Embedding and Storage | 2 | 2-3 days | [SHU-351-Question-Embedding-and-Storage.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-351-Question-Embedding-and-Storage.md) |
| 9 | SHU-352 | Query-Match Retrieval Surface | 2 | 2-3 days | [SHU-352-Question-Match-Retrieval-Surface.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-352-Question-Match-Retrieval-Surface.md) |
| 10 | SHU-350 | Query Classification Service | 3 | 2-3 days | [SHU-350-Query-Classification-Service.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-350-Query-Classification-Service.md) |
| 11 | SHU-348 | Multi-Surface Query Router | 3 | 2-3 days | [SHU-348-Multi-Surface-Query-Router.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-348-Multi-Surface-Query-Router.md) |
| 12 | SHU-358 | Score Fusion Service | 3 | 2-3 days | [SHU-358-Score-Fusion-Service.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-358-Score-Fusion-Service.md) |
| 13 | SHU-347 | Manifest-Based Filtering | 3 | 2-3 days | [SHU-347-Manifest-Based-Filtering.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-347-Manifest-Based-Filtering.md) |
| 14 | SHU-341 | Document Participant Extraction | 4 | 3-4 days | [SHU-341-Document-Participant-Extraction.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-341-Document-Participant-Extraction.md) |
| 15 | SHU-349 | Project Association Extraction | 4 | 2-3 days | [SHU-349-Project-Association-Extraction.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-349-Project-Association-Extraction.md) |
| 16 | SHU-354 | Relational Boost Scoring | 4 | 3-4 days | [SHU-354-Relational-Boost-Scoring.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-354-Relational-Boost-Scoring.md) |
| 17 | SHU-360 | Temporal Relevance Scoring | 4 | 2-3 days | [SHU-360-Temporal-Relevance-Scoring.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-360-Temporal-Relevance-Scoring.md) |
| 18 | SHU-357 | Retrieval Tool Definitions | 5 | 3-4 days | [SHU-357-Retrieval-Tool-Definitions.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-357-Retrieval-Tool-Definitions.md) |
| 19 | SHU-345 | Invocation Policy Service | 5 | 2-3 days | [SHU-345-Invocation-Policy-Service.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-345-Invocation-Policy-Service.md) |
| 20 | SHU-346 | Iterative Refinement Logic | 5 | 3-4 days | [SHU-346-Iterative-Refinement-Logic.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-346-Iterative-Refinement-Logic.md) |
| 21 | SHU-356 | Retrieval Feedback Loop | 5 | 2-3 days | [SHU-356-Retrieval-Feedback-Loop.md](./tasks/SHU-339-SHU-RAG-Intelligent-Retrieval/SHU-356-Retrieval-Feedback-Loop.md) |

**Total Estimated Effort**: 50-67 days (not including SHU-15/SHU-18 dependencies)

Note: Phase 1 effort increased to account for chunk profiling alongside document profiling.

---

## 8. Design Decisions

The following questions have been resolved:

1. **Profiling Model Selection**: Use the configurable side-caller model for document ingestion-time intelligence features. This allows flexibility while maintaining cost control.

2. **Question Count Heuristics**: Not artificially fixed. Configurable maximum, but question count is determined by document summary coverage. Larger documents with more main ideas generate more questions. The limit ensures questions cover all main ideas without redundancy.

3. **Score Fusion Weights**: Configurable with sensible defaults. Initial weights from whitepaper Section 4.4. Weights can be tuned based on testing and eventually learned from feedback.

4. **Profile Staleness**: Event-based updates. User profiles are refreshed when relevant documents are ingested (e.g., new emails from a contact increase affinity). No fixed refresh interval.

5. **Agentic Cost Bounds**: Reuse existing AGENT_MAX_TOOL_CALLS environment variable if defined. Default: 5 iterations. Timeout: 30 seconds. Budget-aware tool selection prefers manifest inspection over full document loading.

---

*Document Status: Active*
*Last Updated: 2025-12-16*


