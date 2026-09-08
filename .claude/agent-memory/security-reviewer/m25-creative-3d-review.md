---
name: m25-creative-3d-review
description: M25 Unity/Blender 3D creation security review (git diff 7c90090..bf791d5, main@bf791d5, 2026-09-08). High/Critical (verified live, PoC executes) - SceneService._finish (services/api/app/creative3d/service.py:574) hardcodes terminal=TERMINAL_VERIFIED and a success-phrased Turkish speech string regardless of compare()'s actual result; TERMINAL_UNVERIFIED is never imported/used, no EVENT_TYPE_SCENE_MISMATCH ledger event and no "mismatch" scene.activity UI-state ever gets published, even though the web contract (scenes.ts SceneRunState) has a whole dedicated "mismatch" posture the backend never emits. High - service.py's _scaffold_and_run sends manifest.run = {key: plan.tool} (the bare word "blender"/"unity"), never the full argv the device's ProjectManifest.Authorise requires (8/14 exact tokens) - project.scaffold will always be permission_denied against the real device, so the entire write path (scene.create/apply/render) is non-functional end-to-end and the device argv allowlist has never been exercised by its only production caller. Medium/High - the sha256 driver pin in drivers/manifest.json is asserted only by a CI unit test (test_manifest_pins_match_the_files_on_disk); no runtime check anywhere (Cloud Core or device) recomputes and compares it before the driver text is scaffolded and executed. Medium - compare.py's camera_aim fold unconditionally drops the "rotation" expectation for any object that ever appears in a look_at, even when a later explicit transform re-asserts a literal rotation on that object afterward - a driver that silently drops that final explicit instruction is not caught (live-verified). Low - Cloud Core's own project.run wait (timeout_s=300.0, 5 min) is shorter than Unity's real allowed run bound (device caps at 10 min 30 s) - a legitimate long Unity run can be reported as a spurious Cloud-Core-side timeout. Low - render dimensions never checked against the spec's claimed <=1920x1080 bound; mitigated by Pillow's un-disabled default decompression-bomb guard. Everything else (ScenePlan closed vocabulary/bounds, SceneInspection.cs bounds-before-read + sha256 + resolve-then-contain path confinement, ProjectManifest token-exact argv matching, Job Object bounds/env scrub, tool detection by install location never PATH, Unity dependency_unavailable honesty, REST routes owner-gated, web renders text only, migration additive, corpus delete-verb negatives) verified sound.
metadata:
  type: project
---

