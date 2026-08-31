# Memory Specification

## 1. Memory is not chat history

Store structured, evidence-linked memory rather than endlessly inserting raw transcripts into prompts.

## 2. Canonical memory types

### Episodic

Events and sessions:

- what happened;
- when;
- on which device;
- related task/artifact/project;
- relevant owner reaction.

### Semantic / knowledge

Learned facts/concepts and synthesized knowledge with source/evidence links.

### Preference

Owner choices and interaction preferences. Store:

- value;
- confidence;
- evidence count;
- last confirmed;
- explicit vs inferred.

### Procedural

Repeated workflows and how the owner performs them.

### Relational/project graph

Entities:

- project;
- person;
- device;
- document;
- decision;
- system;
- task;
- capability.

### Voice preference

Pronunciation, pace, reading policy, provider preference by context.

## 3. Source of truth

PostgreSQL domain schema is canonical.

Use pgvector for semantic retrieval. A Mem0 adapter may be used to accelerate memory extraction/retrieval experiments, but Mem0 must not become an opaque source of truth that prevents export or migration.

## 4. Evidence model

Every inferred preference/fact should point to evidence where practical.

Example:

```json
{
  "kind": "preference",
  "key": "response.detail",
  "value": "executive_first",
  "confidence": 0.92,
  "explicit": false,
  "evidence_count": 34
}
```

Explicit owner commands outrank inference.

## 5. Write policy

Not every sentence becomes memory.

Write when:

- owner explicitly says remember/prefer/from now on;
- repeated behavior crosses a confidence threshold;
- a project decision will matter later;
- a durable workflow/procedure is discovered;
- artifact/task relationship is useful.

## 6. Correction and forgetting

Owner must be able to:

- inspect;
- edit;
- pin;
- mark wrong;
- delete;
- forget a category/time range;
- disable capture for a context.

Deletion must propagate to embeddings/indexes/derived caches.

## 7. Screen/audio capture

Screenpipe or an equivalent local adapter may provide searchable local observations. Raw capture retention must be configurable and should not automatically upload everything to cloud.

Recommended separation:

- raw screen/audio: local with short/explicit retention;
- extracted structured events: cloud-persistent when useful;
- artifacts chosen by owner: cloud object storage.

## 8. Retrieval

Combine:

- metadata filters;
- recency;
- semantic similarity;
- keyword/hybrid retrieval;
- project/entity relationships;
- owner preference confidence.

Do not let old low-confidence memory override explicit current instruction.

## 9. Memory evaluation

Test:

- factual retrieval;
- temporal retrieval;
- project disambiguation;
- preference application;
- false-memory rate;
- deletion correctness;
- cross-device continuity.
