# Shu RAG: Query-Aware Knowledge Synthesis with Relational Context

**Draft v0.1**

**Authors**: Jon McClure, Scotch McClure

---

## Abstract

Retrieval-Augmented Generation (RAG) systems fail on interpretive, synthesized, and relational queries because they conflate semantic similarity with query fitness. This paper presents Shu RAG, an architecture that bridges the gap between query-space and document-space through ingestion-time intelligence. The key innovations are:

1. **Document Profiling**: AI reads documents at ingestion to generate capability manifests describing what questions each document can answer
2. **Question Synthesis**: Generation of hypothetical queries and their embeddings as first-class searchable artifacts
3. **Relational Capability Mapping**: Extending capability manifests to include who, when, and in what context a document is relevant
4. **Multi-tier Retrieval**: Query-first matching against synthesized questions before falling back to content embeddings
5. **Agentic Orchestration**: A reasoning agent dynamically selects retrieval strategies and iteratively refines searches based on intermediate results

---

## 1. The Problem: RAG Does Not Understand Content

### 1.1 The Librarian Who Never Reads

Consider a librarian who catalogs every book by photographing random pages and filing them by visual similarity. When asked "Which book has the most tragic ending?", this librarian cannot answer. The question requires having read the books.

This is how most RAG systems operate today:

- Documents are chunked into fragments
- Fragments are embedded into vector space based on semantic content
- Retrieval matches query embeddings to fragment embeddings via cosine similarity
- The LLM receives fragments and attempts to answer

The fundamental flaw: **embedding similarity measures content overlap, not query fitness**.

### 1.2 The Query-Space Gap

User queries exist in a different semantic space than document content:

| Query Type | Example | Why Standard RAG Struggles |
|------------|---------|----------------------------|
| Interpretive | "Who is the main character?" | Answer distributed across narrative arc |
| Synthesized | "Summarize the key findings" | Requires reading entire document |
| Relational | "What did John promise me last week?" | Requires identity resolution + intent extraction |
| Structural | "What topics does this report cover?" | Requires document-level comprehension |
| Temporal | "What changed since the last version?" | Requires version comparison |

In each case, the answer cannot be found by matching the query against text chunks.

### 1.3 The Missing Intelligence

Standard RAG pipelines have intelligence at query time (the LLM) but not at ingestion time. Documents enter the system as passive data. No AI reads them, interprets them, or catalogs what they can answer.

**Shu RAG inverts this**: intelligence is applied at ingestion to create query-aware artifacts that bridge the gap.

### 1.4 Scope and Assumptions

This paper presents an architectural vision, not a deployed production system. The design targets enterprise knowledge corpora: email, documents, meeting transcripts, tickets, and similar semi-structured content where relational context (who, when, which project) is as important as content.

We assume:
- Access to user interaction signals (opens, replies, stars) for profile construction
- Availability of LLM inference at ingestion time for document profiling
- A user profile store maintained separately from the document index

The architecture is modular; components can be adopted incrementally based on deployment constraints.

---

## 2. Core Architecture: Ingestion-Time Intelligence

### 2.1 Document Profiling

When a document enters Shu RAG, an AI "librarian" reads it and generates structured metadata:

```
DocumentProfile {
  synopsis: string           // 1-paragraph abstract
  document_type: enum        // narrative, transactional, technical, conversational
  key_entities: Entity[]     // people, organizations, concepts
  temporal_scope: DateRange  // when is this document about
  capability_manifest: CapabilityManifest
  synthesized_questions: Question[]
  relational_context: RelationalContext
}
```

This profile is stored alongside the document and its chunks, creating multiple retrieval surfaces.

### 2.1.1 Chunk Profiling

In addition to document-level profiling, each chunk receives lightweight metadata:

```
ChunkProfile {
  summary: string            // One-line description of chunk content
  keywords: string[]         // Specific extractable terms (names, numbers, dates)
  topics: string[]           // Conceptual categories the chunk relates to
}
```