Reviewed git diff 7c90090..bf791d5 (M25 Unity/Blender 3D creation, merged to main at
bf791d5, 2026-09-08): Cloud Core services/api/app/creative3d/{spec,drivers,compare,
service,models,routes,tools_scene}, corpus category creative3d, migration
20260908_0032_scenes; device devices/windows-agent/src/PagentOS.SessionCompanion/
{Scenes,Projects}/*.cs, Operator/OperatorOptions.cs, DEVICE_PROTOCOL.md section 6m; web
apps/web/app/lib/uistate/scenes.ts. Read docs/M25_CREATIVE_3D_SPEC.md, ADR-0088,
both evidence JSONs, and continued the M23/M24 lessons ([[m23-app-factory-review]],
[[m24-capability-genesis-review]]) of hunting free-text-into-generated-source and
vacuous-gate patterns first. Both classes exist here too, in new forms.

**High/Critical (verified live, PoC executes) - the receipt that is supposed to be the
whole milestone's safety property ("a receipt claims only what the inspection confirms")
is unconditional: SceneService._finish (service.py:511-586) always sets
terminal=TERMINAL_VERIFIED (line 574) and speaks a success-phrased sentence
(_describe, called with no compare result at all), no matter what compare() found.**
app.actions.receipt defines TERMINAL_UNVERIFIED explicitly for "server and local
disagree / read-back mismatch" - service.py never imports it. cmp_result.ok is computed,
stored in row.compare_json, and echoed into server/detail/extra["compare"], but
never branched on. There is no EVENT_TYPE_SCENE_MISMATCH in app/ledger/vocabulary.py
and no STATE_MISMATCH in app/creative3d/models.py, even though the web contract
(apps/web/app/lib/uistate/scenes.ts, SceneRunState/scenePosture/sceneStatePhrase)
has a whole dedicated "mismatch" posture (dim/restrained, names the object) that
SceneService._publish never emits - _publish always sends row.state, which is one of
planned/scaffolded/applied/rendered/failed/dependency_unavailable, never mismatch. The
web half was built anticipating a signal the Cloud Core half never sends.

**Live PoC** (services/api, real venv, real SceneService/compare.py/fake device that
reuses the REAL blender_driver.apply_operations against a fake bpy - not a mock):
called service.create(db, device, plan={"tool": "blender", "project": "lab",
"scene": "demo", "operations": [{"op": "create_scene"}, {"op": "transform", "name": "Kup",
"location": [5.0, 5.0, 5.0]}]}, session_id="poc") - a plan that asks to move "Kup", an
object NEVER created in this plan. Result:

    execution_status: executed
    terminal_status: verified
    speech: Kup tasindi:  efendim.
    state: applied
    compare: {'ok': False, 'checked': 1, 'mismatches': [{'object': 'Kup', 'field': 'presence',
      'expected': 'present', 'actual': 'absent', 'detail': 'object not found in inspection'}],
      'reason': None}

The owner is told (in the receipt's canonical terminal_status, which TERMINAL_CLAIMABLE
treats as a fact the system may present as accomplished, and in the spoken sentence) that
"Kup was moved", while the tool's own read-back proves no such object exists. This is
reachable any time a driver silently fails an operation it cannot honour (a stale object
name, a real Blender/Unity bug, a future compromised or buggy driver) - not an exotic
edge case.

**Fix:** in _finish, branch on cmp_result.ok: when false, use TERMINAL_UNVERIFIED (or
a new failure path for checked==0/reason=="no_constraints"), build the spoken
sentence from the mismatches (naming the object/field, matching spec section 4's own promise),
publish state="mismatch" (or add a real STATE_MISMATCH and a matching
EVENT_TYPE_SCENE_MISMATCH) so the web contract's already-built "mismatch" posture is
finally reachable, and add a test in test_scene_service.py asserting
terminal_status != "verified" when compare_ok=False.

**High - the Cloud Core never builds the argv the device's allowlist requires, so the
entire write path is non-functional against the real device.** service.py::
_scaffold_and_run (line ~371-374) sends "manifest": {"entry": DRIVER_PATH_BY_TOOL[tool],
"run": {RUN_COMMAND_KEY[tool]: plan.tool}} - the run command's VALUE is literally the bare
string "blender" or "unity", never the full "blender -b <scene.blend> --python
<driver.py> -- <plan.json> <out.json>" / "unity -batchmode ..." text DEVICE_PROTOCOL.md
section 6m and ProjectManifest.Authorise (devices/windows-agent/src/PagentOS.SessionCompanion/
Projects/ProjectManifest.cs:364-406) both document as the ONLY accepted shape (token-exact,
8 tokens for blender / 14 for unity). A single-token command falls through to the bare
"case SceneCapabilityNames.BlenderProgram:"/"UnityProgram:" arms, which unconditionally
Refuse(...) -> permission_denied/command_not_allowlisted. project.scaffold validates
the manifest before writing anything (ProjectScaffold.cs:196), so this fails at the very
first device call, every time. Confirmed by exhaustive code reading (deterministic
token-count switch, no dynamic behaviour) plus grep: services/api/app/creative3d/ has NO
module that builds a full command string (unlike app.appfactory.validation's mirror of
its own device allowlist) - grepped for "scene.blend"/"blender -b"/-projectPath etc.,
zero hits outside docstrings. The Python unit tests never catch this because
tests/creative3d_support.py::FakeCreative3DDevice.scaffold never validates the manifest
command text at all (just echoes success) - so **none of the "PROVEN_REAL" Blender lab
evidence (docs/evidence/m25-blender-lab-2026-09-08.json) went through this code path**;
it ran scripts/tests/blender-scene-lab.py, which calls the driver directly, bypassing
service.py and the device's ProjectManifest/ProjectRunner entirely. The device-side
argv allowlist (SceneAllowlistTests.cs, 21 hostile variants) is real and sound in
isolation, but it has never been exercised by its only production caller.

**Fix:** service.py must assemble the full command text from the fixed conventions
(blender -b <scene-file> --python <driver filename> -- plan.json out.json, and the Unity
equivalent) and put THAT string as the manifest's run-command value - matching
DEVICE_PROTOCOL.md section 6m exactly, token for token. Because ScenePlan.project/scene are
already closed-alphabet slugs (_SLUG_RE) this is safe to do directly, but it MUST be
fixed together with the receipt-honesty finding above: once scaffold stops being refused,
every real driver hiccup will start reaching _finish and, unless that finding is also
fixed, will be reported to the owner as "verified" success.

**Medium/High - the sha256 driver pin exists only as a CI assertion, never a runtime
check.** drivers/manifest.json's own note says the pin is "asserted by
tests/unit/test_blender_driver.py::test_manifest_pins_match_the_files_on_disk" - grepped
every file that references drivers/manifest.json or _DRIVERS_DIR: only that one test.
service.py::_scaffold_and_run (line 358-360) reads driver_text = (_DRIVERS_DIR /
...).read_text(...) and ships it verbatim - no hash computed, no comparison to the pin.
Device-side, ProjectScaffold.Write (ProjectScaffold.cs:261-286) computes and returns the
sha256 of whatever it received, but never compares it to a known-good value either - it is
bookkeeping, not verification. So the spec/ADR-0088's claim ("the drivers are fixed
repository files with sha256 pinned... never edited without updating the pin: a driver that
changed silently is exactly the tampering this pin exists to catch") is enforced only at
merge/CI time; a driver tampered with AFTER that (a bad deploy, a compromised build
artifact, a local edit on the running container) runs with nothing at request time to
detect it. **Fix:** compute the sha256 of the driver bytes in _scaffold_and_run (or at
SceneService construction) and compare to drivers/manifest.json's pin before every
scaffold, refusing (dependency_unavailable or a new error_class) on mismatch.

**Medium - compare()'s camera-aim fold can miss a real, later-superseding constraint,
live-verified.** app/creative3d/compare.py:242-243:

    for cam_name in camera_aim:
        expected_by_object.get(cam_name, {}).pop("rotation", None)

pops the "rotation" expectation for ANY object that ever appears in camera_aim,
regardless of whether a LATER explicit transform ... rotation op re-asserted a literal
rotation on that same object after the look_at. The comment says a later op should win
(same as every other field in this function), but for rotation-after-look_at it never does
- the aim always wins, even when the plan's own final explicit instruction was a literal
rotation override. PoC (services/api, real compare()): plan =
[add_primitive camera "Kamera" at (0,0,5)] -> [set_camera "Kamera" look_at "Kup"] ->
[transform "Kamera" rotation=(45,0,0)]; inspection reports the camera's rotation
UNCHANGED at the add_primitive default (0,0,0) - i.e. a driver that silently dropped the
final transform. Because (0,0,0) happens to already satisfy the look_at aim (camera
directly above Kup), compare(plan, inspection) returns ok=True, checked=6,
mismatches=[] - the dropped "rotate 45 degrees on X" instruction is invisible. **Fix:** only pop
the rotation expectation up to the point in the operation SEQUENCE where the look_at last
applied; if a transform with an explicit rotation appears strictly after the last
set_camera ... look_at for that object, keep/restore it as a checked constraint (the fold
needs to track operation ORDER for this interaction, not just "did this object ever have a
look_at anywhere in the plan").

**Low - project.run's Cloud-Core-side wait is shorter than Unity's own allowed run
bound.** service.py:411-416 calls device_action.run(capability=CAPABILITY_PROJECT_RUN,
..., timeout_s=300.0) (5 minutes) while SceneCapabilityNames.UnityRunLimit is 10 minutes
and the Device Service's own per-capability ceiling for project.run
(ProtocolConstants.cs:475, RunCommandTimeoutCap) is correctly UnityRunLimit + 30s =
10:30. A legitimate Unity batch run between 5 and 10 minutes would have the Cloud Core's
own wait expire and report a spurious timeout while the device is still working fully
within its own policy - a false negative to the owner, and a device-side run the Cloud
Core's own bookkeeping (MAX_ACTIVE_SCENES, the SceneRow state machine) then loses track
of. **Fix:** raise service.py's own timeout_s for CAPABILITY_PROJECT_RUN to at least
match RunCommandTimeoutCap (~10:30), keeping the shorter web-run answer time as
irrelevant (a ceiling, not a wait, per DEVICE_PROTOCOL.md's own framing) - the same
reasoning the device side already correctly applied.

**Low - render pixel dimensions are never checked against the spec's own claimed bound.**
Spec section 7 claims "the render size bounded (<= 1920x1080)"; in code, only BYTE size is bounded
(device: SceneInspection.MaxRenderBytes before read; Cloud Core:
compare.RENDER_MIN_BYTES/PIL non-uniformity in check_render) - neither reads
img.size and compares to RENDER_WIDTH_MAX/RENDER_HEIGHT_MAX. Not exploitable today:
Pillow's own un-disabled default Image.MAX_IMAGE_PIXELS (~89 megapixels) makes
Image.open(...).load() raise DecompressionBombError on a grossly oversized image, and
check_render's broad except Exception turns that into an honest mismatch, not a crash -
grepped the whole services/api/app tree, MAX_IMAGE_PIXELS is never set/disabled. Chains
with the sha256-pin gap above: if a driver is ever tampered with (finding above), an
oversized-but-clever render is caught by Pillow's OWN guard, not this codebase's. **Fix:**
add an explicit img.size check in check_render for defense-in-depth and a clearer
Mismatch reason than "PIL could not open it".

**Verified sound, with the live/read checks performed:**
- ScenePlan (spec.py): genuinely closed vocabulary/alphabet - _SLUG_RE for
  project/scene (no path, drive, .. , UNC ever matches), _NAME_RE for every object name
  (ASCII identifier only), bounded numbers (_bounded on location/rotation/scale/color/
  energy/render dims), MAX_OPERATIONS=64, attach_script restricted to
  SCRIPT_CATALOGUE and Unity-only. label is the one free-text field, control-character
  filtered (_no_control_characters, blocks NUL/U+2028/U+2029/C0-C1), never spliced into
  any driver/filename/manifest - only carried into DB/ledger/receipt text (and not even
  exposed by routes.py's _row_dict). Live-verified against
  docs/evidence/m25-blender-lab-2026-09-08.json's own refusals (SQL-injection-shaped
  name, an out-of-vocabulary op, a path outside the fixture root - all refused with the
  exact validator messages spec.py produces).
- SceneInspection.cs (device read-back): every bound checked BEFORE the bytes are read
  (info.Length checked before File.ReadAllText/ReadAllBytes, both for out.json
  256 KiB and the render 512 KiB); the render's sha256 is recomputed and compared to what
  the DRIVER declared (render_sha256_mismatch on any divergence); every path
  (Confine) is normalised then resolved-then-contained via AuthorisedRoots.ResolveFinal/
  IsWithin - the same non-lexical pattern already verified sound for M20/M22/M23
  ([[junction-escape-recurring-pattern]]), not the M3/M8/M19-class StartsWith bug.
- ProjectManifest.cs's new blender/unity argv shapes: matched structurally, token count
  and token-for-token equality (never a string prefix), composition/quote characters
  refused before tokenising, every path token required relative-and-inside-project with
  the right extension (InsideProject), -executeMethod restricted to
  PagentOS.SceneDriver.Run only, the 3D runtimes refused outside ProjectScope.ThreeD.
  SceneAllowlistTests.cs already covers the owner's real Unity project path
  (E:/hologram/HologramVehicleTest) as -projectPath - refused because it isn't the
  <root> placeholder token, structurally, not by a name-blocklist.
- Tool detection (SceneTools.cs): install-location/registry lookup only, PATH never
  searched - a planted blender.exe earlier on PATH cannot be what "blender" resolves to.
  ProjectRunner.ResolveRuntime calls SceneTools.Require for both 3D runtimes,
  confirmed by reading (not FindOnPath, which is only used for python/node/npm).
- Job Object / environment: unchanged from M23 ([[m23-app-factory-review]]) -
  KILL_ON_JOB_CLOSE, no breakaway, DIE_ON_UNHANDLED_EXCEPTION, UI_RESTRICTIONS_ALL,
  bounds read back from the kernel (ReadLimits), UseShellExecute=false +
  ArgumentList (never a shell), Scrub removes every PAGENTOS_*/credential-shaped
  variable and runs unconditionally in BuildStartInfo for every runtime including
  blender/unity. RunCommandTimeoutCap (Device Service ceiling) correctly computed as
  UnityRunLimit + 30s; a web run still only waits its own 20s PortWait (ceiling, not a
  wait) - confirmed in InteractiveCapabilityExecutor.TimeoutCapFor.
