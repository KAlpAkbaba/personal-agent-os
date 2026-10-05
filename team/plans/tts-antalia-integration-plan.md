# tts-antalia-integration-plan — Antalia 1, integration plan (cycle d20261005)

Proposal: `team/proposals/2026-10-05-antalia-turkce-ses-olcum.md`. Scope: **measurement only**. The worker of
`tts-antalia-measure` builds from this file verbatim, in the FreyaTTS pattern (`tools/tts-measure/`, ADR-0296,
`team/plans/tts-freya-integration-plan.md`). Everything below was read on **2026-10-05** from the sources named
next to each claim. The two weight files were downloaded at the pinned revisions and hashed (both equal the HF
LFS record), the hash lock was made from the wheels actually fetched inside the pinned base image, and the image
was **built once and proven with `docker run`** (§3), including one smoke synthesis under `--network none`. No
timing was recorded — that is the measure card's work.

**STOP lines for the lead are in §1.4.** One is a licence-class STOP, which the card's rule requires: the
weights are not under a permissive licence. The others cover the use restrictions that the *later wiring*
could breach. Nothing in the measurement itself breaches them.

## Şu an üzerinde çalışılan (for the lead's HANDOFF)

- Task: `tts-antalia-integration-plan` — Antalia 1 integration plan (licence, pins, CPU runtime, safety)
- Area: `team/plans/tts-antalia-integration-plan.md` (only this file)
- Machine: home PC (build PC), worktree `.claude/worktrees/team/d20261005/worker-tts-antalia-integration-plan`

## 1. Licences

### 1.1 The pieces the model needs

| Piece | Licence | Read from (the actual file) | Verdict |
|---|---|---|---|
| Weights `cloud0day3/antalia-1` @ `eaec2aad2da8c0db5fc359734470874dae82c603` | **Antalia Open RAIL-M**, "dated September 16, 2026" (LICENSE.md lines 3-4): the CreativeML Open RAIL-M template with an adapted preamble and Attachment A, plus an attribution paragraph (lines 6-10). Card front matter `license: openrail`, `license_name: antalia-openrail-m` (README.md lines 4-6); `config.json` `"license": "openrail"` | https://huggingface.co/cloud0day3/antalia-1/blob/eaec2aad2da8c0db5fc359734470874dae82c603/LICENSE.md ; https://huggingface.co/cloud0day3/antalia-1/blob/eaec2aad2da8c0db5fc359734470874dae82c603/README.md | **STOP (licence class), §1.4** |
| Foundation weights `cloud0day3/antalia-1-foundation` @ `302c1046c555b404a5f6cc3b0da39100b6439158` | **The same Antalia Open RAIL-M** (byte-identical LICENSE.md, blob `f462716f…`, sha256 `19253f7e…`; card `license_name: antalia-openrail-m`). **The proposal is wrong here:** this repo holds **no inference code** (files: `.gitattributes`, `LICENSE.md`, `README.md`, `README.tr.md`, `config.json`, `model.safetensors`). Its weights are **not loaded** for synthesis: antalia-1 is the fine-tune and ships its full weights. Pinned for provenance only. | https://huggingface.co/cloud0day3/antalia-1-foundation/blob/302c1046c555b404a5f6cc3b0da39100b6439158/LICENSE.md ; https://huggingface.co/api/models/cloud0day3/antalia-1-foundation | not loaded |
| Code `0daycloud/antalia` @ `20f9bfeaaefefb3ef723c292fe2bb0306e08823d` (the inference code) | **Apache-2.0**, full text (LICENSE line 1-3 "Apache License Version 2.0"); `pyproject.toml` `license = { text = "Apache-2.0" }`; NOTICE: "This product includes code developed under the Apache-2.0 license. The associated model weights are licensed separately under the Antalia Open RAIL-M license". GitHub API `license.spdx_id: Apache-2.0`. | https://github.com/0daycloud/antalia/blob/20f9bfeaaefefb3ef723c292fe2bb0306e08823d/LICENSE ; …/NOTICE ; …/pyproject.toml | OK |
| BigVGAN code `NVIDIA/BigVGAN` @ `7d2b454564a6c7d014227f635b7423881f14bdac` (tag v2.4, also current `main` HEAD) | **MIT**, "Copyright (c) 2024 NVIDIA CORPORATION." (LICENSE) | https://github.com/NVIDIA/BigVGAN/blob/7d2b454564a6c7d014227f635b7423881f14bdac/LICENSE | OK |
| BigVGAN weights `nvidia/bigvgan_v2_24khz_100band_256x` @ `c329ede9e9bbc100ddf5c91e2330a61921262370` | **MIT**, the same NVIDIA MIT text in the HF repo's LICENSE file (sha256 `90459cd5…`); card `license: mit`. The Antalia card agrees: "downloads `nvidia/bigvgan_v2_24khz_100band_256x` (MIT)". | https://huggingface.co/nvidia/bigvgan_v2_24khz_100band_256x/blob/c329ede9e9bbc100ddf5c91e2330a61921262370/LICENSE | OK |
| **The Antalia patch to BigVGAN** `scripts/patches/bigvgan-huggingface-hub-1.patch` (in the code repo @ `20f9bfea…`) | Ships inside the Apache-2.0 code repo, so the patch is **Apache-2.0**. The patched `bigvgan.py` stays MIT plus two Apache-2.0 lines. **What it changes:** one hunk at `bigvgan.py` @@ -419 (`BigVGAN._from_pretrained`, keyword-only): `proxies: Optional[Dict]` → `proxies: Optional[Dict] = None` and `resume_download: bool` → `resume_download: bool = False`. Nothing else. That makes the hub mixin work on `huggingface_hub >= 0.23`; upstream has the same fix as open PR #11. It does not touch the model, the weights or the audio path. Our loader (§2.3) never calls `_from_pretrained`, so the patch is applied to match the authors' recipe only. | https://github.com/0daycloud/antalia/blob/20f9bfeaaefefb3ef723c292fe2bb0306e08823d/scripts/patches/bigvgan-huggingface-hub-1.patch ; https://github.com/NVIDIA/BigVGAN/pull/11 | OK |

### 1.2 What the OpenRAIL-M asks, quoted (LICENSE.md @ `eaec2aad…`)

