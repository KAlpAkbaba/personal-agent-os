# speaker-engine-integration-plan — local speaker embedding + diarisation, integration plan (cycle d20261006)

Proposal: `team/proposals/2026-10-06-konusmaci-sesi-motoru-olcum.md`. Scope: **measurement only**. The worker of
`speaker-engine-measure` builds from this file verbatim, in the FreyaTTS container pattern (`tools/tts-measure/`,
`scripts/voice/tts-measure.ps1`, `team/plans/tts-freya-integration-plan.md`). Everything below was read on
**2026-10-06** from the sources named next to each claim. Every model file was downloaded and hashed outside the
repository (`E:\AI\_scratch_speaker_plan`, deleted afterwards). The hash lock was installed with
`--require-hashes` into the pinned base image, and the image was **built once and smoke-run with `docker run
--network none --read-only --user 10001 --cpus 4 --memory 2g`** (§3). That run checked the API names, the embedding
dimensions and one diarisation of sherpa-onnx's own public English test file. No owner audio was used and no
timing was recorded. Timing is the measure card's work.

**FACT FIRST: on `main` (`9b302f107f56ff873610eafefd8c9a2bca66e22c`) there is NO sherpa-onnx anywhere.** There is no
`services/api/app/voice/providers_sherpa.py` and no line in `docs/THIRD_PARTY_COMPONENTS.md`. `git grep -il sherpa
main` finds only two researcher reports (`team/reports/d20261001/cycle-researcher-1.{json,md}`). The proposal's
"zaten sabitli" refers to `team/plans/local-tr-stt-measure-integration.md`. That plan pinned sherpa-onnx 1.13.8
for a **Windows venv** (win_amd64 wheels) and lives only on the unmerged branch `team/d20261004/worker-local-tr-stt-measure`.
This plan pins sherpa-onnx for a **Linux x86-64 container** and does **not** depend on that card. §8 is therefore a
NEW THIRD_PARTY line.

**STOP lines for the lead are in §1.4.** There is one gate STOP: the upstream pyannote segmentation model is gated
on Hugging Face. The file we fetch is an ungated MIT redistribution, and the lead decides whether that is acceptable.
There is also one training-data STOP-CHECK. No piece has a non-permissive licence. Every licence could be read.

## Şu an üzerinde çalışılan (for the lead's HANDOFF)

- Task: `speaker-engine-integration-plan` — speaker embedding + diarisation engine integration plan (licence, pins,
  container runtime, KVKK), before measurement
- Area: `team/plans/speaker-engine-integration-plan.md` (only this file)
- Machine: home PC (build PC), worktree `.claude/worktrees/team/d20261006/worker-speaker-engine-integration-plan`

## 1. Licences

### 1.1 The pieces