- Unity honesty: RequireLicence (ProjectRunner.cs) turns exit 198 or the licensing
  client's own marker line into dependency_unavailable/unity_licence, never
  device_error; SceneService._scaffold_and_run maps this to
  STATE_DEPENDENCY_UNAVAILABLE (distinct from STATE_APPLIED/STATE_RENDERED) and a
  speech naming "Unity lisansi yok" with the run's own message - never claims a scene was
  built. apply() refuses further work on a STATE_DEPENDENCY_UNAVAILABLE row.
- REST routes (routes.py): owner-gated at router level
  (dependencies=[Depends(require_owner_session)]), covered in
  test_identity_enforcement.py (scenes/scenes-render/scenes-inspect cases); no
  scene.create REST route exists at all (creation only via voice/tools_scene.py); a
  refused receipt answers 422/404, never 200.
- Web (scenes.ts): every field is a published fact or explicit null (no filled-in
  guesses); renders only typed tokens/numbers into fixed Turkish sentence templates -
  no raw HTML/JS ever constructed from event data; the render image is fetched through
  the owner-gated GET /v1/scenes/{id}/render route and displayed as an <img>, never
  interpreted.
- Migration 20260908_0032_scenes.py: purely additive (one new table + indexes),
  portable JSON/JSONB types, reversible downgrade.
- Corpus (corpus.py::_scene_negative_cases): "Sahneyi sil."/"Projeyi sil." reach no
  tool (closed vocabulary names no delete anywhere - structurally true, not just
  asserted); "Sahneyi disa aktar." (out-of-vocabulary) is a plain miss; "Kupu sil." (a
  bare object-deletion attempt) also reaches nothing; the M18.2 technical-explain
  regression case is repeated here. No literal corpus case names the owner's real
  project by string, but this is unreachable by construction (_SLUG_RE can never
  produce a path/drive/UNC), not merely by a missing test.

**Residual risk / priority note for whoever picks this up:** the High "manifest never
built" finding means the write path fails safe TODAY (permission_denied on every real
attempt) - so the receipt-honesty and driver-pin findings are currently latent, not
exploitable through the real device. But they are landmines: fixing the argv-assembly bug
without ALSO fixing _finish's hardcoded TERMINAL_VERIFIED will make the milestone's
central honesty promise false the moment the feature starts working. Recommend fixing
both in the same change, with a test that forces a compare_ok=False path through
service.create/apply end to end (the PoC above is a ready-made regression case).