**Keywords** are specific, extractable terms from the chunk: "Q3", "$2.5M", "Sarah Chen", "API rate limit".

**Topics** are conceptual categories: "budget planning", "quarterly review", "authentication".

Chunk profiles serve two purposes:
1. **Fast filtering**: Keyword/topic matching (O(log n) via index) can eliminate 90% of chunks before expensive vector search
2. **LLM-scannable index**: An agentic retriever can scan chunk summaries to navigate top-down from document to relevant chunks without loading full content

### 2.2 Capability Manifests

A document's *capability* is the set of user questions for which the document contains sufficient evidence to support a satisfactory answer. Rather than leaving this implicit in embeddings, Shu RAG makes it explicit through capability manifests.

A capability manifest declares what questions a document can answer:

```
CapabilityManifest {
  answers_questions_about: Topic[]
  provides_information_type: InfoType[]  // facts, opinions, instructions, decisions
  authority_level: enum                   // primary source, summary, reference
  completeness: enum                      // comprehensive, partial, fragmentary
  question_domains: QuestionDomain[]      // who, what, when, where, why, how
}
```

Example for a meeting transcript:
```
{
  answers_questions_about: ["Q3 roadmap", "budget allocation", "team assignments"],
  provides_information_type: ["decisions", "action items", "context"],
  authority_level: "primary source",
  completeness: "comprehensive for meeting scope",
  question_domains: ["what was decided", "who is responsible", "when is deadline"]
}
```

### 2.3 Question Synthesis

The most novel component: generating hypothetical queries the document can satisfy.

For each document, the profiling AI generates 5-20 questions that someone might ask and this document would answer. These questions are embedded and stored as searchable artifacts.

**Example for a technical specification:**
- "What are the API rate limits?"
- "How do I authenticate with the service?"
- "What error codes can the endpoint return?"
- "Is there a sandbox environment?"

**Example for an email thread about a deal:**
- "What is the status of the Acme proposal?"
- "Who is our contact at Acme?"
- "What were the pricing objections?"
- "When is the decision expected?"

At query time, the user's question is matched against these synthesized questions first. High similarity indicates the document can likely answer the query, even if the query text doesn't appear verbatim in the content.

---

## 3. Relational Capability Mapping

### 3.1 The Relational Dimension

Documents don't exist in isolation. Their relevance depends on:

- **Who** is asking (identity, role, permissions)
- **Who** the document is about (participants, stakeholders)
- **What relationships** exist between asker and document subjects
- **What context** the asker brings (current projects, recent interactions)

Standard RAG ignores all of this. Shu RAG makes it explicit.

### 3.2 Relational Context Schema

Each document profile includes relational metadata:

```
RelationalContext {
  participants: ParticipantLink[]
  relevance_by_role: RoleRelevance[]
  project_associations: ProjectLink[]
  temporal_relevance: TemporalRelevance
  interaction_signals: InteractionSignal[]
}

ParticipantLink {
  entity_id: string
  role_in_document: enum    // author, recipient, subject, mentioned, decision_maker
  affinity_to_user: float   // from user profile: how important is this person to the user
  recency: timestamp        // last interaction between user and this entity
  reciprocity: float        // balance of communication (high = mutual, low = one-way)
}

RoleRelevance {
  user_role: string         // e.g., "CEO", "engineer", "sales lead"
  relevance_score: float    // how relevant is this document to someone in this role
  relevance_reason: string  // "contains budget decisions", "technical implementation details"
}

ProjectLink {
  project_id: string
  association_strength: float
  association_type: enum    // primary, related, tangential
}

TemporalRelevance {
  freshness_decay: float    // how quickly does this document become stale
  deadline_proximity: Date? // if document relates to a deadline
  recurrence: Cadence?      // if document type recurs (weekly reports, monthly reviews)
}

InteractionSignal {
  signal_type: enum         // opened, replied, starred, used_in_answer, escalated
  timestamp: timestamp
  weight: float             // contribution to relevance scoring
}
```