| Piece | Licence | Read from (the actual file / page) | Verdict |
|---|---|---|---|
| sherpa-onnx code + wheels, tag `v1.13.8` @ `11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf` | **Apache-2.0**. The LICENSE at the tag opens "Apache License Version 2.0, January 2004" (sha256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`, the standard text). PyPI: `sherpa-onnx` "Apache licensed, as found in the LICENSE file"; `sherpa-onnx-core` `license_expression: Apache-2.0`. | https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/LICENSE ; https://pypi.org/pypi/sherpa-onnx/1.13.8/json ; https://pypi.org/pypi/sherpa-onnx-core/1.13.8/json | OK |
| ONNX Runtime 1.28.2, bundled inside `sherpa-onnx-core` (`sherpa_onnx/lib/libonnxruntime.so`; version read with `OrtGetApiBase()->GetVersionString()` in the built image) | MIT (Microsoft ONNX Runtime) | https://github.com/microsoft/onnxruntime/blob/main/LICENSE | OK |
| numpy 2.5.3 | `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` (PyPI `license_expression`) | https://pypi.org/pypi/numpy/2.5.3/json | OK |
| **The release page that ships all three embedding ONNX files**, `speaker-recongition-models` (sic) | No licence of its own. The body reads, verbatim: "This release contains speaker recognition models for sherpa-onnx. Each model has its own license. Please see the corresponding repository for the specific license of a given model." Each ONNX file's own metadata names its origin (`framework=3d-speaker`, `url=https://www.modelscope.cn/models/iic/<name>/summary`, read from the ONNX `metadata_props`). The conversion script is `scripts/3dspeaker/export-onnx.py` in the Apache-2.0 sherpa-onnx repo. | https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models ; https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/scripts/3dspeaker/export-onnx.py | follows the originals ↓ |
| **E1 — CAM++ zh+en "advanced"**: `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx`. Original: ModelScope `iic/speech_campplus_sv_zh_en_16k-common_advanced`, weights `campplus_cn_en_common.pt` @ file revision `73001f7ab0bbf7a18739f6e0a48bc1bc74f7a271` (sha256 `92f29b94e6948786a26778c9e302525d185bb08c8b9f5252ed98776902840199`) | **Apache-2.0**. The README front matter says `license: Apache License 2.0` (README sha256 `6bf5ea0b…a432fc`, revision `6d377c98274c7d76fba299541fda623f837731ea`). The ModelScope API field `"License": "Apache License 2.0"` agrees. **The model repo has no LICENSE file** (the full file list via the API is `.gitattributes, campplus_cn_en_common.pt, config.yaml, configuration.json, ding.png, quickstart.md, README.md, examples/*, structure.png`), so the front matter and the API field are the licence statement. | https://modelscope.cn/models/iic/speech_campplus_sv_zh_en_16k-common_advanced ; https://modelscope.cn/api/v1/models/iic/speech_campplus_sv_zh_en_16k-common_advanced ; https://modelscope.cn/api/v1/models/iic/speech_campplus_sv_zh_en_16k-common_advanced/repo?Revision=master&FilePath=README.md | OK |
| **E2 — ERes2NetV2 zh-cn**: `3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx`. Original: ModelScope `iic/speech_eres2netv2_sv_zh-cn_16k-common`, weights `pretrained_eres2netv2.ckpt` @ file revision `1cf80d41fb3435bd3d8df185b5c423333b2db42a` (sha256 `0eb4057106b2573dd7b132cf0c36273ab29afd192c1610f80baa9c556dbb963c`) | **Apache-2.0**: README front matter `license: Apache License 2.0` (README sha256 `4f8f2ccb…70e4e9`, revision `3317286545c587ae682dbc166831d9448780eebb`) and API `"License": "Apache License 2.0"`. No LICENSE file in the model repo (same reading as E1). | https://modelscope.cn/models/iic/speech_eres2netv2_sv_zh-cn_16k-common ; https://modelscope.cn/api/v1/models/iic/speech_eres2netv2_sv_zh-cn_16k-common | OK |
| **E3 — CAM++ VoxCeleb (optional third row)**: `3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx`. Original: ModelScope `iic/speech_campplus_sv_en_voxceleb_16k`, weights `campplus_voxceleb.bin` @ file revision `032b8131a7ad812f87061955ca974c99060c5a03` (sha256 `5b1a88b6f8d85826fabef804779c3372b42f3af21457fa48bd5c097c0686b2de`) | **Apache-2.0**: README front matter `license: Apache License 2.0` (README sha256 `5219533…3131f7`) and API `"License": "Apache License 2.0"`. No LICENSE file. | https://modelscope.cn/models/iic/speech_campplus_sv_en_voxceleb_16k ; https://modelscope.cn/api/v1/models/iic/speech_campplus_sv_en_voxceleb_16k | OK |
| 3D-Speaker training/inference code (origin of E1-E3; **not** installed, only provenance) `modelscope/3D-Speaker` @ `065629c313eaf1a01c65c640c46d77e61e9607b4` (main HEAD, last push 2025-12-08; formerly `alibaba-damo-academy/3D-Speaker`) | **Apache-2.0**: LICENSE "Apache License Version 2.0, January 2004"; GitHub API `license.spdx_id: Apache-2.0` | https://github.com/modelscope/3D-Speaker/blob/065629c313eaf1a01c65c640c46d77e61e9607b4/LICENSE | OK |
| **S — segmenter** `sherpa-onnx-pyannote-segmentation-3-0/model.onnx`, the ONNX export of `pyannote/segmentation-3.0` | **MIT**: the LICENSE inside the archive reads "MIT License / Copyright (c) 2022 CNRS" (sha256 `14d7016ad68e7394d6e6b78d96cc2ae431c905287b89674cfdf021e79e62b8ba`). The archive README: "Models in this file are converted from https://huggingface.co/pyannote/segmentation-3.0/tree/main". The ONNX metadata has `model_author=pyannote`, `maintainer=k2-fsa`, and a `license=` URL pointing to the upstream LICENSE. Upstream card metadata (public HF API `cardData`): `license: mit`. Upstream gate text: "Though this model uses MIT license and will always remain open-source …". | https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models ; https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/blob/9403a6902bb58e3d5ae8c7e77c3422de279db2e0/LICENSE ; https://huggingface.co/api/models/pyannote/segmentation-3.0 ; https://huggingface.co/pyannote/segmentation-3.0 | OK (licence) — **gate: STOP-1** |

**Gated or not (the segmenter).** Upstream `pyannote/segmentation-3.0` @ `e66f3d3b9eb0873085418a7b813d3b369bf160bb`
is **gated**: the HF API reports `"gated": "auto"` (automatic approval after the form). The page says "You need to
agree to share your contact information to access this model". The form asks for `Company/university` and
`Website`. Even its `README.md` and `LICENSE` return "Access to model pyannote/segmentation-3.0 is restricted. You
must have access to it and be authenticated to access it. Please log in." when fetched without an account (tried
2026-10-06). The **ONNX redistribution we would fetch is NOT gated**. Its HF mirror is
`csukuangfj/sherpa-onnx-pyannote-segmentation-3-0` @ `9403a6902bb58e3d5ae8c7e77c3422de279db2e0`, HF API
`"gated": false`, maintained by the sherpa-onnx author. The same bytes are in the GitHub release asset. Neither
needs a token or an account. → **STOP-1**.

### 1.2 Training data, as each card states it

