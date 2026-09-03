# Project Constitution

## 1. Purpose

Build a private Personal Agent OS for exactly one owner. It should understand the owner over time, operate across the owner's devices, create and preserve useful artifacts, use voice as a primary interface, minimize cognitive overload through executive summaries, and evolve its own capabilities safely and autonomously.

## 2. Owner model

There is exactly one human authority: `OWNER`.

The system does not need SaaS tenants, subscription plans, organization administration, user roles or per-user quotas. Internal service identities may still exist for technical isolation.

## 3. Interaction principles

- Default language: Turkish.
- Default output: executive summary first.
- Detailed material remains available on demand.
- A completed background task sends a short readiness notification and waits.
- “Oku” starts narration; “dur”, “devam”, “tekrar”, “şu bölümü açıkla” must work naturally.
- “Gönder” presents/exports the artifact on the current device.
- “Bilgisayarda aç” delegates to the selected Windows device.
- Conversation/task context follows the owner across desktop, mobile and web.

## 4. Artifact principle

Every substantive task may produce a canonical artifact independent from the chat UI.

Core relationship:

`Task -> Artifact -> Presentation`

Artifact formats may include Markdown, PDF, DOCX, HTML, TXT, JSON, audio or other generated formats. Canonical content and rendered formats must be versioned.

## 5. Autonomous operation

The owner should not perform routine software maintenance.

The system must progressively own:

- monitoring;
- incident detection;
- reproduction;
- root-cause analysis;
- bug fixing;
- regression testing;
- release creation;
- canary/shadow validation;
- rollback;
- capability-gap detection;
- new skill/module creation;
- measurable optimization.

## 6. Evolution boundaries

Self-development is required, but uncontrolled in-place mutation is prohibited.

The main product may evolve its modules and services. A minimal recovery root remains independently executable:

- owner identity root;
- secret root;
- update signature verification;
- last-known-good release pointer;
- backup/restore primitives;
- recovery supervisor.

A new version of those components may be built and tested automatically, but activation must use a two-phase/recoverable update process.

## 7. Voice

Voice quality is a core acceptance criterion, not a cosmetic feature.

- Strong Turkish STT.
- Speaker verification must distinguish `OWNER`, `NOT_OWNER`, `UNCERTAIN`.
- Realtime conversation must support interruption/barge-in.
- Long-form Turkish narration must be natural and resumable across devices.
- Pronunciation must be personalized over time.
- Raw tables, code and logs should be semantically narrated instead of blindly read unless the owner explicitly requests literal reading.

## 8. Security authority

The owner has root authority over owner-enrolled devices and assets that are explicitly recorded as authorized.

Security-testing capability should be gated by **target authorization scope**, not by generic repeated approval prompts.

No task may automatically broaden from authorized assets to unrelated third-party infrastructure.

## 9. Privacy

Prefer local capture and owner-controlled storage for sensitive persistent context. Minimize retention of raw audio/screen data when derived structured memory is sufficient. The owner must be able to inspect, correct and delete remembered facts.

## 10. Provider independence

No critical domain concept may be owned by a single external AI provider. Model, TTS, STT, embedding, object-storage and coding-agent providers must be replaceable behind interfaces.

## 11a. Multi-device, roaming owner (architectural invariant)

PersonalAgentOS is never tied to one microphone, one Windows PC or one SID. The Hetzner
Cloud Core is the authoritative control plane and owner brain; any owner-authorised,
enrolled device attaches to the same owner identity without per-machine source edits or
manual configuration after enrollment. Device configuration, capabilities, policies and
updates are centrally managed; each device advertises its capabilities (browser, desktop
control, microphone, speakers, GPU, filesystem, integrations) and Cloud Core selects the
device for an action - explicitly when the owner names one ("ev bilgisayarımda aç", "iş
bilgisayarımda çalıştır", "laptopta devam et"), otherwise by presence, capability and
policy. Conversation, memory, tasks, research state and preferences roam; a voice session
may move between desktop, laptop, web and phone without a new identity. Microphone/DSP
settings are per-device, per-microphone profiles - a newly encountered microphone is
calibrated automatically and stored on its own, and one noisy profile never degrades
another machine. The Arbor target voice is an owner-level profile across devices. Device
key material is unique per machine; a lost device is removable centrally without rotating
the owner identity; reaching the tailnet grants nothing by itself. Reconnect after reboot
or network loss needs no owner intervention. Nothing is designed around a hardcoded machine
name, path, audio device id or SID (guarded by a test). Acceptance is the
MULTI-DEVICE / ROAMING OWNER QUALIFICATION milestone in `docs/ROADMAP.md`.

## 11. Reliability over novelty

A new autonomous feature is promoted only when measurable quality is at least as good as the current version on required regression/evaluation suites. Otherwise it is rejected or remains experimental.
