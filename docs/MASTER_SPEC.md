# Master Product Specification

## A. Core user experience

The product is a single-owner personal Agent OS that is reachable from:

- Windows desktop;
- web browser;
- mobile browser/PWA;
- later, a native mobile client for stronger background voice behavior.

It maintains one continuous owner context across devices.

## B. Primary commands and expected behavior

### Research

Owner: “Son üç günde yapay zekâ ajanlarıyla ilgili önemli gelişmeleri araştır.”

System:

1. creates a durable task;
2. plans search/research;
3. uses cloud web research when sufficient;
4. delegates to owner browser/device when a logged-in session or desktop context is required;
5. verifies/scores sources;
6. writes memory links;
7. generates executive summary and detailed report;
8. persists artifact and renderings;
9. notifies: “Araştırma tamamlandı. Rapor hazır.”;
10. waits.

### Narration

Owner: “Oku.”

System begins with executive-summary narration. Supports:

- stop;
- resume;
- repeat last paragraph;
- next section;
- jump to a topic;
- ask a question about the current paragraph;
- then continue from the saved narration cursor.

### Presentation

Owner: “Gönder.”

System presents the artifact on the current device in the learned/preferred format, normally PDF on mobile. It may offer PDF/DOCX/HTML/audio without losing the canonical artifact.

Owner: “Bilgisayarda aç.”

System selects an online enrolled Windows device and opens the artifact with the correct local application.

### Cross-device continuity

A task started on PC may be narrated on phone hours later. The narration cursor, artifact identity and conversation context are cloud-persistent.

## C. Device control

Windows capabilities should eventually include:

- process/application launch;
- browser control;
- filesystem read/write under owner policy;
- Word/Excel/PowerPoint/PDF interaction;
- clipboard;
- keyboard/mouse fallback;
- UI Automation/Win32/COM where available;
- screenshots and visual fallback;
- PowerShell and shell execution;
- multi-monitor targeting;
- downloads/uploads;
- local notification and file open.

The Windows Agent is split into a machine service and interactive user-session companion.

## D. Browser control

Browser control should prefer semantic automation. Support:

- dedicated automation browser profile;
- connection to current Chrome/Edge when requested;
- reuse of authenticated sessions through extension/CDP path;
- DOM/accessibility snapshot;
- download/upload;
- source extraction;
- screenshots;
- vision fallback.

## E. Voice

Four distinct concerns:

1. **STT:** What was said?
2. **Speaker verification:** Was the owner speaking?
3. **Realtime assistant voice:** Fast natural conversation.
4. **Narration TTS:** High-quality long-form reading.

The system must benchmark providers and support local fallback rather than hard-code one vendor.

## F. Memory

Required memory types:

- episodic timeline;
- semantic/knowledge memory;
- owner preferences with confidence/evidence;
- procedural memory (how repeated tasks are done);
- project/entity relationships;
- conversation/task/artifact links;
- voice/pronunciation preferences.

Memories are inspectable, editable and deletable.

## G. Executive layer

Default response tiers:

- `EXECUTIVE`: decision/answer in seconds;
- `IMPORTANT`: what materially affects the owner;
- `DEEP_DIVE`: detailed technical/source material.

Voice should follow the same hierarchy.

## H. Artifact system

Canonical artifact object contains:

- immutable ID;
- task/conversation/project links;
- title/type;
- canonical semantic body;
- sources/citations;
- versions;
- rendered files;
- voice/narration metadata;
- lifecycle state;
- retention class.

Expected renders:

- Markdown;
- PDF;
- DOCX;
- HTML;
- TXT where useful;
- narration audio chunks on demand.

## I. Self-healing

The product should automatically detect and remediate software failures through a controlled engineering pipeline. It needs last-known-good rollback independent of the broken service.

## J. Self-extension

When a request requires a missing capability:

1. detect capability gap;
2. attempt composition of existing capabilities;
3. extend existing skill if appropriate;
4. otherwise generate a new skill/module;
5. test and evaluate;
6. register capability;
7. continue original task.

The owner should hear a short status only when useful, e.g. “Bu iş için eksik bir modülü hazırlıyorum.”

## K. Authorized cybersecurity

Maintain Authorized Asset Registry with:

- asset ID;
- ownership/authorization evidence metadata;
- CIDR/hostname/device identity;
- environment (`lab`, `dev`, `prod`);
- allowed testing classes;
- maintenance constraints;
- expiry/review timestamp if applicable.

Within scope, security agents may automate defensive validation and produce remediation artifacts. Do not automatically extend scope to unrelated targets.

## L. Non-goals for early versions

- multi-tenant SaaS;
- billing/subscriptions;
- organization/user administration;
- Kubernetes;
- fully local LLM-only operation;
- training a foundation model;
- always-on mobile background wake-word before native app stage.