- **E1 CAM++ advanced**: "本模型使用大规模中文和英文说话人数据集进行训练" ("trained on large-scale Chinese and
  English speaker datasets"). The card names **no** dataset. Its eval table is CN-Celeb Test EER 5.98 % and
  Voxceleb-O EER 1.16 %. The 3D-Speaker README says the zh-cn "common" models were "trained on a Mandarin dataset of
  200k labeled speakers". That set is **not published** and has no licence anywhere.
- **E2 ERes2NetV2 zh-cn**: "本模型使用大型中文说话人数据集进行训练，包含约200k个说话人" (a large Chinese speaker
  set, about 200k speakers). It is the same unpublished set, and its licence cannot be read.
- **E3 CAM++ VoxCeleb**: "本模型使用公开的英文说话人数据集VoxCeleb2进行训练，包含5994个说话人" (VoxCeleb2 dev,
  5 994 speakers).
- **Datasets that can be read**:
  - **VoxCeleb**, as stated by Oxford VGG: "The provided VoxCeleb2 metadata is licensed under a Creative Commons
    Attribution-ShareAlike 4.0 International License". KAIST, the current host, says: "available to download for
    **research purposes** under a Creative Commons Attribution 4.0 International License. The copyright remains
    with the original owners of the video." Sources: https://www.robots.ox.ac.uk/~vgg/data/voxceleb/vox2.html ,
    https://mm.kaist.ac.kr/datasets/voxceleb/
  - **CN-Celeb**: "License: Attribution-ShareAlike 4.0 International" (https://www.openslr.org/82/).
  - **3D-Speaker dataset**: "metadata is available to download under a Creative Commons Attribution-ShareAlike 4.0
    International License (CC BY-SA 4.0)" (https://3dspeaker.github.io/).
- **S segmenter**: "trained by Séverin Baroudi with pyannote.audio 3.0.0 using the combination of the training sets
  of AISHELL, AliMeeting, AMI, AVA-AVD, DIHARD, Ego4D, MSDWild, REPERE, and VoxConverse" (public card text,
  https://huggingface.co/pyannote/segmentation-3.0). I did **not** read each of the nine dataset licences.
  DIHARD (LDC), REPERE (ELRA/ELDA) and Ego4D are known to be distributed under research or licence-agreement terms,
  not open licences. Treat them as **research-only** until read.
- **Non-commercial or research-only inside the training data: yes.** VoxCeleb is "for research purposes" and
  several of the segmenter's sets are research-only. The 200k-speaker set behind E1/E2 is unreadable. The weights
  themselves are Apache-2.0 / MIT → **STOP-CHECK-2**.

### 1.3 What the licences ask of us

- Apache-2.0 (sherpa-onnx, E1-E3, 3D-Speaker): keep the licence and NOTICE when redistributing. We do not
  redistribute: the files stay in a private named volume.
- MIT (segmenter, ONNX Runtime): keep the copyright notice. The LICENSE is fetched next to the model (§2.2) and
  kept in the volume.
- No attribution is triggered by private use. §8 gives the credit anyway.

### 1.4 STOP lines for the lead

- **STOP-1 (gate): upstream `pyannote/segmentation-3.0` is gated (`gated: "auto"`, HF account + contact-info form).**
  The card's rule makes a model that needs an HF token or an account a STOP. The file this plan pins does **not**
  need one: it is k2-fsa's ONNX export, published ungated on GitHub and on HF, with the upstream MIT LICENSE
  included. MIT expressly permits redistribution. The gate is a contact-collection form, not a licence term (the
  gate text itself: "this model uses MIT license and will always remain open-source"). My reading: fetching the
  redistribution is lawful and is **not** a token workaround. Still, it routes around the authors' wish to know
  their users, so the lead must accept it explicitly. **If the lead does not accept it**, the measure card drops
  diarisation (no DER row) and measures only the embedding models (E1-E3: same-speaker/different-speaker scores
  and EER). The rest of this plan is unchanged.
- **STOP-CHECK-2 (training data, not licence class).** The weights are Apache-2.0 (E1-E3) and MIT (S). Their
  training data includes research-only sets (VoxCeleb "for research purposes"; DIHARD / REPERE / Ego4D in the
  segmenter), and E1/E2 were trained on an unpublished 200k-speaker Mandarin set whose terms cannot be read. For a
  private, single-owner measurement this is the same situation the lead already accepted for the CC BY-NC
  Zipformer (`team/plans/local-tr-stt-measure-integration.md`, "Lisans" section). Any **adoption** card must re-read
  this before the model ships in a product.
- No STOP for: sherpa-onnx, sherpa-onnx-core, ONNX Runtime, numpy (Apache-2.0 / MIT / BSD). All could be read, all
  install from hashes (§3).
- **NOT candidates of this measurement** (PyTorch, gated or account-bound), listed only in §7:
  `pyannote/speaker-diarization-community-1` and NVIDIA Sortformer. The proposal's "optional third row" with
  community-1 is **out** under this card's rule (gated + HF token).

## 2. The exact pin

### 2.1 sherpa-onnx

- Repository https://github.com/k2-fsa/sherpa-onnx, tag **`v1.13.8`** → commit
  **`11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf`** (GitHub API `git/ref/tags/v1.13.8`, lightweight tag, object type
  `commit`). It is the newest release ("latest" = `v1.13.8`, published 2026-09-10T14:00:23Z).
- The PyPI wheels are 1.13.8 (§3). Code at the tag is **not** cloned or installed. The two upstream example files
  this plan quotes were read at the tag: `python-api-examples/offline-speaker-diarization.py` (136 lines) and
  `python-api-examples/speaker-identification.py` (260 lines).

### 2.2 Model files — every file the measurement loads

Release assets are **mutable** (GitHub lets the uploader replace an asset under the same name; the assets'
`digest` field is `null` for all ONNX files). **The sha256 below is therefore the pin**, not the URL. All
hashes were computed with `sha256sum` from the downloaded bytes on 2026-10-06. The three embedding hashes also
equal the release's own `checksum.txt` (asset sha256 `c3a928cc16d165ce869553f71fcbe6fc1c1b513aee0665414548780a979b2325`).

| Id | File | Exact download URL | Bytes | sha256 (downloaded bytes) | Embedding dim / rate |
|---|---|---|---:|---|---|
| E1 | `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx` | https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx | 28 281 164 | `aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2` | **192** (`extractor.dim`, and `len(compute())` = 192 in the smoke run); 16 kHz (`sample_rate=16000`, `normalize_samples=1` in ONNX metadata) |
| E2 | `3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx` | https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx | 71 441 526 | `bf1a75b9930474cf3389ef415e6e5d38ca96fea4a3a00f7e301d080a58ee2239` | **192**; 16 kHz |
| E3 (optional) | `3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx` | https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx | 29 596 978 | `357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b` | **512**; 16 kHz |
| S | `model.onnx` (fp32 pyannote segmentation-3.0) | https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/9403a6902bb58e3d5ae8c7e77c3422de279db2e0/model.onnx | 5 992 913 | `220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079` (downloaded via this URL and hashed; equals the HF LFS record and the copy inside the GitHub tarball) | — ; ONNX metadata: `sample_rate=16000`, `window_size=160000` (10 s), `receptive_field_shift=270` samples (**frame hop 16.875 ms**), `receptive_field_size=991` (61.94 ms), `num_speakers=3`, `powerset_max_classes=2`, `num_classes=7`, `model_type=pyannote-segmentation-3.0` |
| S-lic | `LICENSE` (kept beside S; never loaded) | https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/9403a6902bb58e3d5ae8c7e77c3422de279db2e0/LICENSE | 1 061 | `14d7016ad68e7394d6e6b78d96cc2ae431c905287b89674cfdf021e79e62b8ba` (hashed from the GitHub tarball copy; HF reports the same size 1 061) | — |

Total fetched: 135.3 MB (E1+E2+E3+S), or 105.7 MB without E3.

- **Not fetched**: `model.int8.onnx` (1 540 506 B, sha256 `d582f4b4c6b48205de7e0643c57df0df5615a3c176189be3fc461e9d18827b5d`).
  It may be a separately labelled later row, but it is not in this card. The upstream PyTorch `pytorch_model.bin` is
  gated and never fetched. The tarball's `*.py` helper scripts are not fetched (they import torch / pyannote).
- **Alternative source for S, same bytes**: the GitHub tarball
  https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2
  (6 958 444 B, sha256 `24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488`). Prefer the HF URL above:
  it is addressed by a 40-hex revision, so it is immutable, and it needs no `tar`.
- **Where the dims came from**: `SpeakerEmbeddingExtractor(...).dim` and `len(extractor.compute(stream))` in the
  smoke run (§3). The three ONNX `metadata_props` carry `framework=3d-speaker`, `sample_rate=16000`,
  `normalize_samples=1` and the ModelScope `url`. They do not carry the dimension.

### 2.3 How the tool is forced onto these files (never "latest")

sherpa-onnx itself **never downloads anything**. Every config takes a local file path:
`SpeakerEmbeddingExtractorConfig(model="<path>")` and
`OfflineSpeakerSegmentationPyannoteModelConfig(model="<path>")`. There is no hub, cache or revision logic in the
Python API. So the pin is enforced entirely by our fill step:

1. **Fill step (network allowed, separate run, the only networked run):** fetch the five files of §2.2 by the exact
   URLs above into the named volume:
   `/models/3dspeaker/{E1,E2,E3}.onnx`, `/models/pyannote-seg-3-0/model.onnx`, `/models/pyannote-seg-3-0/LICENSE`.
   Write each to `<name>.part`, `sha256sum` it against §2.2, then rename. **A mismatch deletes the `.part` and exits
   3**, the same rule as `scripts/voice/tts-measure.ps1:177-179` (a failed fill is `Stop-Measure 3`). No `latest`, no `master`, no redirect-following to a
   different name: `curl -fL` to the exact URL, then the hash decides.
2. **Measure step:** the script holds the §2.2 table as a constant (`MODEL_FILES = {relative_path: (bytes, sha256)}`).
   **Before the first `SpeakerEmbeddingExtractor(...)` / `OfflineSpeakerDiarization(...)` is constructed**, it
   re-hashes every file it will load (135 MB, about 0.5 s) and exits 3 on any difference. Then `config.validate()`
   must be true (it checks that the paths exist), else exit 3.
3. A test reads the constant and asserts 64-hex hashes, positive sizes and the four file names, so a
   placeholder can never pass.

## 3. Runtime

- **sherpa-onnx 1.13.8 is enough.** Its Python API exposes everything the measurement needs. Proven by
  `dir(sherpa_onnx)` in the built image: `OfflineSpeakerDiarization`, `OfflineSpeakerDiarizationConfig`,
  `OfflineSpeakerDiarizationResult`, `OfflineSpeakerDiarizationSegment`, `OfflineSpeakerSegmentationModelConfig`,
  `OfflineSpeakerSegmentationPyannoteModelConfig`, `SpeakerEmbeddingExtractor`, `SpeakerEmbeddingExtractorConfig`,
  `SpeakerEmbeddingManager` (and `FastClusteringConfig`). No newer tag exists; 1.13.8 is the latest.
- **Python 3.12.15**, base image **the FreyaTTS base, reused**:
  `python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3`
  (`tools/tts-measure/Dockerfile:3`). Nothing forbids it: sherpa-onnx `requires_python >=3.7`, numpy `>=3.12`.
- **No torch, no soundfile, no librosa.** The `/voice/measure` recordings are "WAV (PCM 16-bit, mono, 16 kHz, at
  most 30 s)" (`services/api/app/voice/measurement/service.py:4`, `SAMPLE_RATE = 16_000` at `:49`, 16-bit and mono
  enforced at `:201-204`). Stdlib `wave` reads them. The smoke run read a 16 kHz PCM16 mono WAV with `wave` and
  `np.frombuffer(..., "<i2") / 32768.0`, so no extra reader is needed. Resampling is **not** written: a WAV that is
  not 16 kHz / mono / 16-bit is refused (exit 2, the reason named), never converted.