### 3.3 Integration with User Profile

The relational context connects documents to the User Profile schema:

| Profile Feature | Relational Impact on Retrieval |
|-----------------|-------------------------------|
| Identity & Roles | Filter by role relevance; prioritize documents where user is author/recipient |
| Relationships (contact graph) | Boost documents involving high-affinity contacts |
| Projects | Boost documents associated with active projects |
| Work Patterns | Time-weight documents based on user's working hours |
| Interests & Topics | Boost documents matching extracted interest keywords |
| Preferences | Adjust retrieval to match user's decision style (detail vs. summary) |
| Availability & Constraints | Deprioritize documents about meetings during travel |
| Knowledge Links | Recognize when user has already seen/used this content |

### 3.4 Relational Query Enhancement

When a query arrives, Shu RAG enhances it with relational context:

**Raw query**: "What's the status of the proposal?"

**Enhanced with profile context**:
- User's active projects: ["Acme Deal", "Q4 Planning"]
- User's high-affinity contacts: ["John Smith", "Sarah Chen"]
- User's role: "VP Sales"
- Recent document interactions: [doc_123, doc_456]

**Relational retrieval boost**:
- Documents about "Acme Deal" or "Q4 Planning" proposals get +0.3 score
- Documents authored by or mentioning John/Sarah get +0.2 score
- Documents user has previously interacted with get +0.1 score (familiarity)
- Documents flagged as "sales-relevant" get +0.15 score

This transforms an ambiguous query into a personalized retrieval that reflects the user's actual context.

---

## 4. Multi-Tier Retrieval Architecture

### 4.1 Retrieval Surfaces

Shu RAG maintains multiple retrieval surfaces, each optimized for different query types:

| Surface | Contents | Best For |
|---------|----------|----------|
| Synthesized Questions | LLM-generated hypothetical queries | Interpretive/structural queries |
| Capability Manifests | Explicit topic/domain declarations | "Which documents address X" queries |
| Synopses | Document-level summaries | Broad topic matching |
| Relational Index | Participant/project/temporal links | "Who/when" queries |
| Content Chunks | Standard chunk embeddings | Specific fact retrieval |
| Structured Extractions | Intents, states, entities | Deterministic filtering |

### 4.2 Query Classification

Before retrieval, classify the query to determine which surfaces to prioritize:

```
QueryClassification {
  query_type: enum          // factual, interpretive, relational, temporal, structural
  entity_references: Entity[] // extracted entities from query
  temporal_scope: DateRange?
  implied_document_type: DocType?
  confidence: float
}
```

### 4.3 Retrieval Pipeline

```
1. CLASSIFY: Determine query type and extract entities
2. PROFILE-MATCH: If relational/temporal, query user profile for context
3. QUESTION-MATCH: Compare query embedding to synthesized question embeddings
4. MANIFEST-MATCH: Filter by capability manifests if query domain is clear
5. SYNOPSIS-MATCH: Match against document synopses for document-level relevance
6. RELATIONAL-BOOST: Apply profile-based scoring adjustments
7. CHUNK-MATCH: Standard vector similarity against content chunks
8. RANK-MERGE: Combine scores across surfaces with learned weights
9. CONTEXT-EXPAND: For top results, pull related context (thread, entities)
10. ESCALATE: If chunk scores low but synopsis scores high, load full document
```

### 4.4 Score Fusion

Final document score combines multiple signals:

```
score_final =
    w_q * score_question_match +
    w_s * score_synopsis_match +
    w_c * score_chunk_match +
    w_r * score_relational_boost +
    w_t * score_temporal_relevance +
    w_i * score_interaction_history
```

Weights are configurable per deployment and can be learned from user feedback.

---

## 5. Agentic Retrieval Orchestration

### 5.1 Beyond Static Pipelines