**Use restrictions, Attachment A (lines 88-104), verbatim.** "You agree not to use the Model or Derivatives of the Model:"
- "In any way that violates any applicable national, federal, state, local or international law or regulation;"
- "For the purpose of exploiting, harming or attempting to exploit or harm minors in any way;"
- "To generate or disseminate verifiably false information and/or content with the purpose of harming others;"
- "To generate or disseminate personal identifiable information that can be used to harm an individual;"
- "To defame, disparage or otherwise harass others;"
- "For fully automated decision making that adversely impacts an individual's legal rights or otherwise creates or modifies a binding, enforceable obligation;"
- "For any use intended to or which has the effect of discriminating against or harming individuals or groups based on online or offline social behavior or known or predicted personal or personality characteristics;"
- "To exploit any of the vulnerabilities of a specific group of persons based on their age, social, physical or mental characteristics, in order to materially distort the behavior of a person pertaining to that group in a manner that causes or is likely to cause that person or another person physical or psychological harm;"
- "For any use intended to or which has the effect of discriminating against individuals or groups based on legally protected characteristics or categories;"
- **"To provide medical advice and medical results interpretation;"**
- "To generate or disseminate information for the purpose to be used for administration of justice, law enforcement, immigration or asylum processes, …"
- "To impersonate any real person, including the voice actor whose voice this Model reproduces, or to imply that a real person said something they did not say;"
- **"To present, label, or distribute Output as a genuine human recording, or to use Output without disclosing to listeners that the speech is synthetic where a reasonable listener could believe otherwise;"**
- "To generate speech for fraud, scams, social engineering, or unsolicited automated calls or messages, including political robocalls;"
- "To generate sexual, violent, or harassing content in the voice reproduced by this Model;"
- "To create voice-cloning datasets, voice conversion targets, or other Derivatives of the Model that are marketed or presented as the voice of a specific real person."

Paragraph 5 (line 67) adds: "You may use the Model … only for lawful purposes" and "You shall require all of Your
users … to comply". Paragraph 6 (line 68): "No use of the output can contravene any provision".

**Attribution duty, paragraph 4.f (line 64), verbatim:** "When You Distribute the Model or Derivatives of the Model,
and in any product, service, publication, or other public work that uses the Model or Derivatives of the Model
(including works built on their Output), You must give credit to the Licensor in a reasonable manner appropriate to
the medium and visible to its audience, … The credit must include, at minimum: the name "Antalia 1"; the authors
Sezgin Saygili, Emre Kaplaner, Oncel Ozgul and Fikri San Koktas (Patientdesk.ai); and a link to
https://huggingface.co/cloud0day3/antalia-1 or https://github.com/0daycloud/antalia. … Credit given under this
paragraph must not suggest that the Licensor endorses You or Your use (see paragraph 8)."

"Distribution" (line 42) means sharing with "a third party, including providing the Model as a hosted service".
Two more clauses matter. Paragraph 7 (line 72): "Licensor reserves the right to restrict (remotely or otherwise)
usage … You shall undertake reasonable efforts to use the latest version of the Model." Paragraph 8 (line 73): no
trademark use and no implied endorsement.

**Is single-owner personal use on the owner's own machines inside them? Yes, for the measurement.** The owner
synthesises twenty fixed sentences (`OWNER_SENTENCES`) locally. The owner knows the audio is synthetic, nothing
is distributed, no third party is served, and none of the sentences is medical, legal, deceptive or about a real
person. **For later everyday narration it is inside only under two conditions** (STOP-2 and STOP-3 below):
health content must not be narrated as advice or result interpretation in this voice, and any listener other than
the owner must be told that the speech is synthetic. Attribution is not triggered by private use. A single-owner
system has no public audience and no Distribution. We give the credit anyway (§8) because it costs one line.
Paragraph 7's "latest version" clause conflicts with exact pinning only in theory: development is discontinued
(README: "Development of this model is discontinued. This is the last checkpoint we were satisfied with").

### 1.3 Training data and consent, quoted from the card (README.md @ `eaec2aad…`)

- "All stages used only Common Voice 26.0 Turkish (CC0; 59,593 filtered clips), FLEURS Turkish (CC-BY-4.0;
  1,876 clips), and the consented recordings of the released voice (621 script-aligned segments / 2.965 h, later
  1,073 corrected segments / 5.008 h). No scraped audio, no other speakers' private recordings, no third-party
  TTS weights."
- "The voice actor signed an addendum permitting public redistribution of the recordings themselves, not only of
  these weights." / "The voice actor consented to redistribution of these weights and her recordings, not to
  derivative datasets of her voice presented as recordings of a real person."
- Corpus `cloud0day3/antalia-voice-corpus` @ `9e67eeb2616f4e7ed6b607155e1ab3d1fabd912e`: card `license: cc-by-4.0`
  (HF API), not gated. Code NOTICE: Common Voice "(CC0-1.0), under the Mozilla Data Collective Terms"; FLEURS
  "(CC-BY-4.0)"; "The CrossFlow implementation in this repository does not include or depend on F5-TTS/FreyaTTS
  weights, code, audio, or training data."
- Not verified by us: the signed addendum itself (not published). The consent statement is the authors' word.
  Common Voice speaker ids appear in the speaker table (`config.json` `speaker_vocabulary`, 1 502 entries,
  `cv-…` hashes). We only use `voicedata-candidate-b`.

### 1.4 STOP lines for the lead

- **STOP-1 (licence class): the weights `cloud0day3/antalia-1` are Antalia Open RAIL-M, which is not Apache-2.0,
  MIT, BSD, CC0 or CC-BY.** It is a use-restricted licence (Attachment A) that binds the Output too (para 6). By the
  card's rule the lead must accept it explicitly before `tts-antalia-measure` starts. My reading: the measurement
  (§1.2) is inside every restriction, and the grant is royalty-free, perpetual and needs no account.
- **STOP-2 (wiring, not measurement): "To provide medical advice and medical results interpretation".** A later
  `TTSRouter` wiring that lets this voice narrate whatever the assistant says could breach it. An example is the
  assistant explaining a lab result or a medication to the owner. The wiring card must keep health-classified
  answers off this provider, or the owner must accept the risk knowingly.
- **STOP-3 (wiring, not measurement): the synthetic-disclosure duty.** "to use Output without disclosing to
  listeners that the speech is synthetic where a reasonable listener could believe otherwise". The owner knows.
  But narration heard by guests, played into a phone call or forwarded as an audio file would need a disclosure.
  "unsolicited automated calls or messages" is forbidden outright. The wiring card must keep this voice off any
  outbound call or message path.
- **STOP-CHECK (lead, dependencies, same as the Freya plan §1.3): `soxr` 1.1.0 is LGPL-2.1-or-later**, and the
  `soundfile` 0.14.0 manylinux wheel bundles LGPL-2.1 `libsndfile`. librosa pulls them in, and BigVGAN's
  `meldataset.py:13` imports librosa at import time. They are used unmodified and dynamically linked in a private
  container that is never distributed. Precedent: Freya plan §1.3, and `fpdf2` (LGPL-3.0) in
  `docs/THIRD_PARTY_COMPONENTS.md`.
- No STOP for: the code (Apache-2.0), BigVGAN code and weights (MIT), the patch (Apache-2.0), the training data
  (CC0 / CC-BY-4.0). All of them could be read, and all of them install (§3).

### 1.5 Runtime dependencies (the §3 lock)