- **sherpa-onnx declares one dependency** (`requires_dist: ['sherpa-onnx-core==1.13.8']`). It does not declare
  numpy, but `accept_waveform` / `process` take numpy float32 arrays, so numpy is pinned explicitly. numpy 2.5.3 is
  the version already in `tools/tts-measure/requirements.txt`.

Requirements body (`tools/speaker-measure/requirements.txt`, 3 lines, every line `==` + `--hash`). These are the
three manylinux x86-64 wheels for cp312, sha256 from PyPI JSON, and they installed with `--require-hashes` in the
pinned base (build log below):

```text
numpy==2.5.3 --hash=sha256:b7e18c623bb5c95acb3b3328861272816ba199fb531921c5d6d0b675f1fde9e3
sherpa-onnx==1.13.8 --hash=sha256:6949773017647febc0c3696dffb2c67dd3febd4737b87e3dd135a42773704a06
sherpa-onnx-core==1.13.8 --hash=sha256:4da90acf435373d7b2ba9cc0be806e7e274d28f63f5780880b5dea6721626e36
```

| Wheel (the file pip picked) | Bytes | sha256 |
|---|---:|---|
| `numpy-2.5.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl` | 16 717 410 | `b7e18c623bb5c95acb3b3328861272816ba199fb531921c5d6d0b675f1fde9e3` |
| `sherpa_onnx-1.13.8-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl` | 4 400 575 | `6949773017647febc0c3696dffb2c67dd3febd4737b87e3dd135a42773704a06` |
| `sherpa_onnx_core-1.13.8-py3-none-manylinux2014_x86_64.whl` (bundles `libonnxruntime.so` 1.28.2, `libsherpa-onnx-c-api.so`, `libsherpa-onnx-cxx-api.so`) | 10 642 497 | `4da90acf435373d7b2ba9cc0be806e7e274d28f63f5780880b5dea6721626e36` |