The multi-tier retrieval pipeline described in Section 4 executes a fixed sequence of operations. While effective for many queries, complex questions benefit from dynamic orchestration by a reasoning agent that can adapt its retrieval strategy based on intermediate results.

### 5.2 Retrieval Tools

The agent has access to distinct tools for each retrieval surface:

```
Tools {
  query_synthesized_questions(query) → document candidates with scores
  query_capability_manifests(topic, domain) → document candidates
  query_synopses(query) → document candidates with synopsis text
  query_relational_index(entity_id, relationship_type) → related documents
  query_chunks(query, doc_ids?) → content chunks
  inspect_manifest(doc_id) → capability manifest for single document
  get_document_context(doc_id) → synopsis + relational context
  load_full_document(doc_id) → full text (escalation)
}
```

Each tool returns structured results the agent can reason over before deciding next steps.

### 5.3 Capabilities Enabled by Agentic Orchestration

**Strategic Surface Selection**: The agent reasons about which surface to query based on query classification, rather than following a fixed pipeline. A factual query might go directly to chunks; an interpretive query starts with synthesized questions.

**Manifest Interrogation**: Before retrieving content, the agent can inspect a document's capability manifest to determine if the document is worth pursuing. This avoids wasted chunk retrieval for irrelevant documents.

**Iterative Refinement with State**: The agent maintains state across iterations: which queries it has tried, which documents it has examined, what quality signals it has received. This informs subsequent retrieval decisions.

**Cross-Surface Triangulation**: When signals conflict (synopsis says "relevant" but chunks return low scores), the agent can reason about the discrepancy and trigger appropriate action (e.g., full document load).

**Query Decomposition**: Complex queries can be broken into sub-queries, each routed to the appropriate surface. Results are then synthesized.

**Relational Graph Traversal**: The agent can follow entity links to expand search. "Who else was in that meeting?" leads to new entity queries.

### 5.4 Example: Agentic Resolution of Complex Query

**Query**: "What did we agree about pricing with Acme, and has anything changed since?"

**Static pipeline behavior**: Matches "pricing" + "Acme" against chunks. Returns fragments. May miss temporal dimension.

**Agentic behavior**:

```
Step 1: Classify query → relational + temporal
Step 2: query_relational_index(entity="Acme", type="deal")
        → Returns [doc_123, doc_456, doc_789]
Step 3: inspect_manifest(doc_123)
        → "contains: pricing decisions, Q3 2024"
Step 4: inspect_manifest(doc_456)
        → "contains: pricing revision, Q4 2024"
Step 5: inspect_manifest(doc_789)
        → "contains: Acme contact info" (not relevant)
Step 6: Reason: doc_456 is newer, may contain changes. Query both.
Step 7: query_chunks("pricing agreement terms", doc_ids=[doc_123])
        → Original pricing terms
Step 8: query_chunks("pricing changes revisions", doc_ids=[doc_456])
        → Revision details
Step 9: Synthesize answer with temporal ordering
```

The agent's reasoning about tool selection, ordering, and intermediate interpretation produces a more complete answer than static retrieval.

### 5.5 Termination Conditions

The agent continues retrieval until:
- Confidence threshold met (retrieved content sufficiently answers query)
- Iteration limit reached (prevent infinite loops)
- No new relevant documents found (search exhausted)
- User-specified constraints satisfied (e.g., "only from last month")

### 5.6 Invocation Policy

Not every query requires agentic retrieval. The static pipeline (Section 4) is faster and sufficient for most queries. The agent is invoked when:

- **Query classification suggests complexity**: Queries classified as relational + temporal, or multi-hop, route to the agent
- **Static pipeline returns low confidence**: If the top results score below a threshold, the agent attempts iterative refinement
- **Explicit user request**: "Find everything about..." or "dig deeper" signals trigger agentic mode
- **Cross-document reasoning required**: Questions requiring synthesis across multiple documents benefit from agent coordination

