---
name: project-m22-core-voice-track-status
description: M22 track A (Artifact Factory voice/corpus/contract) completion status, branch, and the one known real gap (device auth on the render-download URL)
metadata:
  type: project
---

Completed 2026-09-08 on branch `m22-core-voice` (based on `m22-core-scratch` merged
with `main`) in the worktree at
`E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\.claude\worktrees\agent-aa5a663ef962e780c`.
Not merged, not pushed (task rule: report only).

**What landed:** `FOCUS_KIND_ARTIFACT`; five voice tools (`app/voice/realtime_sessions/tools_artifacts.py`:
`artifact.create/render/validate/open/list`); router intents `ARTIFACT_CREATE/OPEN/LIST/VALIDATE`
in `app/voice/intents.py` (`ResolvedIntent.artifact_ref/artifact_kind/artifact_title/spoken_numbers`);
`app/artifacts/open_service.py` (shared fetch+open logic used by both the voice tool and
`POST /v1/artifacts/{id}/open` in `app/artifacts/routes.py`); UI-state contract v7
(`app/uistate/contract.py`: `UiState.ARTIFACT_FACTORY`, subsystem `artifacts`), published
from `app/artifacts/factory.py` (`create`/`render_format`/`revalidate`/`revalidate_all`);
corpus category `artifacts` (110 cases, `tests/voice_corpus/corpus.py` + `harness.py`
`CTX_ARTIFACT_FOCUSED` seeding two real artifacts through the real factory against an
in-memory object store — the harness never set `ArtifactRuntime._store` before this track,
so `artifacts.store` would have hit real S3 without this fix); `tests/unit/test_artifact_tools.py`
(14 tests) and `test_artifact_routes.py` `/open` extension (6 tests); `test_identity_enforcement.py`
and `test_uistate.py` updated. Full unit suite green, corpus green (916 cases: 806 pre-existing +
110 new, zero forbidden side effects), `ruff check .` clean.

**Why:** owner master directive 2026-09-07, M22 kickoff — this is track A (Cloud Core voice/corpus/
contract) of three parallel tracks (device = `m22-device`, already merged into `main`; web =
track C, already merged into `main`).

**Known real gap, recorded in `docs/DECISIONS.md` ADR-0085 addendum 4 decision 5 — do not let
anyone claim PROVEN_REAL for M22 until this is closed:** the M13 render-download route
(`GET /v1/artifacts/{id}/renders/{fmt}`) the device's `file.fetch` is supposed to GET is
owner-session-gated (bearer token), but `DEVICE_PROTOCOL.md` §6k requires the device's own GET to
carry no owner token, no cookie, no header at all. Nobody has built the signed-URL bypass yet
(the `app.alarms.audio_store.AudioStore` single-use-token pattern is the obvious precedent). This
track's own tests never catch it because the fakes (both the voice corpus's `FakeDeviceAction` and
the REST route's injected fake) never make a real HTTP call — they just record the payload. See
[[reference_test_commands_and_gates]] for how to run this suite; see [[feedback_rfc822_rrule_dos_bounds]]-style
"read the actual protocol doc before assuming a REST auth pattern transfers" as the general lesson
here — M13's owner-session gate was built for a browser/Cockpit caller, never audited against a
device caller with zero headers.

**A design choice worth remembering for future M22 work:** "kind" words (tablo/belge/sunum/...)
double as FORMAT words (Excel/Word/PDF/...) in Turkish. The router's `_artifact_create_match` in
`app/voice/intents.py` checks the deictic pronoun ("bunu"/"şunu") BEFORE the kind-word check for
exactly this reason — "Bunu Excel yap" must set `artifact_ref="current"` (pointing at something
that exists) even though "Excel" also matches the spreadsheet kind stem. Both `ARTIFACT_CREATE`
(new file) and "Bunu PDF yap" (render an existing one to a new format) resolve to the SAME router
intent (`Intent.ARTIFACT_CREATE`, capability `artifact.create`) — the corpus harness's own
router-capability-equality check has one added exemption (`artifact.render`) for exactly this,
mirroring the pre-existing research-tool exemptions.