Sources: https://pypi.org/pypi/sherpa-onnx/1.13.8/json , https://pypi.org/pypi/sherpa-onnx-core/1.13.8/json ,
https://pypi.org/pypi/numpy/2.5.3/json .

Dockerfile shape (the measure card writes it; this is the shape that was built):

```dockerfile
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-deps --require-hashes --only-binary=:all: -r /tmp/requirements.txt && rm /tmp/requirements.txt
RUN useradd --uid 10001 --no-create-home spk && mkdir /models /out && chown 10001 /models /out
USER spk
# + COPY speaker_compare.py; RUN python speaker_compare.py selfcheck; ENTRYPOINT (measure card)
```

The scratch build was made **outside the repository**, last lines:
```
#7 8.697 Successfully installed numpy-2.5.3 sherpa-onnx-1.13.8 sherpa-onnx-core-1.13.8
#9 0.442 3.12.15 (main, Oct  1 2026, 21:50:06) [GCC 12.2.0]
#9 0.442 ['OfflineSpeakerDiarization', 'OfflineSpeakerDiarizationConfig', 'OfflineSpeakerDiarizationResult', 'OfflineSpeakerDiarizationSegment', 'OfflineSpeakerSegmentationModelConfig', 'OfflineSpeakerSegmentationPyannoteModelConfig', 'SpeakerEmbeddingExtractor', 'SpeakerEmbeddingExtractorConfig', 'SpeakerEmbeddingManager']
#10 naming to docker.io/library/pagentos-speaker-plan-smoke:tmp done
```
Image `sha256:6e886f69fd38bcb1126de39bce088c4de630e47327e364135b85ce3a0193ff94`, 81.9 MB. It is a throwaway
tag, removed afterwards, and the measure card's own image will have a different digest.

Smoke run, `docker run --rm --network none --read-only --tmpfs /tmp --memory 2g --memory-swap 2g --cpus 4 --user 10001`,
models mounted `:ro`, input = sherpa-onnx's public test file `1-two-speakers-en.wav` (16.0 s, sha256 `f1c877dc…21f3`):
```
emb 3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx dim 192 192
emb 3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx dim 192 192
emb 3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx dim 512 512
diar sample_rate 16000
segments 4 speakers 2 first [(1.58, 3.41, 0), (4.4, 6.46, 0), (9.35, 11.47, 1)]
```
That proves the models load and run with no network, read-only, as uid 10001, under 2 GB. The 2 GB is the ceiling
the run fitted under, **not** a measured peak.

## 4. How the models are called (sherpa-onnx 1.13.8)