The 50 packages, from their PyPI JSON (`https://pypi.org/pypi/<name>/<version>/json`, `license_expression` /
`license` / classifiers). These are the same as the Freya plan §1.2 for the packages both locks share.
- torch 2.14.1 (Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2/3 AND BSL-1.0 AND MIT).
- Apache-2.0: huggingface-hub 0.36.2, hf-xet, requests, safetensors. python-dateutil is Apache-2.0 / BSD dual.
- BSD: numpy, scipy, numba, llvmlite, scikit-learn, contourpy, cycler, kiwisolver, joblib, threadpoolctl,
  fsspec, idna, jinja2, markupsafe, mpmath, networkx, pooch, pycparser, sympy, decorator, cloudpickle,
  lazy-loader, soundfile (the Python part).
- MIT / MIT-CMU: fonttools, pyparsing, six, filelock, platformdirs, setuptools, cffi, charset-normalizer, urllib3,
  pyyaml, msgpack, narwhals, typing-extensions (PSF-2.0), pillow (MIT-CMU).
- matplotlib: the Matplotlib licence (PSF-based, permissive).
- ISC: librosa.
- MPL-2.0 AND (Apache-2.0 OR MIT): orjson. MPL-2.0: certifi, tqdm. These are file-level copyleft, unmodified, and
  certifi and tqdm are already accepted in `services/api/uv.lock`.
- LGPL: soxr, libsndfile (STOP-CHECK above).

## 2. The exact pin

### 2.1 Code repositories