Cost and latency bounds constrain agent behavior:
- Maximum iteration depth (e.g., 5 tool calls) prevents runaway inference
- Per-query timeout forces termination regardless of confidence
- Budget-aware tool selection prefers manifest inspection over full document loads

### 5.7 Feedback Loop

Agent discoveries can update the knowledge base:
- Correct misclassified document types
- Add missing entity links discovered during traversal
- Update relational context based on new understanding
- Flag documents with stale capability manifests

This creates a learning system where retrieval improves the underlying index.

---

## 6. Implementation Considerations

### 6.1 Ingestion Cost

Document and chunk profiling require LLM inference at ingestion time. Cost considerations:

- **Amortization**: Profiling cost is paid once; retrieval benefits accrue over document lifetime
- **Tiered profiling**: High-value documents (contracts, reports) get full profiling; low-value (notifications) get minimal
- **Incremental updates**: For documents that change, only regenerate affected profile components
- **Async processing**: Profiling runs in background; documents are immediately searchable via chunks
- **Chunk batching**: Chunk profiling can batch multiple chunks per LLM call to reduce API overhead
- **Proportional cost**: Chunk profiling adds N LLM calls per document (batched), where N scales with document size

### 6.2 Storage Requirements

Additional storage per document:

| Component | Approximate Size |
|-----------|-----------------|
| Synopsis (text) | 500-1000 chars |
| Synopsis embedding | 384 floats (1.5KB) |
| Capability manifest | 200-500 chars JSON |
| Synthesized queries (10) | 10 * (100 chars + 1.5KB embedding) = 16KB |
| Relational context | 500-2000 chars JSON |

Additional storage per chunk:

| Component | Approximate Size |
|-----------|-----------------|
| Summary (text) | 100-200 chars |
| Keywords (JSONB) | 100-500 chars |
| Topics (JSONB) | 100-300 chars |

Document overhead: ~20-25KB per document, plus ~1.5KB per synthesized query.
Chunk overhead: ~0.5-1KB per chunk.

For 100,000 documents with 10 queries each and 50 chunks per document:
- Document profiles: ~25KB * 100K = 2.5GB
- Query embeddings: 1.5KB * 10 * 100K = 1.5GB
- Chunk profiles: 0.75KB * 50 * 100K = 3.75GB
- Total: ~8GB additional storage. Manageable.

Note: Using 384-dimension embeddings (MiniLM) instead of 1536 (OpenAI) significantly reduces storage.

### 6.3 Query Latency

Multi-tier retrieval adds latency. Mitigations:

- **Parallel queries**: Hit all surfaces simultaneously
- **Early termination**: If question-match returns high-confidence results, skip chunk matching
- **Caching**: Cache profile context and frequent query classifications
- **Approximate search**: Use HNSW/IVF indices for all vector surfaces

Target: <100ms additional latency over standard RAG.

### 6.4 Profile Staleness

User profiles must stay current for relational boosting to work:

- **Continuous learning**: Update profile features as new documents are ingested
- **Decay functions**: Old interaction signals fade over time
- **Explicit refresh**: Allow users to update profile preferences
- **Freshness indicators**: Track when each profile feature was last updated

---

## 7. Related Work

### 7.1 Standard RAG

| Aspect | Standard RAG | Shu RAG |
|--------|-------------|---------|
| Ingestion intelligence | None | Full document profiling |
| Query-document bridging | Embedding similarity only | Synthesized questions + manifests |
| Personalization | None | Profile-based relational boosting |
| Interpretive queries | Fails | Answered via question synthesis |
| Relational queries | Fails | Answered via relational index |

### 7.2 Knowledge Graphs

Knowledge graphs extract entities and relationships but:
- Require explicit schema definition
- Miss nuanced/contextual relationships
- Don't generate query-answerable artifacts

Shu RAG complements KGs: the relational context can feed into and draw from a knowledge graph.

### 7.3 HyDE (Hypothetical Document Embeddings)