Documentation: https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html ,
https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html ,
https://k2-fsa.github.io/sherpa/onnx/speaker-identification/index.html . The examples at the pinned commit:
https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/python-api-examples/offline-speaker-diarization.py
(lines 73-93, 126-131) and
https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/python-api-examples/speaker-identification.py
(lines 115-123, 158-173). Field names below were read from `dir()` of each class in the built image:
- `SpeakerEmbeddingExtractorConfig`: `model, num_threads, debug, provider, validate`
- `OfflineSpeakerSegmentationPyannoteModelConfig`: `model, window_shift_ratio`
- `OfflineSpeakerSegmentationModelConfig`: `pyannote, num_threads, debug, provider`
- `FastClusteringConfig`: `num_clusters, threshold, compute_confidence`
- `OfflineSpeakerDiarizationConfig`: `segmentation, embedding, clustering, min_duration_on, min_duration_off`
- `OfflineSpeakerDiarization`: `process, sample_rate, set_config`
- `OfflineSpeakerDiarizationSegment`: `start, end, speaker, duration, confidence, text`
- `SpeakerEmbeddingExtractor`: `create_stream, compute, dim, is_ready`

**Reading audio (both paths):**
```python
with wave.open(path, "rb") as w:   # refuse unless nchannels==1, sampwidth==2, framerate==16000
    x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0   # [-1, 1)
```

**Embedding (E1 / E2 / E3), one vector per recording:**
```python
cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=path, num_threads=threads, debug=False, provider="cpu")
assert cfg.validate()
ext = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)          # once per model, reused for every file
s = ext.create_stream()
s.accept_waveform(sample_rate=16000, waveform=x)
s.input_finished()
assert ext.is_ready(s)
v = np.asarray(ext.compute(s), dtype=np.float64)          # python list[float] -> len == ext.dim
```
`compute()` returns a Python `list[float]`. Convert with an explicit dtype (upstream issue #2212 is about the
example's implicit float64).

**Score: the same arithmetic as the product.** `services/api/app/voice/speaker.py:99-111` `cosine_similarity` is
`dot / (sqrt(Σa²) · sqrt(Σb²))`, 0.0 if either norm is 0, clamped to [-1, 1]. Profiles are built by
`l2_normalize` (`speaker.py:92-96`), and the bands are `owner_accept: float = 0.75` / `not_owner_max: float = 0.45`
(`speaker.py:49-50`). The measure script **copies this formula verbatim**. It does not import `app` (the image has
no API code). So its scores mean the same thing against the 0.75 / 0.45 bands. One test reads `speaker.py`'s source
and asserts that the copied function's body matches, so the two halves cannot drift (the contract-halves rule).
Report per model:
- the owner-vs-owner score distribution (enrol = mean of L2-normalised vectors, as `enroll_owner` does at
  `speaker.py:145-165`);
- owner-vs-konuk scores;
- the share of each above 0.75 and below 0.45;
- EER.
EER with one non-owner speaker is a weak number; the report says so next to it.

**Diarisation (S + one embedding model per row):**
```python
cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
    segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
        pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=seg_path),  # window_shift_ratio default
        num_threads=threads, debug=False, provider="cpu"),
    embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=emb_path, num_threads=threads, provider="cpu"),
    clustering=sherpa_onnx.FastClusteringConfig(num_clusters=2, threshold=0.5),   # two rows: num_clusters=2 (known) and num_clusters=-1 with threshold
    min_duration_on=0.3, min_duration_off=0.5)   # upstream example values
assert cfg.validate()
sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
assert sd.sample_rate == 16000
segments = sd.process(x).sort_by_start_time()   # each: .start, .end (seconds, float), .speaker (int index from 0)
```
- **Output**: a list of segments `(start s, end s, speaker index)`. Indices are arbitrary labels, so the DER
  scorer maps them to the reference by the best assignment.
- **Threads**: `num_threads` on both configs = the `-Threads` argument (4 for `cpx32-bicimi`).
- **Provider**: `"cpu"` only. Never `cuda`.
- Upstream issue #1466 reports a "strong decrease in accuracy … when cluster numbers were not specified". So the
  known-count row (`num_clusters=2`) is the main one, and the threshold row (`-1`, 0.5) is labelled as a second row.
- **DER needs a reference.** A real two-person conversation has no ground truth unless someone marks who spoke when,
  and nobody will. So the scored DER uses a **constructed** two-speaker file: owner clips and konuk clips
  concatenated in a fixed order, with known boundaries and no overlap (say so). The real conversation reports only
  the number of speakers found and the talk-time split, labelled "referanssız".

## 5. Device safety and security

- **Own container, own image** `tools/speaker-measure/` (`pagentos-speaker-measure`), copied from
  `scripts/voice/tts-measure.ps1`. The API image **never imports `sherpa_onnx`**: `services/api` dependencies are
  unchanged (CLAUDE.md voice rule: speaker verification is its own subsystem).
- **Fill**: the only networked run. It writes into a named volume (`pagentos-speaker-models`) by the fixed URLs of
  §2.2 and checks sha256. Wrong hash → exit 3.
- **Every measurement run**: `--network none --read-only --tmpfs /tmp --user 10001 --memory 4g --memory-swap 4g`
  (no swap). The smoke run fitted in 2 GB, and 4 GB leaves room for a 3-minute recording. The measure card records
  the real peak and does not lower the ceiling on a guess. Also `-e OMP_NUM_THREADS=$Threads`, plus
  `--cpus 4 -Threads 4` for the **`cpx32-bicimi`** row (the VEKİL label, `docs/DECISIONS.md:25526`). Mounts: model
  volume `:ro`; the recordings folder `:ro`; `/out` read-write **for the raw JSON lines only**.
- **Host allow-list before docker is touched**: `[string[]]$AllowedHosts = @("MAIL", "pagentos-core")`, else exit
  4 "never run this on the office PC" (`tts-measure.ps1:45,127-128`). **The office PC never runs it** (employer
  machine).
- **ONNX files are opaque binaries executed by ONNX Runtime's graph loader.** The sha256 check before the first
  load (§2.3/2) is **the only defence** against a replaced or corrupt asset. Release assets can be replaced (§2.2),
  so the hash, not the URL, is the trust anchor. Upstream issue #3983 (open, "Heap-buffer-overflow (OOB read) …
  when decoder.onnx vocab_size metadata mismatches") shows that a malformed model can cause out-of-bounds reads in
  sherpa-onnx's native code. It is in the ASR decoder, not our path, but it is why an unverified file is never loaded.
  The load runs non-root, read-only, with no network.
- **Open issues read** (search `repo:k2-fsa/sherpa-onnx is:issue is:open diarization` → 24; "speaker embedding /
  identification / verification" → 24; security/CVE → 12 incl. closed; security advisories: 0):
  - **#1466** accuracy drops without `num_clusters` → main row uses `num_clusters=2` (§4).
  - **#1708** ONNX diarisation differs from `pyannote/speaker-diarization-3.0` on the same audio → our numbers are
    sherpa-onnx's, not pyannote's published DER; never compare them as equal.
  - **#2883** NeMo ONNX embedding gives a different cosine than the original model (0.349 vs 0.478) → the same may
    hold for 3D-Speaker exports; the published EERs (§6) are of the PyTorch originals, not of these ONNX files.
  - **#2212 / #2208** example dtype and in-place-sum pitfalls → explicit `dtype=np.float64`, and the enrolment mean
    is built from copies, never `+=` onto the first vector.
  - **#3253** `AccessViolationException` (C# API, diarisation) → not our binding; a crash is exit ≠ 0 and the
    temp folder is removed anyway (below).
  - **#3983** (above), **#3554** (Windows `sherpa-onnx-bin` wheel flagged by AV) and **#4011** (Windows Smart App
    Control) → Windows-only or ASR-only, not our Linux path.
  - Sources: https://github.com/k2-fsa/sherpa-onnx/issues/1466 , …/1708 , …/2883 , …/2212 , …/2208 , …/3253 , …/3983 .

**KVKK / TCK 133: rules the worker enforces in code (each one a test):**
1. **A voice print is biometric data** (KVKK md. 6, özel nitelikli kişisel veri). The tool processes only:
   (a) the owner's own `/voice/measure` recordings, and (b) **one** short recording of **one** consenting second
   person. It refuses (exit 2) a folder holding any other file pattern.
2. **No name of the second person is written anywhere.** The label is the literal `konuk`, in file names, JSON,
   logs and the report. The tool takes no name argument at all.
3. **Embedding vectors live only in process memory and the run's temp folder** (`tempfile.mkdtemp` under the
   container's tmpfs `/tmp`). They are **deleted at the end of the run, success or failure** (`try/finally`
   `shutil.rmtree`). A test makes the run fail mid-way and asserts that the folder is gone.
