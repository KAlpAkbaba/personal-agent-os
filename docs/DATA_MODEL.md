# Data Model — Initial Domain Schema

This document defines domain concepts, not exact SQL. Claude should create migrations during M0/M1.

## Singleton owner

### owner

- id
- display_name
- locale
- timezone
- created_at
- settings_json

Exactly one active row.

## Devices

### devices

- id
- name
- platform
- public_key
- capabilities_json
- tags
- enrolled_at
- last_seen_at
- status
- revoked_at

### device_sessions

- id
- device_id
- connection_id
- started_at
- ended_at
- software_version
- network_metadata

## Conversations/tasks

### conversations

- id
- title
- project_id nullable
- created_at
- updated_at

### messages

- id
- conversation_id
- role
- canonical_text
- modality
- device_id nullable
- created_at

### tasks

- id
- conversation_id
- intent
- status
- priority
- created_from_device_id
- workflow_id
- created_at
- ready_at
- completed_at

### task_runs

- id
- task_id
- attempt
- status
- plan_json
- telemetry_json
- error_class
- started_at
- ended_at

## Artifacts

### artifacts

- id
- task_id
- conversation_id
- project_id nullable
- title
- kind
- canonical_format
- state
- current_version
- created_at
- updated_at

### artifact_versions

- id
- artifact_id
- version
- canonical_object_key
- source_manifest_json
- content_hash
- created_at

### artifact_renders

- id
- artifact_version_id
- format
- object_key
- mime_type
- content_hash
- created_at

### narration_sessions

- id
- artifact_id
- artifact_version
- device_id
- semantic_cursor_json
- playback_seconds
- speed
- voice_profile_id
- updated_at

## Memory

Realized in migration `0005_memory` (M5, ADR-0023) as a superset of this
sketch:

### memories

- id
- memory_class (preference | episodic | project | semantic | procedural | voice_preference)
- key nullable
- text (canonical statement; what gets embedded)
- value_json
- stage (session | candidate | durable)
- status (active | superseded) — forgetting is a hard delete, not a status
- explicit, pinned
- confidence, evidence_count
- retention_class (session | short | standard | pinned)
- project_id -> entities.id nullable; conversation_id/task_id/artifact_id/device_id nullable
- provenance_json
- occurred_at / valid_from / valid_until
- version, superseded_by -> memories.id
- created_at, updated_at, last_confirmed_at

### memory_versions

Immutable per-version snapshots (memory_id, version, text, value_json, stage,
explicit, confidence, edited_by, change_reason, created_at).

### memory_evidence

Individual observations backing inferred memories (memory_id, kind,
source_ref_json, weight, observed_at). Cascade-deleted on forget.

### memory_embeddings

- memory_id (cascade on delete — forget removes the vector row)
- model_id, model_version, dim (re-embedding migration metadata)
- embedding vector(256), hnsw cosine index
- created_at

### memory_audit_events

Append-only (action, memory_id, memory_class, key, actor, detail_json,
trace_id, created_at). Never stores memory content after a forget.

### entities / entity_edges

Project/document/person/device/decision/system/task/capability nodes with
unique (kind, name); edges unique (src, dst, relation).

## Voice

### voice_profiles

- id
- locale
- tts_provider_preference_json
- narration_settings_json
- created_at
- updated_at

### pronunciation_entries

- id
- token
- spoken_form/provider_payload
- context
- confidence
- explicit

### speaker_profiles

- id
- encrypted_embedding_blob/reference
- model_id
- enrollment_metadata
- updated_at

## Capabilities/evolution

### capabilities

- id
- version
- status
- manifest_json
- current_release_id

### skill_versions

- id
- skill_id
- version
- git_commit
- evaluation_json
- status
- created_at

### incidents

- id
- service/component
- severity
- fingerprint
- evidence_json
- status
- introduced_release_id nullable
- fixed_release_id nullable

### releases

- id
- version
- git_commit
- manifest_digest
- status
- health_json
- created_at
- promoted_at nullable

## Authorized assets

### authorized_assets

- id
- name
- kind
- locator/cidr
- authorization_class
- environment
- allowed_testing_json
- constraints_json
- evidence_metadata
- valid_from
- valid_until nullable
- enabled

## Audit

### audit_events

- id
- category
- action
- subject_ref
- metadata_json
- timestamp
- integrity_hash optional

Audit should avoid secret payloads.