HyDE generates hypothetical documents at query time to improve retrieval. Shu RAG inverts this by generating hypothetical queries at ingestion time. This amortizes cost across all future queries, provides multiple questions per document for richer coverage, and enables inspection and correction of generated questions.

### 7.4 Query Expansion and Rewriting

Traditional query expansion techniques (pseudo-relevance feedback, synonym expansion, query rewriting) operate at query time and modify the query to improve recall. Shu RAG's question synthesis operates at ingestion time, pre-generating the queries a document can answer rather than expanding user queries.

### 7.5 Learning-to-Rank

Learning-to-rank systems train models to score document relevance based on features. Shu RAG's score fusion (Section 4.4) is compatible with learned ranking, but the primary contribution is the additional retrieval surfaces that provide features unavailable to standard systems.

### 7.6 Personalized Search

Personalized search systems maintain user models to bias retrieval. Shu RAG's relational context (Section 3) extends this by indexing documents with relational metadata at ingestion time, enabling bidirectional matching between user profiles and document profiles.

### 7.7 Multi-Hop Question Answering

Multi-hop QA systems decompose complex questions into sub-questions and chain retrieval steps. Shu RAG's agentic orchestration (Section 5) provides similar capabilities but with explicit tool selection across heterogeneous retrieval surfaces rather than repeated vector search.

### 7.8 Agentic Retrieval (ReAct, Tool-Use Agents)

ReAct and similar frameworks enable LLMs to reason about tool use. Shu RAG extends this pattern by providing the agent with purpose-built retrieval tools (manifest inspection, relational index queries) that expose the ingestion-time intelligence as actionable surfaces.

---

## 8. Limitations and Open Questions

### 8.1 Dependence on LLM Profiling Quality

The architecture's effectiveness depends on accurate document profiling. If the profiling LLM generates incorrect capability manifests or irrelevant synthesized questions, retrieval quality degrades. Mitigation requires profiling validation and mechanisms to correct profiles based on retrieval feedback.

### 8.2 Cold-Start Problem

New users have empty profiles. Until sufficient interaction history accumulates, relational boosting provides no benefit. The system must fall back to non-personalized retrieval for new users, with progressive enhancement as profiles develop.

### 8.3 Computational Cost of Agentic Iteration

Agentic retrieval adds latency and cost. Each reasoning step requires LLM inference. For simple queries, the static pipeline (Section 4) is more efficient. The system must balance retrieval quality against computational budget.

### 8.4 Profile-Based Filter Bubbles

Personalization risks creating filter bubbles where users only see documents reinforcing existing patterns. The relational boost should be bounded to preserve serendipitous discovery.

### 8.5 Profile Privacy

User profiles contain sensitive information about relationships, interests, and behavior patterns. The architecture must address profile data governance, user consent, and data minimization.

### 8.6 Evaluation Metrics

Standard retrieval metrics (precision, recall, MRR) may not capture the value of relational and interpretive retrieval. New evaluation frameworks are needed to measure whether the system answers questions that standard RAG cannot.

---

## 9. Conclusion

Shu RAG addresses the fundamental limitation of current RAG systems: they match content, not capability. By applying intelligence at ingestion time to generate question-aware and relationship-aware metadata, Shu RAG enables retrieval that understands what documents can answer, not just what they contain.

The five key innovations form a coherent architecture:

1. **Question synthesis** bridges the query-space gap by generating hypothetical queries at ingestion
2. **Capability manifests** declare what each document can answer, enabling fast filtering
3. **Relational context** connects documents to people, projects, and time, enabling personalization
4. **Multi-surface retrieval** routes queries to optimal retrieval strategies based on classification
5. **Agentic orchestration** enables dynamic, iterative retrieval for complex queries

These innovations are complementary and can be adopted incrementally. Together, they transform RAG from a content-matching system into a knowledge-aware assistant that retrieves like a librarian who has actually read the books.

---

*Document Status: Draft for review*
*Last Updated: 2025-12-16*