4. **Nothing is written to any owner or person profile.** The image has no API code, no DB driver and no network.
   The script never calls `enroll_owner` or a profile endpoint. Its only writable mount is `/out`.
5. **The evidence files hold scores, counts and timings only.** Never audio, never a vector (no float array longer
   than the per-model score list), never a transcript. The `/voice/measure` sidecar's transcript fields are not
   read. A test serialises a run's output and asserts that it contains no key named
   `embedding`/`vector`/`audio`/`transcript`/`text` and no list of ≥ 64 floats. File names are reported as
   `owner-NN` / `konuk-1`, never the original names.
6. Recordings are deleted afterwards by the existing 30-day rule and "sil" (`measurement/service.py:44`
   `RETENTION_DAYS = 30`). The konuk recording is deleted by the owner right after the run.
7. Audio and vectors never leave the owner's machines (home PC, Cloud Core). No cloud provider is called.

## 6. Memory and CPU: what is known and what is not

Model file sizes (bytes, from the downloads): E1 28 281 164; E2 71 441 526; E3 29 596 978; S 5 992 913. Wheels:
31.8 MB. Image: 81.9 MB.

Published accuracy (authors' numbers, PyTorch originals, **not** these ONNX files, **not** Turkish):
- 3D-Speaker README @ `065629c3`, "The EER results on VoxCeleb, CNCeleb and 3D-Speaker datasets for
  fully-supervised speaker verification" (lines 23-31): **CAM++ 7.2 M params, VoxCeleb1-O 0.65 %**; **ERes2NetV2
  17.8 M, VoxCeleb1-O 0.61 %**. Verified. **But** those rows are the models **trained on VoxCeleb** (the
  `egs/voxceleb` recipes). They are **not** E1 or E2:
  - E1 (CAM++ zh+en advanced) card: CN-Celeb Test **5.98 %**, Voxceleb-O **1.16 %**;
  - E2 (ERes2NetV2 zh-cn 200k) card: CN-Celeb Test **3.81 %**, no VoxCeleb number;
  - E3 (CAM++ VoxCeleb) card: VoxCeleb1-O **0.73 %**, -E 0.89 %, -H 1.76 %.
  - So the proposal's "ERes2NetV2 %0,61, CAM++ %0,65" describes neither file that sherpa-onnx ships. No
    VoxCeleb-trained ERes2NetV2 ONNX exists in the release.
- Segmenter: no DER is published for the ONNX export. Issue #1708 says that its output differs from pyannote's
  pipeline.
- Sources: https://github.com/modelscope/3D-Speaker/blob/065629c313eaf1a01c65c640c46d77e61e9607b4/README.md and the
  three ModelScope cards of §1.1.

**There is NO Turkish number and NO x86 timing or memory number anywhere.** No same/different-speaker score on
Turkish speech, no EER, no DER, no real-time factor and no peak RSS exist for any of these files on the home PC
(i7-14700KF) or the `cpx32-bicimi` proxy. The smoke run's success under 2 GB is not a measurement. `speaker-engine-measure`
produces all of them. Nothing here estimates them.

## 7. Alternatives seen in passing (no recommendation)

| Option | Licence (as read) | Why not a candidate here | Source |
|---|---|---|---|
| `pyannote/speaker-diarization-community-1` | CC-BY-4.0 | gated download, HF token, PyTorch pipeline | https://huggingface.co/pyannote/speaker-diarization-community-1 |
| NVIDIA Sortformer (streaming diarisation, ≤ 4 speaker slots) | NVIDIA model licence (not read here) | NeMo/PyTorch; sherpa-onnx support is an open feature request (#3497) | https://github.com/k2-fsa/sherpa-onnx/issues/3497 |
| WeSpeaker ResNet34 / CAM++ (in the same sherpa-onnx release) | per model, not read here | not proposed; same runtime, could be a later row | https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models |
| NeMo TitaNet small/large, SpeakerNet (same release) | per model, not read here | not proposed; #2883 reports ONNX/original score drift | same release page |
| `sherpa-onnx-reverb-diarization-v1/v2` (Rev) | "accessible under a non-commercial license" (sherpa docs) | non-commercial | https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html |
| A cloud STT provider's speaker labels | provider terms | sends audio out; excluded by the owner's rule ("ses değil metin saklansın") and KVKK | proposal, Alternatif 3 |

## 8. THIRD_PARTY_COMPONENTS entry (the lead copies this; the worker does not edit the shared file)

```markdown
## sherpa-onnx speaker embedding + diarisation (3D-Speaker CAM++ / ERes2NetV2, pyannote segmentation-3.0 ONNX) — MEASUREMENT ONLY

Role: candidate local speaker-embedding and diarisation engine for "who is speaking" (ROADMAP order 3, v1.0 item
665), MEASURED by `speaker-engine-measure` only; not wired into `app/voice/speaker.py` or conversation transcripts.
NEW line: no sherpa-onnx component existed on main before this.

- Runtime: `sherpa-onnx==1.13.8` + `sherpa-onnx-core==1.13.8` (Apache-2.0; tag v1.13.8 @
  11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf; bundles ONNX Runtime 1.28.2, MIT) and `numpy==2.5.3` (BSD-3-Clause et al.),
  hash-locked manylinux cp312 wheels in `tools/speaker-measure/requirements.txt`; image
  python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 (same base as FreyaTTS).
- Embedding models (sherpa-onnx release `speaker-recongition-models`, sha256-pinned; originals Apache-2.0 per their
  ModelScope cards, iic/...):
  CAM++ zh+en advanced `3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx`
  (aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2, 192-dim);
  ERes2NetV2 zh-cn `3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx`
  (bf1a75b9930474cf3389ef415e6e5d38ca96fea4a3a00f7e301d080a58ee2239, 192-dim);
  optional CAM++ VoxCeleb `3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx`
  (357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b, 512-dim).
- Segmenter: `csukuangfj/sherpa-onnx-pyannote-segmentation-3-0` @ 9403a6902bb58e3d5ae8c7e77c3422de279db2e0
  `model.onnx` (220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079) — MIT (c) 2022 CNRS, ONNX export of
  the gated `pyannote/segmentation-3.0`; ungated redistribution accepted by the lead (STOP-1,
  `team/plans/speaker-engine-integration-plan.md` §1.4).
- Training data (per the cards): VoxCeleb ("for research purposes", CC BY 4.0 / metadata CC BY-SA 4.0), an
  unpublished 200k-speaker Mandarin set (terms unreadable), and for the segmenter AISHELL, AliMeeting, AMI,
  AVA-AVD, DIHARD, Ego4D, MSDWild, REPERE, VoxConverse (several research-only). Acceptable for a private
  single-owner measurement; must be re-read before any adoption (STOP-CHECK-2).
- Where it runs: own container `pagentos-speaker-measure`, home PC and Cloud Core only (`MAIL`, `pagentos-core`
  allow-list), `--network none --read-only`, uid 10001; never the office PC; never in the API image.
- Privacy (KVKK md. 6 / TCK 133): voice prints are biometric. Only the owner's own recordings plus one consenting
  "konuk" recording, no name stored, vectors deleted at run end, nothing written to any profile, evidence holds
  scores/counts/timings only.
```

## Later card (not planned here): adoption

A separate, owner-approved card would decide one embedding model, then run it as a local service (its own container
or a companion-side process, never inside the API image). It would produce the vector that `enroll_owner` /
`verify_speaker` (`services/api/app/voice/speaker.py`) already consume, set `OwnerProfile.model_id` to the pinned
file's name + sha256, and possibly re-tune the 0.75 / 0.45 bands from this measurement's Turkish distribution. It
would also feed the vector end of the `conversation-transcripts` store. It needs the owner's own enrolment, explicit
consent records per named person, and a re-read of STOP-CHECK-2. None of that is in this plan.
