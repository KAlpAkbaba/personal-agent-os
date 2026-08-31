# Roadmap

## M-1 — Environment & Bootstrap

Goal: make the development machine reproducible and report capabilities.

Deliverables:

- environment report;
- Git hygiene;
- toolchain;
- dev scripts;
- initial lock/version strategy.

## M0 — Foundation

Goal: compile/run a minimal cloud-core stack locally.

Deliverables:

- repo structure;
- web shell;
- FastAPI API;
- PostgreSQL/pgvector;
- Redis;
- MinIO dev S3;
- Temporal dev;
- logging/telemetry skeleton;
- CI;
- quality gate.

## M1 — Cloud / Windows Device Link

Goal: owner UI can execute a safe Windows action through cloud broker.

Deliverables:

- device service + session companion;
- enrollment;
- outbound WSS;
- command protocol;
- reconnect/idempotency;
- Notepad E2E.

M1 is delivered local-first: broker and agent run and are acceptance-tested entirely on the development machine (loopback transport). Tailscale/Hetzner provisioning is a deployment step that reuses the same protocol and is unblocked separately by the owner actions list.

## M2 — Browser Agent

Goal: reliable semantic browser automation.

Deliverables:

- Playwright adapter;
- MCP integration in development;
- browser extension/CDP path;
- browser error taxonomy;
- test browser E2E.

## M3 — Research & Artifact

Goal: research command produces durable report and can open/send it.

Deliverables:

- Research workflow;
- source/evidence model;
- artifact service;
- PDF/DOCX render;
- Artifact Inbox UI;
- Windows open artifact.

## M4 — Voice Core & Narration

Goal: natural Turkish voice interface.

Substages:

- M4A STT/provider benchmark;
- M4B owner speaker verification;
- M4C realtime dialogue;
- M4D Turkish narration engine;
- M4E cross-device narration cursor.

## M5 — Memory

Goal: durable owner/project/procedural memory.

## M6 — Self-Healing Engineering

Goal: detect an injected bug, restore service, generate and validate a fix automatically.

## M7 — Self-Extension/Evolution

Goal: add a genuinely missing capability and resume the original task automatically.

## M8 — Authorized Security Agent

Goal: owner-authorized asset scope and autonomous defensive testing/remediation workflow.

## M9 — Native Mobile

Goal: stronger always-available mobile voice experience beyond PWA limitations.

## M10 — Optimization

Only after real use:

- local voice fallback;
- local models;
- additional devices;
- HA cloud;
- advanced screen-history learning;
- proactive workflow automation.