- `0daycloud/antalia` (https://github.com/0daycloud/antalia) — commit **`20f9bfeaaefefb3ef723c292fe2bb0306e08823d`**
  (2026-09-16 00:54 +0300, "Require attribution in the weights license", default-branch HEAD, not archived). Clone
  with `git -c core.autocrlf=false`. sha256 of the **committed blobs** (`git cat-file blob 20f9bfea:<path> | sha256sum`):

| File | sha256 |
|---|---|
| `src/turkish_tts/__init__.py` | `1b3261cde5f8f4b70577dd27a89880fda87f2ea75e0ffa8ec7efef45c81e5297` |
| `src/turkish_tts/crossflow.py` | `597bfc81f7ff23ec4ccf2da82f061293b3327926584fffcc61620b2a87710d38` |
| `src/turkish_tts/crossflow_train.py` | `4392208dbf7e0d20b1778c4ca0453d6f48ee33bd1f419fd115a4ff6acc889d5d` |
| `src/turkish_tts/crossflow_release.py` | `89cd6399a7f7a9154de0de5bdaa217ad530f8a5c691136cad5833227a1c84f9c` |
| `src/turkish_tts/normalize.py` | `84956ca49521f1a52f3abcdf5dcc759e385d1d262f10483b05213562f0466957` |
| `scripts/synthesize-crossflow.py` (reference CLI, not used) | `f0a926b783a5ecb2e2911b83b03b8e6b88bbf7cc04391f55d6c6294a6f0206f7` |
| `scripts/patches/bigvgan-huggingface-hub-1.patch` | `c65018be7ffc0e56117b5896a2c602e8b2361732bb64c4a39884d0dbbf376cf0` |
| `LICENSE` / `NOTICE` | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` / `e2db777defb70560a627e6c24959615b1fdb9c978ba32488f5452a0335f6a22d` |

  The image copies the whole `src/turkish_tts/` (`git archive 20f9bfea… src/turkish_tts`). Only the five modules
  above are imported on the synthesis path.
- `NVIDIA/BigVGAN` (https://github.com/NVIDIA/BigVGAN) — commit **`7d2b454564a6c7d014227f635b7423881f14bdac`**
  (2024-09-04, tag "v2.4"). This is the commit the Antalia README and NOTICE pin, and it is still `main` HEAD
  (`git log 7d2b4545..origin/main` is empty). Copy only `bigvgan.py activations.py utils.py env.py meldataset.py
  alias_free_activation/torch LICENSE` (`git archive`), then `git apply -p4` the patch (it applies cleanly).

| File (BigVGAN @ 7d2b4545) | sha256 (committed blob) |
|---|---|
| `bigvgan.py` before the patch | `dc0a39917d3a6f720afd888afe585ecbb72211e808d692deb4e5d85608062acb` |
| `bigvgan.py` **after** the patch (what the image holds) | `fb0dee3e2125962df8028c6577d391bfbffe74a5cc6b0c8b74b5056eabf1c7fb` |
| `activations.py` | `ff2562e116399bca730929aeb07e029a61321660a4f07c3db0b8f9853c70470f` |
| `utils.py` | `04eee590ca04ca33a6b2b79802a6514bbe7d6867164c72d61f9404ea9b0101c4` |
| `env.py` | `0c458da3132ebcce272eb277f9af489d09f3cf3dd4378ceeeb915b467aa039a5` |
| `meldataset.py` | `8b9e35a4f62728fdf55003b93c7ab3d8bb982504e0366333842f0ffa3293e6db` |
| `alias_free_activation/torch/__init__.py` | `2e3138e1052e377ba2e51ea59c7d5d255a519559001757af21dbec4cb9c22471` |
| `alias_free_activation/torch/act.py` | `651448005dd5ae0c193da60d1a50c0aa53550a4a3050e29ed7d35cf86410e212` |
| `alias_free_activation/torch/filter.py` | `acf2257276e617dd3161e53abc0a1582e586a3d2d618a43235c7f83818b4e179` |
| `alias_free_activation/torch/resample.py` | `4d7e1bf4169d03f59360f34f33d029c04b99364150f6737563ebfbee0ab49d79` |
| `LICENSE` | `5c7f573db5f807a9adc2a755c4901e203ea067f73c7a20fa6b703da7e77d7b35` |

  Note: the HF repo `nvidia/bigvgan_v2_24khz_100band_256x` also carries a `bigvgan.py`, and it is **different**
  (sha256 `2b2c5d7b…`). Use the GitHub one, as the authors do. Never import code from the weights repo.

### 2.2 Weights — every file the model loads

| Repository @ revision (commit sha) | File | Bytes | sha256 (downloaded and hashed 2026-10-05; equals the HF LFS record) |
|---|---|---|---|
| `cloud0day3/antalia-1` @ **`eaec2aad2da8c0db5fc359734470874dae82c603`** (lastModified 2026-09-15) | `model.safetensors` | 1 218 246 588 | `853a117ef95fa44efff785a6b674f380878da57879cb879d6d099d1e1444266e` (also printed in the card's "Files" table and in `inference-recipe.json`) |
| same | `config.json` | 49 150 | `7c52eb29997ff38d106cf9082a6e9f76ecf1a2cfab9ff50333e5c0414c6ed8cf` |
| same | `inference-recipe.json` (read for the recipe values; §4 hard-codes them) | 2 703 | `2140084f0c5ee1e49169779e772097eaeb10b166fcec2ac36464eba0dd74d609` |
| `nvidia/bigvgan_v2_24khz_100band_256x` @ **`c329ede9e9bbc100ddf5c91e2330a61921262370`** (lastModified 2024-09-05) | `bigvgan_generator.pt` | 450 088 331 | `6f9c5715550c9d0f11159ceb8935638da5aeb19e27d1e63677632df095e376f5` |
| same | `config.json` | 1 402 | `d77e2c96583ca2296ac112a56ec7cc6bd5da4bf7681ceff18448bedc4fcf6512` |
| `cloud0day3/antalia-1-foundation` @ **`302c1046c555b404a5f6cc3b0da39100b6439158`** | — (not loaded; provenance pin only; its `model.safetensors` LFS sha256 is `89ecf310a14333c6bd5a754360cee2207027dd23b2a0171984a32c423afcfcd1`) | — | — |

The other antalia-1 files (`prosody-presets.json` `add64b2e…`, `timbre-profile.json` `fa711a44…`,
`envelope-stats.json` `2150492d…`) are only for the best-of-N selector and are not fetched. The vocoder repo's
`bigvgan_generator_3msteps.pt` and the two 1.45 GB discriminator/optimizer `.pt` files are **never** fetched. All
three repos are public and not gated (HF API `gated: false`), so no token is needed. Sources:
https://huggingface.co/api/models/cloud0day3/antalia-1?blobs=true ,
https://huggingface.co/api/models/nvidia/bigvgan_v2_24khz_100band_256x?blobs=true .
Fill download: 1.67 GB (1.22 GB took 3 min 48 s here).

### 2.3 How the loader is forced onto the pin — the upstream loader CANNOT be

The pinned code has no revision argument on the Hub path:
- `crossflow_release.py:47-50`: a `--checkpoint` that does not exist locally and looks like `owner/name` goes to
  `snapshot_download(repo_id=reference, allow_patterns=[CONFIG_FILENAME, WEIGHTS_FILENAME])`, with **no
  `revision=`**.
- `crossflow_train.py:1575`: `BigVGAN.from_pretrained(str(vocoder_dir), use_cuda_kernel=False)`, with no revision.
  The release `config.json` has `"vocoder": {"repo_id": "nvidia/bigvgan_v2_24khz_100band_256x", "revision": null}`.
- huggingface_hub 0.36.2 turns a missing revision into `"main"`: `constants.py:58` `DEFAULT_REVISION = "main"`,
  `_snapshot_download.py:139`, `file_download.py:959`. `HF_HUB_OFFLINE` is read at `constants.py:165` and makes
  HTTP raise `OfflineModeIsEnabled` (`utils/_http.py:107`). (Line numbers are in the installed 0.36.2 wheel.)
- `crossflow_train.py:1552-1558`: a `--checkpoint` that **is a file** is loaded with `torch.load(…,
  weights_only=False)`, a full unpickle. Never pass a `.pt` file.

Binding for the measure card, the same shape as Freya §2.3:
1. **Fill step (network allowed, separate run):** fetch the five files by their pinned URLs:
   `https://huggingface.co/cloud0day3/antalia-1/resolve/eaec2aad2da8c0db5fc359734470874dae82c603/{model.safetensors,config.json}`
   and
   `https://huggingface.co/nvidia/bigvgan_v2_24khz_100band_256x/resolve/c329ede9e9bbc100ddf5c91e2330a61921262370/{bigvgan_generator.pt,config.json}`
   (plus `inference-recipe.json` if the script reads the recipe instead of hard-coding it). Write them into
   `/models/antalia-1/` and `/models/bigvgan/`, then `sha256sum -c` against §2.2. A mismatch is exit 3. No HF
   cache layout is used.
2. **Synthesis step:** never call `synthesize_crossflow`, `load_crossflow_checkpoint`, `resolve_release_path`,
   `_load_crossflow_vocoder` or `BigVGAN.from_pretrained`. Build both models by hand from the verified paths:
   - acoustic model: `turkish_tts.crossflow_release.load_release_payload(Path("/models/antalia-1"), "cpu")`
     (`crossflow_release.py:105-138`; local directory only; `load_file` + `load_state_dict(..., strict=True)` at
     line 115);
   - tokenizer: `CharacterTokenizer(payload["vocabulary"])`;
   - vocoder: `BigVGAN(AttrDict(json.load(config)), use_cuda_kernel=False)`, then
     `torch.load(path, map_location="cpu", weights_only=True)`, then
     `load_state_dict(ck["generator"], strict=True)`, `remove_weight_norm()`, `.eval()`. This is exactly what
     `bigvgan.py` `_from_pretrained` does (lines 414-493 at `7d2b4545`), but from a local path, with
     `weights_only=True` stated explicitly and `strict=True` (upstream's except-branch at 486-491 retries without
     weight norm; we do not want that path).
   - Then call `_synthesize_loaded_crossflow` (§4). The smoke run in §3 did exactly this and printed
     `VOCODER_LOAD <All keys matched successfully>`.
3. Still set `HF_HUB_OFFLINE=1` (baked into the image) and run with `--network none` (§5), so that any forgotten
   hub call fails loudly. Pass no `HF_TOKEN`.

Nothing is downloaded at synthesis time. That is proven by construction (no hub call on the path above), enforced
twice (offline env and no network), and was **run**: the smoke synthesis in §3 ran with `--network none
--read-only`.

## 3. Runtime

- Python **3.12**: `pyproject.toml` `requires-python = ">=3.12,<3.13"`; the image ships 3.12.15.
- Base image: **the FreyaTTS base, reused**:
  `python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3`.
- torch **2.14.1+cpu** from **`https://download.pytorch.org/whl/cpu`**. That is the current release, so the code
  installs on a current torch: **no "kurulamadı" STOP**. Unlike Freya, torchaudio is **not** needed (the synthesis
  path has no `torchaudio` import; it is only used in `LogMelFrontend` / `_prepare_reference`, which are not
  reached without reference audio). That frees torch from torchaudio's 2.11 ceiling.
- The upstream constraints that are kept are `numpy>=2.1.0,<2.3` (→ 2.2.6) and `huggingface-hub>=0.34.0,<1`
  (→ 0.36.2), taken from `pyproject.toml`.
- The antalia package itself is **not pip-installed**. Its declared dependencies (datasets, openai, jiwer,
  pydantic-settings, tenacity, typer…) are not on the synthesis path. `src/turkish_tts` goes on `PYTHONPATH`.
- Why matplotlib, scipy and librosa are in the lock: BigVGAN imports them at import time. `bigvgan.py:18` imports
  `utils`, which imports `matplotlib` and `scipy.io.wavfile` (`utils.py:6,11,13`). `utils` also imports
  `meldataset`, which imports `librosa` (`meldataset.py:13-14`). They cannot be dropped without editing vendor
  files, and we do not edit vendor files.
- How the lock was made:
  1. `uv pip compile` (`--python-version 3.12 --python-platform x86_64-manylinux_2_28`, index
     `pytorch-cpu=https://download.pytorch.org/whl/cpu`) from
     `torch, numpy>=2.1.0,<2.3, orjson>=3.10,<4, soundfile>=0.13,<1, safetensors>=0.5,<1, huggingface-hub>=0.34,<1,
     matplotlib, scipy, librosa, tqdm`.
  2. `pip download --no-deps --only-binary=:all:` inside the pinned base image.
  3. `sha256sum` of the 50 wheels actually fetched (350 MB).

Requirements body (50 lines, every line `==` + `--hash`; install with
`pip install --no-cache-dir --no-deps --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt`):

```text
certifi==2026.7.22 --hash=sha256:62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775
cffi==2.1.1 --hash=sha256:c1453022f490d2459a11819d83ad1d586e9ff65a12ac3e705ffebd46d3685dcf
charset-normalizer==3.5.2 --hash=sha256:3d31298449090ab8d47b7b1b2a555ff73cac7ed438a08b7ac160980c7ebed649
cloudpickle==3.1.2 --hash=sha256:9acb47f6afd73f60dc1df93bb801b472f05ff42fa6c84167d25cb206be1fbf4a
contourpy==1.4.0 --hash=sha256:875f42444c9cf48d56f724f2637e60d0f73b3b12c9041e1484580a233edf9591
cycler==0.12.1 --hash=sha256:85cef7cff222d8644161529808465972e51340599459b8ac3ccbac5a854e0d30
decorator==5.3.1 --hash=sha256:f47fe6fdbd2edd623ecfe36875d37aba411624e2670dd395dddae1358689bb3c
filelock==4.0.11 --hash=sha256:1cbdc4ace7fb11d2f6d073f5c3d91537a23b3d58d2bf2fdffc12c6d717a589c6
fonttools==4.66.1 --hash=sha256:7b8ff9e0edbcee2fbf7dff0c41b9041c1901c26acf64e23adb67495012df11de
fsspec==2026.9.0 --hash=sha256:8dd6e646e99ea382bd85f97a45e6b526a442d79423a7dc673f1e2756d05fcb5f
hf-xet==1.6.0 --hash=sha256:d62671bb130879cef0ee4c9ebe47a14af6c66ec53e6d84dc15936e5ffdfac82f
huggingface-hub==0.36.2 --hash=sha256:48f0c8eac16145dfce371e9d2d7772854a4f591bcb56c9cf548accf531d54270
idna==3.20 --hash=sha256:ab7ae7122974553370f0bdb919e1a960b2cd1bc1ef0276416d896db81c14582c
jinja2==3.1.6 --hash=sha256:85ece4451f492d0c13c5dd7c13a64681a86afae63a5f347908daf103ce6d2f67
joblib==1.6.0 --hash=sha256:3dbbf9f6e4b592a2357b854608e980fe6390d131d7a82f011a377ef2ebef7aba
kiwisolver==1.5.1 --hash=sha256:34633ecf50d16187ab8e5528b7a2530f2feb4e23f300db4672538b51cfc5cd38
lazy-loader==0.6 --hash=sha256:77253be3391b06124a0e16105bd663b6c54470af1a9ca8e1cf026f38d58ed056
librosa==1.0.0 --hash=sha256:5910a6c0e1b2e494b92758c1615a7acbd0515a2315e138927ba2982f2af88857
llvmlite==0.50.0 --hash=sha256:d501e5103076b9a14be885d2574dc2f6793171aa54a853d1244e011d476f1399
markupsafe==3.0.4 --hash=sha256:8e124f974786f831d6043728e38296969d3579db8896fe004682f5758e613581
matplotlib==3.11.2 --hash=sha256:df4f7784aca81a94f254c0a2767d592ee25f407e488f5fa7203e51093fb6ca27
mpmath==1.3.0 --hash=sha256:a0b2b9fe80bbcd81a6647ff13108738cfb482d481d826cc0e02f5b35e5c88d2c
msgpack==1.2.3 --hash=sha256:ede33b2892ceb976283e009ad12fa1834cfdf1f9c43ee9c97849fc588d00a618
narwhals==2.26.0 --hash=sha256:29326d74f107c347fd1009bd58e38d9f7c7c5b51e6de97bc93dbc325d9038b54
networkx==3.7 --hash=sha256:e3fd2c13a7814cee3746340d8d7f8598a67f16a58bf47fb7f8793fab6efca1b0
numba==0.68.0 --hash=sha256:51fe913a70fe9a7a0b193757ff977a9e96c82ae936ae388aec8990814fffdf9d
numpy==2.2.6 --hash=sha256:fd83c01228a688733f1ded5201c678f0c53ecc1006ffbc404db9f7a899ac6249
orjson==3.12.0 --hash=sha256:1192a7021b6d071aaf909864f6e924d6a2675ca360485b972b8401749311750b
packaging==26.3 --hash=sha256:d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c
pillow==12.3.0 --hash=sha256:78cb2c6865a35ab8ff8b75fd122f6033b92a62c82801110e48ddd6c936a45d91
platformdirs==4.12.3 --hash=sha256:080f3b39423b5abfca9a23d84c4e9795f54d395cd8459867a8ded44084fcd5f8
pooch==1.9.0 --hash=sha256:f265597baa9f760d25ceb29d0beb8186c243d6607b0f60b83ecf14078dbc703b
pycparser==3.0 --hash=sha256:b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992
pyparsing==3.3.3 --hash=sha256:ece8c00a69cf01b45d0b1dedabb469c90d8caf996d4fda40f147627a122849a4
python-dateutil==2.9.0.post0 --hash=sha256:a8b2bc7bffae282281c8140a97d3aa9c14da0b136dfe83f850eea9a5f7470427
pyyaml==6.0.3 --hash=sha256:ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc
requests==2.34.2 --hash=sha256:2a0d60c172f83ac6ab31e4554906c0f3b3588d37b5cb939b1c061f4907e278e0
safetensors==0.8.0 --hash=sha256:fd6f3f93c9a0a7cc2788ee63fb763353d4bd2e89b0751bc78fcf7dda00bea774
scikit-learn==1.9.1 --hash=sha256:e5d7b18a5b9dca241a74695f3275fa4c895a9dadc72b3d8df5fa9d1083c9b83e
scipy==1.18.1 --hash=sha256:f55fa87b6c612ecd6b058f167c53231b1d14e412efe361d3d6e38b3631c73218
setuptools==84.0.0 --hash=sha256:51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670
six==1.17.0 --hash=sha256:4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274
soundfile==0.14.0 --hash=sha256:1e38bac1853412871318e82a1ba69a8be677619b56025bbfcccdb41b6cafe82d
soxr==1.1.0 --hash=sha256:3b033078e86f3c4a658e5697fac8995764fad9e799563616b630136b613167f1
sympy==1.14.0 --hash=sha256:e091cc3e99d2141a0ba2847328f5479b05d94a6635cb96148ccb3f34671bd8f5
threadpoolctl==3.7.0 --hash=sha256:cd8b60b5641b45c67bbf73c64c843235fc2d8a480c87389f52f5dbee893b86be
torch==2.14.1+cpu --hash=sha256:5a6363570c753812540a05eb82380e329469cbe668643e88111414c12627711f
tqdm==4.70.1 --hash=sha256:c293e525e6fef9c20e8728fd4612df02a0aa31bb5fe91ecd93e123b1b7bffa73
typing-extensions==4.16.0 --hash=sha256:481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8
urllib3==2.8.0 --hash=sha256:0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3
```

The hashes are for **Linux x86_64 / CPython 3.12 only**: the measurement runs in the container on both machines.

Dockerfile body (built as below on 2026-10-05; the measure card puts it under `tools/tts-measure/antalia/` and
adds its own `synthesize.py` / `fetch_code.py` in the Freya shape):

```dockerfile
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-deps --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY src/turkish_tts /opt/antalia/turkish_tts
COPY src/bigvgan /opt/bigvgan
ENV PYTHONPATH=/opt/antalia:/opt/bigvgan HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HOME=/models/hf MPLCONFIGDIR=/tmp/mpl NUMBA_CACHE_DIR=/tmp/numba OMP_NUM_THREADS=4
RUN useradd --uid 10001 --no-create-home antalia
USER antalia
WORKDIR /opt/antalia
RUN python -c "import torch, bigvgan, turkish_tts.crossflow, turkish_tts.crossflow_train, turkish_tts.crossflow_release; print('IMPORT_OK', torch.__version__, torch.cuda.is_available(), torch.get_num_threads(), bigvgan.BigVGAN.__name__)"
```

`MPLCONFIGDIR` / `NUMBA_CACHE_DIR` point into the `--tmpfs /tmp`. matplotlib and numba otherwise try to write
under `$HOME` on import, and the root filesystem is read-only.

**Image: BUILT and proven with `docker run` (2026-10-05, home PC, Docker 28.3.2, `docker build --no-cache`).**
- `docker run --rm --network none pagentos-antalia-measure:plan python -c "<the IMPORT_OK line>"` printed
  `IMPORT_OK 2.14.1+cpu False 4 BigVGAN`.
- `docker run --user 0 … sh -c 'id antalia; wc -c /etc/passwd; find / -xdev -type f -size 0 | wc -l'` printed
  `uid=10001(antalia)`, `884 /etc/passwd` and `511`. The base image has 47 zero-byte files. The 511 are 371 empty
  `__init__.py`, 63 `REQUESTED`, 44 `py.typed`, 3 `__init__.pyi` and 2 `lock`: normal package markers, not the
  corruption the Freya image showed (8 039).
- **Smoke synthesis** (not a measurement, no timing kept): `docker run --rm --network none --read-only --tmpfs /tmp
  --cpus 4 --memory 6g -v <models>:/models:ro -v <out>:/out pagentos-antalia-measure:plan python /out/smoke.py
  "Sabah raporunu oku."`. It verified the four sha256 values and loaded both models through §2.3/2. It printed
  `VOCODER_LOAD <All keys matched successfully>` and `SMOKE_OK 24000 float32 (69632,) 'sabah raporunu oku.'
  torch.float32`: a 2.9 s clip at 24 kHz, written as a 139 308-byte WAV.
- Image id `sha256:c714980bd0c234dbd681a15a19ca998743d5c689109e4751e82be4c7ae9e72b9`, tag
  `pagentos-antalia-measure:plan` (local, not pushed). Size 2.19 GB on disk (`docker image ls`) and 484 541 978
  bytes content (`docker image inspect .Size`). Weights are not inside (they are mounted).
- **Removed afterwards** (`docker image rm` + `docker builder prune`) to keep C: free (35 GB before). The measure
  card rebuilds with `--no-cache` and its own content-addressed tag, and proves it with the same two `docker run`
  lines.

## 4. How synthesis is called (pinned commit `20f9bfea…`)

- Reference CLI (do not use as is): `scripts/synthesize-crossflow.py` calls `synthesize_crossflow(...)`
  (`crossflow_train.py:2204-2232`), which loads both models on every call (`:2233`, `:2238`) through the unpinned
  hub path (§2.3). It defaults to `--device cuda` (`synthesize-crossflow.py:22`) and to `--seed 20260803` (`:24`).
- **The call to use:** `_synthesize_loaded_crossflow(*, model, tokenizer, vocoder, mel_normalizer, text,
  sample_rate, hop_length, device, steps, seed, duration_scale, speaker_id, prosody, text_guidance_scale,
  speaker_guidance_scale, sway_coefficient, solver, guidance_rescale, mel_clamp, reference, context_guidance_scale,
  min_seconds_per_char, chunk_character_limit, chunk_pause_seconds, auto_style=None, mel_correction=None,
  text_normalization="turkish") -> tuple[waveform, normalized_text, predicted_frames]`
  (`crossflow_train.py:1768-1797`). This is exactly what `synthesize_crossflow` does at `:2252-2280` once the
  models are loaded. Build `mel_normalizer = MelNormalizer(CrossFlowTrainConfig(**payload["train_config"]), "cpu")`
  (`:2236-2237`), `speaker_id = _resolve_speaker_id(payload, "voicedata-candidate-b")` (`:1624`) and
  `model.set_adapter_scale(1.0)` (`crossflow.py:276`).
- **Speaker must be passed explicitly.** `_resolve_speaker_id(payload, None)` returns **0**, "the unconditioned
  foundation path" (`crossflow_train.py:1625-1626`; card "Speaker id 0 is the unconditioned foundation path"), which
  is not the released voice.
- **Recipe v2 values** (from `inference-recipe.json` @ `eaec2aad…`, the card's Quick start): `steps=32`,
  `seed=20260803`, `speaker="voicedata-candidate-b"`, and
  `prosody=[-1.2398956, 1.1943912, -2.1267404, -0.9549347, 0.9637866, 0.5145879]`. The guidance values are
  `text_guidance_scale=4.0`, `speaker_guidance_scale=1.0`, `sway_coefficient=-0.8`, `solver="euler"` and
  `guidance_rescale=0.5`. Then `mel_clamp=5.0`, `min_seconds_per_char=0.085`, `chunk_character_limit=120`,
  `chunk_pause_seconds=0.16`, `duration_scale=1.0`, `reference=None` and `context_guidance_scale=1.0`. Single seed
  only: **no best-of-8** (it needs Whisper + WavLM scoring and 8 generations; the card's 0.030 CER is best-of-8).
- **Correctness gap found:** the recipe also says `"pin_noise_envelope": true` ("v2 … pin_noise_envelope on"). At
  the pinned commit **no code reads it**: `grep -rn "pin_noise\|noise_envelope"` over `src/` and `scripts/` finds
  nothing. Commit `e237b59` "Add release recipe v2: rescale 0.5 and noise pinning" only adds the JSON. So the v2
  numbers (single-seed CER 0.0615 on 40 prompts) are **not reproducible from the published code**. Our run uses
  what the code does, and the measure report must say "noise-envelope pinning: not in the public code, not applied".
- **Input:** a Turkish string, characters. `validate_crossflow_synthesis_text` (`:1597-1612`) rejects control
  characters and runs the built-in Turkish normaliser (`normalize.py`; numbers, dates, currency and abbreviations
  are spelled out), and unknown characters are dropped (commit `8838766e`). The 68-symbol vocabulary is in
  `config.json`. `OWNER_SENTENCES` go in raw.
- **Output:** a float32 mono numpy array at **24 000 Hz**. `config.json` `"audio": {"sample_rate": 24000,
  "hop_length": 256, "n_mels": 100, …}`, the BigVGAN config `"sampling_rate": 24000`, and the smoke run printed
  `24000`. This confirms the proposal's 24 kHz. The measure card writes it with `soundfile` (as `:2282` does).
- **Seed:** `torch.Generator(device).manual_seed(seed)` for the initial noise (`crossflow.py:505-506`). Chunk *i*
  uses `seed + i * 100_000` (`crossflow_train.py:1891`). It is deterministic per text and seed.
- **Precision:** fp32 on CPU. The weights are F32 (HF API `"F32": 304552293`), and autocast to bfloat16 is enabled
  **only on CUDA** (`crossflow_train.py:1835-1839`), so the CPU path stays fp32 (the smoke run printed
  `torch.float32`). The vocoder input is `.float()` (`:1878`).
- **Threads:** the code never sets them. Set `torch.set_num_threads(N)` at start, set `OMP_NUM_THREADS=N` and limit
  the container with `--cpus N`. Record N with every number, as in Freya §4.
- **Not streaming.** Text is normalised and split into clauses of ≤120 characters (`_chunk_text`, `:1738-1766`).
  Each clause is rendered whole: the acoustic model samples all mel frames, then BigVGAN vocodes them (`:1851-1878`).
  The chunks are joined with 160 ms silences and returned as one array (`:1883-1897`). So with the pinned API
  **first-audio latency = the whole synthesis time** of the input. A later provider could emit per chunk, but this
  code does not, and the measurement must not claim it.
- Hidden costs to count: none like Freya's voiced-check retries. The whole time is the 32 Euler steps × CFG passes
  plus one vocoder pass per chunk. `waveform.size < sample_rate // 20` raises `RuntimeError` (`:1879-1880`): that is
  a FAILED sentence with its reason, never a silent drop.

## 5. Device safety and security

- **Separate process, separate image.** The model runs only in `pagentos-antalia-measure` (later, a provider image
  of its own). The API image never gets torch, BigVGAN or matplotlib: CLAUDE.md voice rule, narration TTS is its
  own subsystem. `services/api` dependencies are unchanged by this work.
- **Exactly as the FreyaTTS script applies them** (`scripts/voice/tts-measure.ps1`):
  - The host allow-list is checked before anything touches docker:
    `[string[]]$AllowedHosts = @("MAIL", "pagentos-core")` (line 45), compared with `$env:COMPUTERNAME` /
    `[Environment]::MachineName`. Otherwise it is exit 4 "never run this on the office PC" (lines 125-129).
  - A wrong hash at fill time is exit 3 (line 179).
  - Synthesis runs with `--network none --read-only --tmpfs /tmp --memory $MemoryLimit --memory-swap $MemoryLimit`
    (no swap; default `8g`, line 40) and `-e OMP_NUM_THREADS=$Threads`, plus `--cpus` when given (lines 188-190).
  - The weights are mounted read-only. The user is uid 10001 from the image, or the host uid on Linux (line 191).
  - The fill is the only networked run (line 173 on). It fetches the five files of §2.3/1 by fixed URL and checks
    sha256.
  - The memory ceiling stays the Freya rule (8 GB, no swap). The smoke run fitted under `--memory 6g`, but its peak
    was not measured. The measure card records the peak and does not lower the ceiling on a guess.
- **Weight formats, every loaded file:**
  - `antalia-1/model.safetensors`: **safetensors** (8-byte header length 37 408, then JSON + raw tensors), loaded
    with `safetensors.torch.load_file` (`crossflow_release.py:107,115`). No code runs on load.
  - `antalia-1/config.json`, `bigvgan/config.json`: **JSON** (`json.loads` / `json.load`). No code.
  - `bigvgan/bigvgan_generator.pt`: **NOT safetensors**. It is a torch zip archive whose `ema_0.990_g_05000000/data.pkl`
    is a **pickle**. Risk: a malicious pickle can run code on load if the unpickler lets it import arbitrary
    globals. Upstream loads it with `torch.load(model_file, map_location=map_location)` and no `weights_only`
    (`bigvgan.py:482`). On torch ≥ 2.6 that defaults to the restricted unpickler
    (`serialization.py:1481 weights_only = _default_to_weights_only(...)` in 2.14.1), unless
    `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD` is set (`:1500`). Inspected with `pickletools`: the pickle references
    exactly three globals, `collections.OrderedDict`, `torch._utils._rebuild_tensor_v2` and `torch.FloatStorage`,
    all on torch's weights-only allow-list, plus 1 568 REDUCE and 1 BUILD. It holds nothing else. Mitigations, all
    required:
    1. The file is accepted only if its sha256 equals `6f9c5715…e376f5` (§2.2), so the bytes are fixed.
    2. Our loader states `weights_only=True` explicitly (§2.3/2) and never relies on the default. The container
       never sets `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD`.
    3. The load runs inside the `--network none`, read-only, non-root container with only `/models` (ro) and
       `/out` mounted.
    4. Never pass a `.pt` to `load_crossflow_checkpoint` (`weights_only=False` at `crossflow_train.py:1558`).

    A later card may convert it once to safetensors offline; that is not needed to measure.
- **Open issues read (2026-10-05):**
  - `0daycloud/antalia`: **0 issues ever, 1 PR (#1, merged)** (GitHub API `issues?state=all`). The HF
    discussions of both model repos: 0. Nothing to list. The real "issue" is the README's own: "Development of
    this model is discontinued" — no fixes will come.
  - `NVIDIA/BigVGAN`: 19 open items, all PRs, the last push 2024-09-05 (also unmaintained). Relevant ones:
    - **#11** "Fix _from_pretrained signature for huggingface_hub>=0.23 compatibility". It is the same bug the
      Antalia patch fixes; we bypass `_from_pretrained` anyway.
    - **#13** "out-of-bounds read in upsampling convolution buffer". It is in the **CUDA kernel** (`elements[...]`
      buffer in `anti_alias_activation_cuda.cu`). We run `use_cuda_kernel=False` and do not even copy
      `alias_free_activation/cuda/`, so it is not on our path.
    - **#25 / #26** "save_audio … missing clamp before int16 conversion". That is BigVGAN's own inference
      script, which we do not use. Our WAVs are float32 via soundfile. If the measure card ever writes int16, it
      clamps first.
    - **#14** "NameError when cutoff is zero in kaiser_sinc_filter1d": the cutoff is fixed by the config and
      never zero.
    - The rest (#10, #12, #15-#24, #27-#29) are training, loss, discriminator or demo fixes, not on the
      inference path.
  - Source: https://api.github.com/repos/0daycloud/antalia/issues?state=all ,
    https://github.com/NVIDIA/BigVGAN/pulls .
- **Where it may run:** the home PC (`MAIL`) and the Cloud Core (`pagentos-core`) only, through the same
  allow-list. **Never the office PC** (employer machine). On the Cloud Core it competes with the live API for 4
  vCPU: run it outside the owner's active hours, with `--cpus` capped and one run at a time.
- **KVKK:** the twenty sentences are written and synthetic. The owner's voice is neither used nor produced. The
  voice is a consenting actor's (§1.3), never presented as a real recording (STOP-3).

## 6. Memory and CPU — what is known and what is not

Published by the authors (not ours):
- 304 552 293 parameters, F32 (card and HF API); `model.safetensors` 1.22 GB, plus the BigVGAN generator 450 MB
  (~112 M params × 4 bytes, read from its size).
- CER 0.0528 mean single seed, 0.0298 best-of-8 (Whisper-large-v3, their 120-prompt `turkish-v2` suite; card
  "Results"). The authors measured these themselves and partly with the noise pinning we cannot apply (§4).
- "Sixteen Euler steps cut generation time by 45 %".
- "Python 3.12; a CUDA GPU is recommended, CPU works slowly" (card Quick start; code README line 47).

Source: https://huggingface.co/cloud0day3/antalia-1/blob/eaec2aad2da8c0db5fc359734470874dae82c603/README.md .

**There is no CPU number anywhere** — no RTF, no latency, no RAM figure, no x86 or ARM figure. The smoke run only
proves that it loads and produces 24 kHz audio offline under a 6 GB cap with 4 CPUs; its time and peak were not
recorded and are not evidence. Resident memory, RTF and first-audio latency on the home PC (i7-14700KF) and the
CPX32 proxy (`cpx32-bicimi`, 4 vCPU) are exactly what `tts-antalia-measure` produces. Nothing here estimates them.
Measure at `steps=32` (the recipe). `steps=16` (`low_latency_steps`) may be a second, separately labelled row.

## 7. Alternatives seen in passing (no recommendation)

| Option | Licence (as read) | Turkish | Source |
|---|---|---|---|
| FreyaTTS-small (already measured, ADR-0296) | Apache-2.0 (code + weights) | yes, one voice | `team/plans/tts-freya-integration-plan.md` |
| Pocket TTS TR `kaanhgunay/pocket-tts-tr` @ `e5aa490d9aa6075cf047e4286fb57cecb99aa1ed` | card `license: cc-by-4.0`. Not proposed: its training data `serdarcaglar/turkish-tts-audiobooks` comes from audiobook channels whose copyright the dataset's publisher says is not cleared, and it can clone voices (KVKK risk) | yes | https://huggingface.co/kaanhgunay/pocket-tts-tr ; `team/reports/d20261004/cycle-researcher-2.md` line 16 |
| Piper `tr_TR-dfki-medium` | repo MIT, voice dataset **CC BY-NC-SA 4.0** | yes | Freya plan §7 |
| XTTS-v2 | Coqui Public Model License (non-commercial) | yes | Freya plan §7 |

## 8. THIRD_PARTY_COMPONENTS entry (the lead copies this; the worker does not edit the shared file)

```markdown
## Antalia 1 (local Turkish narration TTS, second candidate) — MEASUREMENT ONLY

Role: candidate local narration TTS behind `TTSProvider` (not wired; measurement card `tts-antalia-measure`).

- Weights: `cloud0day3/antalia-1` @ `eaec2aad2da8c0db5fc359734470874dae82c603` (`config.json`, `model.safetensors`
  sha256 `853a117ef95fa44efff785a6b674f380878da57879cb879d6d099d1e1444266e`) — **Antalia Open RAIL-M** (2026-09-16;
  use-based restrictions in Attachment A bind the Output; https://huggingface.co/cloud0day3/antalia-1/blob/eaec2aad2da8c0db5fc359734470874dae82c603/LICENSE.md).
  Accepted for single-owner local use by the lead (STOP-1, `team/plans/tts-antalia-integration-plan.md` §1.4).
- Attribution (OpenRAIL-M §4.f): "Antalia 1" by Sezgin Saygili, Emre Kaplaner, Oncel Ozgul and Fikri San Koktas
  (Patientdesk.ai) — https://huggingface.co/cloud0day3/antalia-1 . Used unmodified; no endorsement implied.
- Code: `0daycloud/antalia` @ `20f9bfeaaefefb3ef723c292fe2bb0306e08823d` (`src/turkish_tts` only, not pip-installed)
  — Apache-2.0 (https://github.com/0daycloud/antalia/blob/20f9bfeaaefefb3ef723c292fe2bb0306e08823d/LICENSE).
- Vocoder code: `NVIDIA/BigVGAN` @ `7d2b454564a6c7d014227f635b7423881f14bdac` (v2.4) — MIT, with Antalia's one-hunk
  `bigvgan-huggingface-hub-1.patch` (Apache-2.0; default values for `proxies` / `resume_download`).
- Vocoder weights: `nvidia/bigvgan_v2_24khz_100band_256x` @ `c329ede9e9bbc100ddf5c91e2330a61921262370`, only
  `bigvgan_generator.pt` (sha256 `6f9c5715550c9d0f11159ceb8935638da5aeb19e27d1e63677632df095e376f5`, torch pickle,
  loaded `weights_only=True`) and `config.json` — MIT.
- Training data (per the card): Common Voice 26.0 TR (CC0), FLEURS TR (CC-BY-4.0), 5.008 h of one consenting voice
  actor (`cloud0day3/antalia-voice-corpus`, CC-BY-4.0).
- Runtime: own container, python:3.12-slim-bookworm (digest-pinned, same base as FreyaTTS), torch 2.14.1+cpu,
  hash-locked requirements in the plan §3; includes LGPL-2.1 `soxr` and the LGPL-2.1 `libsndfile` bundled in
  `soundfile` (unmodified, dynamically linked, not distributed).
- Where it runs: home PC and Cloud Core only (`MAIL`, `pagentos-core` allow-list), `--network none` at synthesis;
  never the office PC; never in the API image.
- Why: a second free, self-hosted Turkish narration voice measured beside FreyaTTS (master checklist
  224/414/234/267); output 24 kHz mono, one voice, not streaming. Upstream development discontinued.
- Wiring conditions (OpenRAIL-M Attachment A): no medical advice / result interpretation in this voice; disclose
  synthetic speech to any listener other than the owner; never on outbound calls or messages.
```

## Later card (not planned here): wiring into `TTSRouter`

A separate owner-approved card would add a local provider implementing `TTSProvider`
(`services/api/app/voice/providers.py`, `synthesize(text, *, voice, speed, fmt) -> TTSResult`). It would talk HTTP
to the Antalia container, with no torch in the API. It would be registered behind the paid provider in `TTSRouter`
(`services/api/app/voice/router.py`) with a setting that defaults OFF. It would also have to:
- enforce STOP-2 and STOP-3 at routing time (health-classified text and any outbound or shared audio go elsewhere);
- show the §8 credit line on the about/settings page;
- decide on 24 kHz versus the player's format and on per-chunk streaming.

None of that is in this plan.
