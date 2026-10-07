# tts-pocket-tr-integration-plan — Pocket-TTS Turkish, integration plan (cycle d20261007)

Proposal: `team/proposals/2026-10-07-akisli-turkce-ses-pocket-tts.md`. Scope: **measurement only**. The worker of
`tts-pocket-tr-measure` builds from this file verbatim, in the FreyaTTS / Antalia pattern (`tools/tts-measure/`,
`tools/tts-measure/antalia/`, ADR-0296, `team/plans/tts-antalia-integration-plan.md`). The engine key is
**`pocket-tr`** (hyphen) everywhere: evidence `docs/evidence/tts-pocket-tr-measure.{json,md}`, image
`pagentos-pocket-tr-measure`, volume `pagentos-pocket-tr-weights`, build context `tools/tts-measure/pocket-tr/`.

Everything below was read on **2026-10-07** from the sources named next to each claim. The three model files and
the voice file were downloaded at the pinned revisions and hashed (all four equal the HF LFS / `SHA256SUMS` record).
The hash lock was made from the 35 Linux wheels actually fetched. On the home PC (Windows host, CPython 3.12.7, the
same package versions as Windows wheels — **not** the Linux container) the pinned code **loaded the Turkish
checkpoint with `strict=True`** and streamed one smoke sentence offline (§2.4). No timing was kept; that is the
measure card's work. **Image: not built** (C: had 17 GB free; the Freya plan's disk warning applies).

**STOP lines: none for licences or loading** (§1.5). One **RISK** paragraph for the later wiring decision is in §1.4.

## Şu an üzerinde çalışılan (for the lead's HANDOFF)

- Task: `tts-pocket-tr-integration-plan` — Pocket-TTS Türkçe integration plan (licence, pins, streaming call, voice, safety)
- Area: `team/plans/tts-pocket-tr-integration-plan.md` (only this file)
- Machine: home PC (`MAIL`), worktree `.claude/worktrees/team/d20261007/worker-tts-pocket-tr-integration-plan`

## 1. Licences

### 1.1 Every piece the loader opens, and the pieces it derives from

| Piece | Licence | Read from (the actual file) | Verdict |
|---|---|---|---|
| Code `kyutai-labs/pocket-tts` tag `v3.3.0` = commit `3dbee45d343d7dddd0d105468d17f8dcba14db3e`, installed as the PyPI wheel `pocket_tts-3.3.0-py3-none-any.whl` | **MIT**: `LICENSE` is the MIT permission text ("Permission is hereby granted, free of charge, …"; it has no title line and no copyright line). GitHub API `license.spdx_id: MIT`. The wheel bundles the same text as `pocket_tts-3.3.0.dist-info/licenses/LICENSE` (`License-File: LICENSE`; sha256 `23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3`, equal to the repo file modulo CRLF). PyPI JSON has no `license` field. | https://github.com/kyutai-labs/pocket-tts/blob/3dbee45d343d7dddd0d105468d17f8dcba14db3e/LICENSE ; https://api.github.com/repos/kyutai-labs/pocket-tts ; https://pypi.org/project/pocket-tts/3.3.0/ | OK |
| Git dependencies of pocket-tts | **None.** `pyproject.toml` @ v3.3.0 declares only PyPI packages (numpy, torch, pydantic, sentencepiece, tokenizers, safetensors, typer, typing_extensions, fastapi, uvicorn, python-multipart, scipy, einops, huggingface_hub, requests). **The Mimi codec is not pulled from `moshi`**: it is re-implemented inside the package (`pocket_tts/models/mimi.py`, `pocket_tts/modules/seanet.py`, `conv.py`, …), so it is MIT code like the rest. | https://github.com/kyutai-labs/pocket-tts/blob/3dbee45d343d7dddd0d105468d17f8dcba14db3e/pyproject.toml | OK |
| Turkish weights `kaanhgunay/pocket-tts-tr` @ `e5aa490d9aa6075cf047e4286fb57cecb99aa1ed` (`model.safetensors`, `tokenizer.model`, `config.yaml`) | **CC-BY-4.0**: card front matter `license: cc-by-4.0` (README.md line 2) and "## License — Model weights are released under CC BY 4.0." (lines 98-100). The repo has **no LICENSE file** (files: `.gitattributes`, `README.md`, `SHA256SUMS`, `config.yaml`, `model.safetensors`, `tokenizer.model`, `training/tr_24l_teacher.yaml`); the card is the grant. Card line 102: "Users are responsible for ensuring that voice cloning and generated speech are used lawfully and with appropriate consent." | https://huggingface.co/kaanhgunay/pocket-tts-tr/blob/e5aa490d9aa6075cf047e4286fb57cecb99aa1ed/README.md | OK |
| **The codec weights** | **Inside the Turkish file.** The Turkish `config.yaml` has only a top-level `weights_path` (no `flow_lm.weights_path`, no `mimi.weights_path`), so the loader takes flow LM **and** Mimi from the one `model.safetensors` (`tts_model.py:257-268`); its header holds 87 `mimi.*` tensors (§2.3). No Kyutai codec repository is opened. Licence: CC-BY-4.0, as the row above. | header read (§2.3) | OK |
| Base the Turkish checkpoint derives from (**not opened by our loader**) | Card: "This model is derived from Kyutai Pocket TTS" (line 92); `base_model: kyutai/pocket-tts`; the training recipe `training/tr_24l_teacher.yaml` line 1 `model_config: pocket_tts/config/english_2026-04_24l.yaml`, whose weights are `hf://kyutai/pocket-tts/languages/english_2026-04_24l/model.safetensors@492522650173a0653b7575cdc25ae09810e5d741` (gated, voice-cloning variant) / `kyutai/pocket-tts-without-voice-cloning …@e81d79e8194ad4c7ce879c87a4258ef20cbf2487`. Both repos: card **`license: cc-by-4.0`** (HF API `cardData.license`; `kyutai/pocket-tts` README.md line 2). `kyutai/pocket-tts` is `gated: auto` with the "Prohibited use" text quoted in §1.3. We never download it, so we accept no gate. | https://huggingface.co/kyutai/pocket-tts/blob/3e82814a68665eec246ff649b14c71331f955c06/README.md ; https://huggingface.co/api/models/kyutai/pocket-tts-without-voice-cloning (sha `1e08e6a23401048648a9fdcfde2f89348215c2a7`) | OK (attribution owed, §1.2) |
| Voice repository `kyutai/tts-voices` @ `323332d33f997de8394f24a193e1a76df720e01a`, **only** `voice-donations/Selfie.wav` (§5) | **CC0**: README.md lines 20-22, "## voice-donations/ — Voices of volunteers submitted through our [Unmute Voice Donation Project](https://unmute.sh/voice-donation), licensed as CC0. The Voice Donation project ran from June 2025 to February 2026 and collected 228 voices that passed verification". The repo has no front-matter licence; the README is the per-folder grant (pocket-tts README line 59-60: "this page … details the licenses for each voice"). | https://huggingface.co/kyutai/tts-voices/blob/323332d33f997de8394f24a193e1a76df720e01a/README.md | OK |
| Kyutai predefined voice **embeddings** (`kyutai/pocket-tts-without-voice-cloning/languages/<lang>/embeddings/*.safetensors`) | Not usable and not opened: "Predefined voices are states precomputed with the released weights of a language model, so neither a custom config nor a training checkpoint can use them" (`default_parameters.py:59-62`). A name such as `"marius"` passed to a model loaded from a custom config raises `ValueError` (`tts_model.py:991-996`). | code @ v3.3.0 | not loaded |

### 1.2 The CC-BY-4.0 attribution duty, quoted

CC BY 4.0 legal code, Section 3(a)(1), verbatim: "If You Share the Licensed Material (including in modified form),
You must: a. retain the following if it is supplied by the Licensor with the Licensed Material: i. identification
of the creator(s) of the Licensed Material and any others designated to receive attribution, in any reasonable
manner requested by the Licensor (including by pseudonym if designated); ii. a copyright notice; iii. a notice that
refers to this Public License; iv. a notice that refers to the disclaimer of warranties; v. a URI or hyperlink to
the Licensed Material to the extent reasonably practicable; b. indicate if You modified the Licensed Material and
retain an indication of any previous modifications; and c. indicate the Licensed Material is licensed under this
Public License, and include the text of, or the URI or hyperlink to, this Public License." Section 3(a)(2): it may
be satisfied "in any reasonable manner based on the medium, means, and context".
Source: https://creativecommons.org/licenses/by/4.0/legalcode.txt (lines 210-250).

What the Licensor supplied to retain (Turkish card lines 90-96, verbatim): "This model is derived from Kyutai
Pocket TTS. Training data attribution: Serdar I. Çağlar, *Turkish TTS Audiobooks: a 2,724-hour Turkish
read-speech corpus for text-to-speech*, Hugging Face, 2026." No copyright line is supplied. The duty is triggered
by **sharing**; a single-owner measurement on the owner's machines shares nothing. We record the credit anyway
(§8): it costs one line. Our local config file (§2.2) is a modification of the Licensor's `config.yaml` (two paths
rewritten), and §8 says so. The CC0 voice carries no attribution duty.

### 1.3 Use terms that are not licences, quoted

- Kyutai's "Prohibited use" (pocket-tts README line 402 @ v3.3.0; also the gate prompt of `kyutai/pocket-tts`):
  "Prohibited uses include, without limitation, voice impersonation or cloning without explicit and lawful consent;
  misinformation, disinformation, or deception (including fake news, fraudulent calls, or presenting generated
  content as genuine recordings of real people or events); …". The measurement is inside it: the voice is a CC0
  donation made for this use (§5) or the owner's own.
- The dataset's access terms (§1.4) bind whoever accepted its form (the checkpoint's publisher). They pass on two
  obligations "to whoever uses your version": cite the dataset (§8 does) and publish the weights of any model
  **trained** on it. We train nothing: **never fine-tune or distil this checkpoint** in this project. That is a
  rule for the wiring card, not a STOP.

### 1.4 Bağlama kararının önüne konacak risk

**The lead copies this paragraph into the Onay Merkezi beside the numbers.** The Turkish checkpoint was trained
"primarily on `serdarcaglar/turkish-tts-audiobooks`" (card lines 36-40; front matter `datasets:`). That dataset
(HF sha `744ea6aacefcc4b9385b8c10cd630fb60ca91e39`, lastModified 2026-08-23, `gated: auto`) declares
`license: other`, `license_name: open-weights-with-attribution` (README.md lines 4-5). Its card, section
"## Licensing", says verbatim: "**The underlying rights are not the publisher's to give.** The source recordings
were collected in mid-2026 from publicly accessible Turkish audiobook and spoken-article upload channels; … The
recordings' individual copyright status was **not** cleared, and automatic processing grants no redistribution
rights — so this release cannot and does not grant you any licence over the audio or the underlying texts. Whoever
uses or publishes this dataset, a derivative, or a model trained on it is responsible for verifying the rights of
the underlying recordings and transcripts in their jurisdiction." The gate prompt says the same in Turkish:
"Kaynak kayıtların hakları temizlenmemiştir; hukuki sorumluluğu üstlenir ve hak sahibi talebi üzerine
kopyalarınızı silersiniz. Ayrıca kimsenin sesini rızası olmadan klonlamamanızı rica ederiz." Meaning for us: the
weights are CC-BY-4.0, but the voices and texts they learned from are audiobook narrators' and publishers' whose
rights nobody cleared. A rightsholder could object to a model trained on them, and the publisher promises takedown
on request (so the checkpoint itself may disappear or change). **Measurement on the owner's own machines is
acceptable (the proposal's decision). Wiring it as the owner's everyday voice is a separate owner decision that must
weigh this risk.** Source: https://huggingface.co/datasets/serdarcaglar/turkish-tts-audiobooks/blob/744ea6aacefcc4b9385b8c10cd630fb60ca91e39/README.md
(lines 1-71 front matter and gate; lines 583-616 "Access and terms"; lines 682-703 "Licensing").

### 1.5 STOP lines for the lead

- **STOP: none.** Every piece the loader opens is MIT (code), CC-BY-4.0 (weights, tokenizer, config, codec inside
  the weights) or CC0 (the voice). Every licence was readable at the pinned revision. The pinned code loads the
  checkpoint (§2.4): no "yüklenemedi".
- The training data's `license: other` is **not** a STOP for the measurement (card rule); it is the §1.4 risk.
- Runtime dependencies (§3 lock, PyPI JSON `license_expression` / `license` of each pinned version,
  https://pypi.org/pypi/<name>/<version>/json): MIT — annotated-types, anyio, charset-normalizer, filelock, h11,
  pydantic, pydantic-core, pyyaml, setuptools, typing-inspection, urllib3; BSD — click, fsspec, httpcore, httpx,
  idna, jinja2, markupsafe, mpmath, networkx, sympy, scipy (BSD-3, PyPI field holds the copyright text); Apache-2.0 —
  hf-xet, huggingface-hub, requests, safetensors, sentencepiece, tokenizers; packaging (Apache-2.0 OR BSD-2);
  numpy (BSD-3 AND 0BSD AND MIT AND Zlib AND CC0-1.0); torch (Apache-2.0 AND … BSD AND BSL-1.0 AND MIT);
  typing-extensions (PSF-2.0); MPL-2.0 — certifi, tqdm (MPL-2.0 AND MIT), both already in `services/api/uv.lock`.
  **No LGPL this time**: `soundfile` (bundled LGPL `libsndfile`) and `soxr` are not needed and not in the lock (§3, §5).

## 2. The exact pin

### 2.1 Code

- `pocket-tts` **3.3.0** from PyPI, wheel `pocket_tts-3.3.0-py3-none-any.whl`, sha256
  **`77b2eb5554cb710e92888f84e28816f2361dba075e0ace47108458794db99111`** (equals the PyPI JSON digest). It is the
  release of tag `v3.3.0` = commit **`3dbee45d343d7dddd0d105468d17f8dcba14db3e`** (2026-09-24). The wheel's
  `pocket_tts/` equals the tag's `pocket_tts/` file for file (`diff -r --strip-trailing-cr`: no difference), so the
  `file:line` references below hold for both. `main` is 13 commits ahead (`41cbc84a…`, 2026-10-01): chunk-start
  fade-ins (#332/#333/#335), "End voice prompts on a short pause" (#334), new language models. None is needed to load
  the Turkish checkpoint; the pin stays on the release.
- Installed `--no-deps`: its CLI/server dependencies (fastapi, uvicorn, typer, python-multipart, einops) are not on
  the synthesis path. `import pocket_tts` loads only `models/model_state.py` and `models/tts_model.py`
  (`__init__.py:1-2`); fastapi/uvicorn/typer are imported only by `pocket_tts/main.py`, never imported by us; `einops`
  is imported nowhere in the package.
- sha256 of the wheel's files on the synthesis path (LF bytes as installed):

| File | sha256 |
|---|---|
| `pocket_tts/__init__.py` | `d6a4eab97e2f4acb8b6bd5268d55a52e1fdce991e45b0dd536395ad02e2e5777` |
| `pocket_tts/default_parameters.py` | `374f50d10d9c257a70a464e385a903f9ac7a1b588a4631f22bed5622f376ee38` |
| `pocket_tts/models/tts_model.py` | `7abdbb4c47615c7b8c04359d13be4cabf3ece7d226205cd5556fb5ff4c06dd22` |
| `pocket_tts/models/flow_lm.py` | `803333bff6a6f5bc17107ce5e6479e17e12ee012fd5279494e24e1ed955e94a5` |
| `pocket_tts/models/mimi.py` | `93996a84525e41fb204a53f1edd72abdf5925f6bb8f92304e87d29c7a431054b` |
| `pocket_tts/models/model_state.py` | `b55655e887e83ff529cfbccf748d94a23cfa8f6e4917fc96c4cfe403abde8848` |
| `pocket_tts/models/text_chunking.py` | `215a78549b527495206b93e0941449a8ffb30be1bcf6aa311f5de8ea0d8716d3` |
| `pocket_tts/modules/text_conditioner.py` | `d3f3de0f7eb9f400ed7bd484981f6682a3690c09ea9e8c25ebb6d88564ce18ac` |
| `pocket_tts/modules/transformer.py` | `c9543c9abb4a6f76173942696d6add83cfc4539000140bb9d6e1238fa0cc96ea` |
| `pocket_tts/modules/attention.py` | `4b1763c527e6bb87c4152c7f17f5aa30a88164f1ed11136110539a53d0dbd296` |
| `pocket_tts/modules/seanet.py` | `17cfd00617f8aae225b699c3d66d3bfd463fa717090edb97498936bbdf5080d8` |
| `pocket_tts/modules/conv.py` | `9fdeb902936ac3e7d0e0abaf2bffdceb7b1e1632804c36b4120e6bad0840faa1` |
| `pocket_tts/utils/utils.py` | `8308eecd49d89f5329ce362175584f73a50e0d804fdd874cde34f0f74d02fdc4` |
| `pocket_tts/utils/config.py` | `dcabaccaf030fd215989e75b89b1867351eaa905132091f6cfea31539980f4fe` |
| `pocket_tts/utils/weights_loading.py` | `e1b38ba05f23a0b4581c6711dfa4715725398451eaaf26ff15a0c541639f54bf` |
| `pocket_tts/data/audio.py` | `3e5048f0210f46486661055768f9078f388e465914752cbf7f9971fe968668f5` |
| `pocket_tts/data/audio_utils.py` | `2a44e6be3f3d5c4424fcafd0b8df7b67570b22bde0af535ba70b14ea3a7bb3c3` |

  The wheel's `RECORD` covers every other file; `pip install --require-hashes` checks the wheel as a whole.

### 2.2 Weights, tokenizer, config, voice — every file the model loads

| Repository @ revision (commit sha) | File | Bytes | sha256 (downloaded and hashed 2026-10-07; equals the HF LFS record and, for the Turkish files, `SHA256SUMS`) |
|---|---|---|---|
| `kaanhgunay/pocket-tts-tr` @ **`e5aa490d9aa6075cf047e4286fb57cecb99aa1ed`** (lastModified 2026-09-02; the repo's whole history is 2 commits) | `model.safetensors` | 1 344 315 200 | `575d7a90e41de407340e8048e6c1b3d6573c36ab9054e1233d3cb0d6777eaf5f` |
| same | `tokenizer.model` (SentencePiece) | 302 928 | `c9ff291390865a78112e23b8ded6842300c6a88a58b0ef9694d6e8282f4ea490` |
| same | `config.yaml` (fetched for provenance; **not** loaded as is, see below) | 1 106 | `350c8145552415cfe092c3fa8f3181715b7e3c666ee7bab46b8480b36668a553` |
| ours (made from the line above) | `pocket-tr.yaml` — the loaded config, upstream `config.yaml` with exactly two lines rewritten, LF, 55 lines | — | `3f8ccc0071737d44b9ffda237730de180a38fef994ccb86856ef2d9b85508018` |
| `kyutai/tts-voices` @ **`323332d33f997de8394f24a193e1a76df720e01a`** (lastModified 2026-03-09) | `voice-donations/Selfie.wav` (§5) | 480 044 | `076968c3122520f3412eb7090e8c1c3f75fe57be1e24a2f96465583d84c71e16` |

The two rewritten lines of `pocket-tr.yaml` (everything else byte-identical to upstream):
`weights_path: /models/pocket-tr/model.safetensors` (upstream line 1:
`hf://kaanhgunay/pocket-tts-tr/model.safetensors@v0.1-base`) and `    tokenizer_path: /models/pocket-tr/tokenizer.model`
(upstream line 20: `hf://kaanhgunay/pocket-tts-tr/tokenizer.model@v0.1-base`). The measure card writes this file
in its build context, checks its sha256 like the others and mounts it read-only.

All repos are public. `kaanhgunay/pocket-tts-tr` and `kyutai/tts-voices` are `gated: false`: no token. Sources:
https://huggingface.co/api/models/kaanhgunay/pocket-tts-tr?blobs=true ,
https://huggingface.co/api/models/kaanhgunay/pocket-tts-tr/refs ,
https://huggingface.co/api/models/kyutai/tts-voices/tree/323332d33f997de8394f24a193e1a76df720e01a/voice-donations .
Fill download: 1.35 GB.

### 2.3 What the checkpoint is (safetensors header, read 2026-10-07)

- Header: 42 032 bytes of JSON, no `__metadata__`, **358 tensors, all `F32`**, data end 1 344 273 160 + 8 + 42 032 =
  the file size exactly.
- **Real parameter count: 336 068 290** — `flow_lm.*` 316 013 953 (271 tensors) + `mimi.*` 20 054 337 (87 tensors).
  The proposal's "~0.3B, ~3× the 100 M base" is right: this is the **24-layer** variant, not the 100 M (6-layer)
  model the base README's speed figures describe.
- Architecture fields (`config.yaml`): `flow_lm.transformer` d_model 1024, num_heads 16, **num_layers 24**,
  hidden_scale 4, max_period 10000; `flow_lm.flow` depth 6, dim 512 (type defaults to `"lsd"`, `config.py:23`;
  the training recipe says `flow: type: lsd`); `lookup_table` dim 1024, **n_bins 4000**, tokenizer `sentencepiece`;
  `insert_bos_before_voice: true`; `dtype: float32`; `mimi` sample_rate **24000**, frame_rate **12.5**, channels 1,
  inner_dim 32, outer_dim 512, SEANet ratios [6, 5, 4], transformer 2 layers × d_model 512;
  `default_temperature: 0.3`; `weights_path_without_voice_cloning: null`.

### 2.4 Does the pinned code load it? **Yes — run, not only read.**

On the home PC, Windows CPython 3.12.7, torch 2.14.1+cpu and every other §3 version (Windows wheels), `HF_HUB_OFFLINE=1`
and `HTTP(S)_PROXY=http://127.0.0.1:9` (any network call would fail), `TTSModel.load_model(config=<local copy of
pocket-tr.yaml>)` printed `LOAD_OK params 336068290 sr 24000 voice_cloning True dtype torch.float32 threads 4`. The
load goes through `safetensors.torch.load_file` + `tts_model.load_state_dict(state_dict, strict=True)`
(`tts_model.py:267-268`), so no key is missing or unexpected. The pydantic config is `extra="forbid"`
(`config.py:13-14`) and accepted every field. One smoke stream (§4) produced audio. This is evidence of
load-compatibility only; the Linux container repeats it in its selfcheck.

### 2.5 How the loader is forced onto the pin — the upstream config CANNOT be used

- **The card's documented command does not resolve.** `--config "hf://kaanhgunay/pocket-tts-tr/config.yaml@v0.1-base"`
  (card lines 49-53), and the config's own `@v0.1-base` paths, name a revision that **does not exist**: the repo
  has no tags and one branch (`refs` API: `{"tags":[],"branches":[{"name":"main",…"targetCommit":"e5aa490d…"}]}`),
  and `https://huggingface.co/kaanhgunay/pocket-tts-tr/resolve/v0.1-base/config.yaml` answers **404**. (Card line 56
  also still says "Replace `HF_USERNAME`…", an unedited template.)
- How the code resolves paths: `download_if_necessary` (`utils/utils.py:106-129`). `hf://repo/file@rev` is split at
  `@` (`:122-125`) and handed to `hf_hub_download(repo_id, filename, revision)` (`:126`); without `@` the revision is
  `None`, which huggingface_hub 1.33.0 turns into `"main"` (`file_download.py:990-991`; `constants.py:60`
  `DEFAULT_REVISION = "main"`). A plain path is returned as `Path(file_path)` with **no network** (`:128-129`). The
  config itself goes through the same function (`config.py:141-153`, `yaml.safe_load`).
- Binding for the measure card, same shape as the Antalia plan §2.3:
  1. **Fill step (network allowed, separate run):** fetch by pinned URL
     `https://huggingface.co/kaanhgunay/pocket-tts-tr/resolve/e5aa490d9aa6075cf047e4286fb57cecb99aa1ed/{model.safetensors,tokenizer.model,config.yaml}`
     and `https://huggingface.co/kyutai/tts-voices/resolve/323332d33f997de8394f24a193e1a76df720e01a/voice-donations/Selfie.wav`
     into `/models/pocket-tr/` and `/models/voices/`, write `pocket-tr.yaml` (from the build context), then
     `sha256sum -c` against §2.2. A mismatch is exit 3. No HF cache layout is used.
  2. **Synthesis step:** `TTSModel.load_model(config="/models/pocket-tr/pocket-tr.yaml")` — local paths only, so
     `download_if_necessary` never reaches the hub. Never pass `language=` (it would load Kyutai's own configs from
     `CONFIGS_DIR`, `tts_model.py:356-357`), never a predefined voice name, never an `hf://` or `https://` path,
     never `checkpoint=` (`.pt`, §6). The voice is the local file `Path("/models/voices/Selfie.wav")`.
  3. `HF_HUB_OFFLINE=1` (read at `constants.py:194`; any HTTP then raises `OfflineModeIsEnabled`,
     `utils/_http.py:284`) baked into the image, and `--network none` (§6). Pass no `HF_TOKEN`.

Nothing is downloaded at synthesis time: by construction (local paths at every `download_if_necessary` call:
config `config.py:142`, weights `tts_model.py:260`, tokenizer `text_conditioner.py:29`, voice `tts_model.py:1008`),
enforced twice (offline env + no network), and run that way in §2.4.

## 3. Runtime

- Python **3.12** (pocket-tts `requires-python = ">= 3.10,<3.15"`). Base image: **the Antalia/FreyaTTS base,
  reused**: `python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3`.
- torch **2.14.1+cpu** from **`https://download.pytorch.org/whl/cpu`** (the same version as the Antalia lock;
  pocket-tts asks `torch>=2.5.0`, and its pyproject notes 2.4 produced wrong audio). torchaudio is not needed.
- `soundfile` is **left out on purpose**: it is the optional `audio` extra, used only for non-WAV or non-16-bit WAV
  input (`data/audio.py:28-60`). Our voice inputs are 16-bit PCM WAV (§5) and our output is written with the stdlib
  `wave` module or `scipy.io.wavfile`. This keeps LGPL `libsndfile` out of the image.
- How the lock was made: `uv pip compile` (uv 0.12.7, `--python-version 3.12 --python-platform x86_64-manylinux_2_28`,
  index `pytorch-cpu=https://download.pytorch.org/whl/cpu`) from `torch, numpy>=2, pydantic>=2, sentencepiece>=0.2.1,
  tokenizers>=0.21, safetensors>=0.4.0, scipy>=1.5.0, huggingface-hub>=0.13.0, requests>=2.20.0,
  typing-extensions>=4.10.0, pyyaml` (pocket-tts's own pins minus the CLI/server ones, plus `pyyaml`, which
  `config.py:5` imports); then `pip download --no-deps --only-binary=:all: --platform manylinux_2_28_x86_64
  --python-version 3.12 --abi cp312` of the 35 pins, and `sha256sum` of the 35 wheels actually fetched (262 MB).
  One hash per line, the exact Linux cp312 wheel.

Requirements body (35 lines, every line `==` + `--hash`; install with
`pip install --no-cache-dir --no-deps --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt`):

```text
annotated-types==0.8.0 --hash=sha256:f072f4d804ea359e4eaf198b1af7a8b0943881a87f31bb764f8bf219bb9419e0
anyio==4.15.1 --hash=sha256:6152fdbbf9a77fdec97731721bebf7c4c44f7c29b424b0065826173efc7ed101
certifi==2026.7.22 --hash=sha256:62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775
charset-normalizer==3.5.2 --hash=sha256:3d31298449090ab8d47b7b1b2a555ff73cac7ed438a08b7ac160980c7ebed649
click==8.5.0 --hash=sha256:255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360
filelock==4.0.12 --hash=sha256:5f17ee83ecee8a6f3e389c75822fb70a1c2f0506b99438a6dffa1d56793588c8
fsspec==2026.9.0 --hash=sha256:8dd6e646e99ea382bd85f97a45e6b526a442d79423a7dc673f1e2756d05fcb5f
h11==0.16.0 --hash=sha256:63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86
hf-xet==1.7.0 --hash=sha256:2814a6e999d13464c4d679b788cc5d784eb5a4edfc638a31f10e9a11ab531ef8
httpcore==1.0.9 --hash=sha256:2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55
httpx==0.28.1 --hash=sha256:d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad
huggingface-hub==1.33.0 --hash=sha256:04e434b06e100eddbce9a6e817d72693a7884b10a79bd67ab48080d5c07eb899
idna==3.20 --hash=sha256:ab7ae7122974553370f0bdb919e1a960b2cd1bc1ef0276416d896db81c14582c
jinja2==3.1.6 --hash=sha256:85ece4451f492d0c13c5dd7c13a64681a86afae63a5f347908daf103ce6d2f67
markupsafe==3.0.4 --hash=sha256:8e124f974786f831d6043728e38296969d3579db8896fe004682f5758e613581
mpmath==1.3.0 --hash=sha256:a0b2b9fe80bbcd81a6647ff13108738cfb482d481d826cc0e02f5b35e5c88d2c
networkx==3.7 --hash=sha256:e3fd2c13a7814cee3746340d8d7f8598a67f16a58bf47fb7f8793fab6efca1b0
numpy==2.5.3 --hash=sha256:b7e18c623bb5c95acb3b3328861272816ba199fb531921c5d6d0b675f1fde9e3
packaging==26.3 --hash=sha256:d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c
pocket-tts==3.3.0 --hash=sha256:77b2eb5554cb710e92888f84e28816f2361dba075e0ace47108458794db99111
pydantic-core==2.46.5 --hash=sha256:0fc5be0abd4a407e200d844b404e33639a554e7bd0d448e7b9ae181be4789ac2
pydantic==2.13.5 --hash=sha256:346a034f080da3755d8e9cb5e00e8b07de1d39e4f6e2c87d8ab7cafa0b269a73
pyyaml==6.0.3 --hash=sha256:ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc
requests==2.34.2 --hash=sha256:2a0d60c172f83ac6ab31e4554906c0f3b3588d37b5cb939b1c061f4907e278e0
safetensors==0.8.0 --hash=sha256:fd6f3f93c9a0a7cc2788ee63fb763353d4bd2e89b0751bc78fcf7dda00bea774
scipy==1.18.1 --hash=sha256:f55fa87b6c612ecd6b058f167c53231b1d14e412efe361d3d6e38b3631c73218
sentencepiece==0.2.2 --hash=sha256:c8a168b040bc61681293f79a949b5d911c8e25086f4260285b8d97ab5f1195da
setuptools==84.0.0 --hash=sha256:51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670
sympy==1.14.0 --hash=sha256:e091cc3e99d2141a0ba2847328f5479b05d94a6635cb96148ccb3f34671bd8f5
tokenizers==0.23.2 --hash=sha256:41c2f84d172449b4dadb9cdc508e3e364076613c35b16e76ecfe47a60d1e3305
torch==2.14.1+cpu --hash=sha256:5a6363570c753812540a05eb82380e329469cbe668643e88111414c12627711f
tqdm==4.70.1 --hash=sha256:c293e525e6fef9c20e8728fd4612df02a0aa31bb5fe91ecd93e123b1b7bffa73
typing-extensions==4.16.0 --hash=sha256:481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8
typing-inspection==0.4.4 --hash=sha256:65b8397ba37ccbce054456aaccddfc91e6e3083c92824df348d96ca832f3f147
urllib3==2.8.0 --hash=sha256:0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3
```

The hashes are for **Linux x86_64 / CPython 3.12 only**: the measurement runs in the container on both machines.
`pip check` will report pocket-tts's unused CLI dependencies (fastapi, uvicorn, typer, python-multipart, einops) as
missing; that is expected and not a failure (same as voxcpm in the Freya plan).

Dockerfile body (**not built**; the measure card puts it under `tools/tts-measure/pocket-tr/` with its own
`synthesize.py` in the Antalia shape, builds with `--no-cache` and proves it with `docker run`, never the build log):

```dockerfile
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-deps --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY pocket-tr.yaml synthesize.py /opt/pocket-tr/
ENV HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HOME=/tmp/hf HOME=/tmp KPOCKET_TTS_ERROR_WITHOUT_EOS=1 OMP_NUM_THREADS=4
RUN useradd --uid 10001 --no-create-home pocket
USER pocket
WORKDIR /opt/pocket-tr
RUN python -c "import torch, pocket_tts, sentencepiece, yaml; from pocket_tts import TTSModel; print('IMPORT_OK', torch.__version__, torch.cuda.is_available(), pocket_tts.TTSModel.__name__)"
```

`HOME=/tmp` because `make_cache_directory` (`utils/utils.py:54-57`) writes `~/.cache/pocket_tts` for http(s) paths;
we have none, but on a read-only root it must never be the reason a run fails. `KPOCKET_TTS_ERROR_WITHOUT_EOS=1`: §4.
The selfcheck adds the §2.4 load line under `--network none`. Image size: **not built**, not measured.

## 4. How synthesis is called (pinned `v3.3.0` / wheel 3.3.0)

- **Load once:** `model = TTSModel.load_model(config="/models/pocket-tr/pocket-tr.yaml")` (`tts_model.py:284-388`).
  Defaults kept: `temp=None` → the config's `default_temperature: 0.3` (`:370-371`); `sampler_decode_steps=1`
  (`DEFAULT_SAMPLER_DECODE_STEPS`, `default_parameters.py:5`; one LSD step); `noise_clamp=None`;
  `eos_threshold=-4.0`; **`quantize=False`** (fp32; `quantize=True` is a later, separately labelled row at most).
- **Threads — a trap:** importing `pocket_tts` runs `torch.set_num_threads(1)` at module level (`tts_model.py:57`).
  The measure script must call `torch.set_num_threads(N)` **after** `import pocket_tts`, set `OMP_NUM_THREADS=N`
  and pass `--cpus N`; record N with every number (as Freya §4: CPX32 proxy N=4; home PC N=4 and N=8). The
  code also runs two Python threads per sentence — the latent generator (`:854-855`) and the Mimi decoder
  (`:763-770`) — which is the README's "Uses only 2 CPU cores".
- **Voice prompt, once, at load:** `voice = model.get_state_for_audio_prompt(Path("/models/voices/Selfie.wav"))`
  (`tts_model.py:913-1038`): it reads the WAV (`:1011`), resamples to 24 kHz mono (`convert_audio`, `:1019-1021`),
  encodes it with Mimi (`_encode_audio`, `:1023-1024`), prepends `bos_before_voice` (`:1026-1027`) and runs the flow
  LM over the prompt (`:1031-1032`). This is the expensive embedding step and it happens **once, outside every
  sentence's time**. Record its wall time as its own field (`voice_prompt_ms`), never inside a sentence. Pass a
  `Path`, not a `str`: a `str` goes through `download_if_necessary` (`:1007-1008`) — harmless for a local path, but the
  `Path` makes the "no download" rule visible.
- **The streaming call (the one to use):**
  `TTSModel.generate_audio_stream(self, model_state, text_to_generate, max_tokens=MAX_TOKEN_PER_CHUNK, frames_after_eos=None, copy_state=True, stop=None) -> Iterator[torch.Tensor]`
  — `tts_model.py:634-643`. It splits the text into pieces of ≤ 50 tokens (`split_into_best_sentences`, `:706-715`;
  `MAX_TOKEN_PER_CHUNK = 50`, `default_parameters.py:10`) and for each piece `yield from
  self._generate_audio_stream_short_text(...)` (`:732-738`). Inside, the generator thread puts one latent per step on
  a queue (`:891`), the decoder thread decodes whatever is queued (`:515-539`) and puts audio on the result queue
  (`:552`), and the caller's generator **yields each chunk as soon as it is decoded: `yield audio_chunk[0, 0]`
  (`:791`)**, before the sentence is finished. `generate_audio` (`:567-632`) is the same stream collected into one
  tensor (`:623-632`); do not use it for timing. `copy_state=True` (the default) deep-copies the voice state per piece
  (`:749-750`), so the one voice state is reused unchanged across all twenty sentences; that copy is real per-sentence
  cost and stays inside the timing.
- **Chunk size:** one Mimi frame = 24 000 / 12.5 = **1 920 samples = 80 ms**. The first chunk is always one frame
  ("The first frame never waits", `:518-519`; `max_decoder_frames_per_call = 0` at `:110` means "decode every queued
  frame in one call"), later chunks are whole multiples of 1 920 when the decoder falls behind. The smoke run printed
  `CHUNKS 19 first 1920 all [1920, …] total 36480` for "Sabah raporunu oku." (1.52 s of audio).
- **Sample rate: 24 000 Hz** (`config.yaml` `mimi.sample_rate: 24000`; `model.sample_rate`, `:129-131`; card
  "Audio sample rate: 24 kHz"; smoke `sr 24000`). Confirms the proposal. Chunks are float32 1-D tensors.
- **Seed:** there is **no seed argument**. The only randomness on the generation path is the flow noise,
  `torch.nn.init.normal_(noise, …)` on torch's global CPU generator (`flow_lm.py:156-162`), drawn in the generator
  thread. Call `torch.manual_seed(seed)` immediately before each sentence's `generate_audio_stream`. The smoke run did
  this twice with the same seed and got bit-identical audio (`DETERMINISTIC True torch.float32`). One fixed seed for
  the whole run (the bench's per-engine `Seed`, `tts-measure.ps1:65-71`), recorded in the evidence.
- **Precision:** fp32 on CPU. Every tensor in the file is F32 (§2.3), `flow_lm.dtype: float32` and `mimi.dtype:
  float32` in the config; the transformer output is cast `.to(torch.float32)` (`flow_lm.py:150`). The smoke printed
  `dtype torch.float32`. No autocast.
- **Time to first chunk, measured correctly:** `generate_audio_stream` is a **generator**: calling it runs nothing.
  Take `t0 = time.perf_counter()`, then `it = model.generate_audio_stream(voice, text)` and `first = next(it)`;
  `ttfc_ms = (perf_counter() - t0) * 1000`. That wall time includes text splitting, tokenisation, the state copy, KV
  expansion, text prompting (`:836-837`), the first latent and the first Mimi decode — everything the owner would
  wait for. Then drain `it` and take `total_ms`, `audio_ms = samples / 24 000 × 1000`, and the RTF by the bench's
  existing definition (`app/voice/tts_measure.py`; wall over audio, as the Antalia row). The voice embedding is never
  in it (above). Run **one untimed warm-up sentence** (not one of the twenty) after load, reported separately, because
  the first call pays allocator and thread start-up. Also record the TTFC of each later piece of a multi-piece
  sentence if the bench wants it; the one the owner hears is the first.
- **Failures are failures:** `KPOCKET_TTS_ERROR_WITHOUT_EOS=1` (baked in) makes "Maximum generation length reached
  without EOS" raise instead of only logging a warning and truncating (`tts_model.py:895-901`; upstream issue #113:
  "the final 'test' is skipped in output"). The exception travels through the result queue to the caller
  (`:839-852`, `:795-800`) — a FAILED sentence with its reason, never a silent cut.
- **Interruption hook (for the later wiring, not measured):** `stop: threading.Event` (`:642`, `:665-667`, checked
  at `:718` and `:875`) ends the stream early — the "dur" the proposal wants to matter.
- **Input text:** the twenty `OWNER_SENTENCES` go in raw. The code only applies its light text preparation
  (`prepare_text_prompt`: capital first letter, terminal punctuation; `config.py:121-134` defaults). No number or date
  normalisation (card: "numbers and dates when appropriately normalized"; upstream issues #162, #216).

## 5. The voice prompt

The model clones the voice of a recording; it has no voice of its own (`weights_path_without_voice_cloning: null`,
and predefined embeddings are refused for custom weights, §1.1). Rule from the proposal, binding: **YALNIZ sahibin
kendi sesi ya da açıkça izinli bir ses.** Never a third person's voice, never a clip from the training dataset
(`serdarcaglar/turkish-tts-audiobooks` or anything derived from it), never a voice from the internet.

- **The ONE permitted default voice for the measurement:** `kyutai/tts-voices` @
  `323332d33f997de8394f24a193e1a76df720e01a`, file **`voice-donations/Selfie.wav`**, sha256
  **`076968c3122520f3412eb7090e8c1c3f75fe57be1e24a2f96465583d84c71e16`**, 480 044 bytes. It is Kyutai's predefined
  Pocket TTS voice **"marius"** (`utils/utils.py:21`: `"marius": "hf://kyutai/tts-voices/voice-donations/Selfie.wav"`).
  - Licence: **CC0** (voices README lines 20-22).
  - Consent statement: "Voices of volunteers submitted through our Unmute Voice Donation Project, licensed as CC0. …
    collected 228 voices that passed verification" (voices README lines 22-23), and the Unmute README: "From June 2025
    to February 2026, we also ran the Unmute Voice Donation Project, where volunteers provided their voices for use
    with Kyutai TTS 1.6B (used by Unmute) and other open-source TTS models" (https://github.com/kyutai-labs/unmute ,
    README lines 219-220). The donor gave the voice for exactly this kind of use. The donation page itself
    (https://unmute.sh/voice-donation) renders in JavaScript and its terms could not be read as text; the two
    statements above are Kyutai's word.
  - Why not Kyutai's default for custom models (`alba-mackenna/casual.wav`, `default_parameters.py:58-62`): it is
    CC BY 4.0 and a credited voice actor, but the README gives a licence only, no consent statement for cloning.
    Not chosen. Every other folder (expresso, ears = CC-BY-NC; vctk, cml-tts = dataset speakers) is excluded.
  - Format, read from the file: RIFF WAV, PCM 16-bit, **mono, 24 000 Hz, 10.0 s** (so no resampling happens).
  - It is an English speaker, so the Turkish output will carry an English-speaker timbre; that is a property of the
    measurement, written in the evidence, not hidden.
- **The only other input: the owner's own voice**, as a file path, later (the owner's choice, never a default). The
  file must be:
  - **WAV, PCM signed 16-bit** (`sampwidth == 2`): read with the stdlib `wave` module (`data/audio.py:28-40`). Any
    other WAV width or format falls to `soundfile` (`:34-35`, `:42-55`), which is **not installed** (§3) and raises
    `ImportError` — a FAILED run with that reason, by design.
  - **Mono** preferred (stereo is averaged, `:38-39`).
  - **Any sample rate**, resampled to 24 kHz by `convert_audio` (`tts_model.py:1019-1021`); 24 000 Hz avoids it. The
    measurement-recording page writes **WAV PCM 16-bit mono 16 000 Hz, ≤ 30 s** (`team/plans/measure-recordings-api-adr.md`
    line 11), so a take from it is accepted as is. Note for the evidence: a 16 kHz prompt has no content above 8 kHz,
    and the browser's echo cancellation / noise suppression were on (`measure-recording-page-adr.md` decision 1).
  - **Length 5-10 s of clean speech by one person**, ending on a short pause. The Turkish recipe trained with
    `max_voice_prompt_sec: 5.0` (`training/tr_24l_teacher.yaml` line 13); long prompts are a known failure in older
    releases (issue #65: an over-long cloning prompt crashed with a tensor-size error). Never above 30 s (the code's
    own `truncate` limit, `tts_model.py:1013-1017`).
  - It stays on the machine: mounted read-only into the `--network none` container, never uploaded, never put in the
    evidence files (only its sha256 and its duration are).

## 6. Device safety and security

- **Separate process, separate image.** The model runs only in `pagentos-pocket-tr-measure` (later, a provider image
  of its own). The API image never gets torch or pocket-tts: CLAUDE.md voice rule (narration TTS is its own
  subsystem). `services/api` dependencies are unchanged by this work.
- **Exactly as `scripts/voice/tts-measure.ps1` applies them** for FreyaTTS and Antalia (the measure card adds
  `pocket-tr` to `[ValidateSet("freya", "antalia")]` at line 41 and to `$engines` at lines 65-71):
  - Host allow-list before anything touches docker: `[string[]]$AllowedHosts = @("MAIL", "pagentos-core")` (line 54),
    compared with `$env:COMPUTERNAME` / `[Environment]::MachineName` (line 145); otherwise exit 4 "never run this on
    the office PC" (lines 146-147).
  - The fill is the only networked run (lines 194-198, exit 3 on failure); it fetches the four files of §2.5/1 by
    fixed URL and checks sha256.
  - Synthesis: `--network none --read-only --tmpfs /tmp --memory $MemoryLimit --memory-swap $MemoryLimit` (default
    `8g`, line 49; no swap) and `-e OMP_NUM_THREADS=$Threads` (lines 207-208), `--cpus` when given (line 209), the
    weights volume mounted `:ro` (line 211), uid 10001 from the image (the host uid on Linux, line 210). Line 212 passes
    `--steps 32`, which means nothing to this engine (one LSD step, §4): the measure card makes the synth arguments
    per engine.
- **Every loaded file, its format:**
  - `model.safetensors`: **safetensors** (8-byte length 42 032 + JSON + raw F32 tensors), loaded with
    `safetensors.torch.load_file` (`tts_model.py:267`). No code runs on load.
  - `tokenizer.model`: **SentencePiece model (protobuf)**, loaded by `sentencepiece.SentencePieceProcessor(path)`
    (`text_conditioner.py:29-30`). Not a pickle; a parser, no code execution.
  - `pocket-tr.yaml`: **YAML**, `yaml.safe_load` (`config.py:152-153`), then pydantic `extra="forbid"`. No object
    construction from tags.
  - `Selfie.wav` / the owner's WAV: **PCM WAV**, stdlib `wave` (`data/audio.py:30-40`). No code.
  - The voice-prompt state: **computed in memory** at load (§4). If the measure card caches it, only through
    `export_model_state` / `_import_model_state`, which are **safetensors** (`model_state.py:15`, `:32-36`
    `safetensors.safe_open`).
  - **No pickle is loaded on our path.** The package's only `torch.load` is for training checkpoints
    (`weights_loading.py:93`, `weights_only=True`), reached only through `load_model(checkpoint=…)`; never pass
    `checkpoint=`. The container never sets `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD`.
- **Open issues read (2026-10-07):**
  - `kyutai-labs/pocket-tts`: **53 open issues + 4 open PRs** (GitHub API `open_issues_count` 57 counts both; the
    proposal's 53 is the issues). Relevant ones:
    - **#218** "Add SECURITY.md with vulnerability reporting guidance": the project has **no security policy / private
      disclosure path**. Mitigation: pinned bytes, no network, read-only, non-root; re-read the issue list before any
      wiring.
    - **#113** "Generation stops mid-sentence with 'Maximum generation length reached without EOS'" (the last word is
      dropped): handled by `KPOCKET_TTS_ERROR_WITHOUT_EOS=1` (§4) → FAILED, not silent.
    - **#347** "Phrase at end of sentence gets repeated" and **#221** "intermittent inserted/repeated words with
      exported voice profiles": correctness; the owner's listening step must check sentence ends. Not measurable by
      timing.
    - **#91** "First word is garbled", **#70** "Initial word gets missed", and the Turkish card's own "Beginning-of-generation
      artifact … around the first syllable" (card lines 79-83). Upstream's chunk-start fade-ins (#332/#333/#335) and
      open PR **#331** "Fix onset click: drop the first decoded frame of each chunk" are **after v3.3.0 / not merged**:
      not in our pin. The listening step judges the first syllable explicitly.
    - **#346** "Text with en or em dashes causes German generation to cut off": check whether any OWNER_SENTENCE holds
      "–"/"—"; if so, its result is read with this in mind (input goes in raw, no rewriting).
    - **#162** "tokenizer incorrectly splits decimals", **#216** "articulation of numbers": number reading is weak;
      numbers in the twenty sentences are judged by ear.
    - **#65** long cloning prompt → tensor-size `RuntimeError`: the 5-10 s rule in §5.
    - **#229** WAV from `serve` has placeholder RIFF sizes: we never run `serve`.
    - **#215** "Feature Request: Turkish Language Support": open — there is no official Turkish model.
    - The rest are feature requests, ports and community announcements, not on our path.
  - `kaanhgunay/pocket-tts-tr`: **0 discussions** (HF API `discussions`, count 0). HF API `downloads: 0`.
  - Sources: https://api.github.com/repos/kyutai-labs/pocket-tts/issues?state=open&per_page=100 ,
    https://huggingface.co/api/models/kaanhgunay/pocket-tts-tr/discussions .
- **Not thread-safe** (`tts_model.py:581-582`, `:649-650`): one model instance, one sentence at a time.
- **Where it may run:** the home PC (`MAIL`) and the Cloud Core (`pagentos-core`) only, through the allow-list.
  **Never the office PC** (employer machine). On the Cloud Core it competes with the live API for 4 vCPU: outside the
  owner's active hours, `--cpus` capped, one run at a time.
- **KVKK:** the default voice is a CC0 donation; the owner's voice, if used, never leaves the machine (§5). The output
  is synthetic speech in a cloned voice: never presented as a real recording (Kyutai's prohibited-use text, §1.3).

## 7. Memory and CPU — what the cards say, and what is not known

Published (not ours):
- Base model README (`kyutai/pocket-tts` README @ `3e82814a…`, lines 43-48): "Runs on CPU", "Small model size, 100M
  parameters", "Low latency, ~200ms to get the first audio chunk", "~6x real-time on a CPU of MacBook Air M4",
  "Uses only 2 CPU cores"; line 215-219: "measured on a cloud x86 VM (4 vCPUs) … RTF ~2.3-2.5x on CPU". Line 76:
  the 24-layer variants "are higher quality but slower".
- Turkish card (README @ `e5aa490d…`): "Pocket TTS Turkish — v0.1 Base", "Turkish 24-layer Pocket TTS teacher
  model", "Training checkpoint selected: 112,000 steps", "Generation temperature: 0.3", "intended … as a base
  checkpoint for further fine-tuning and depth distillation". **No quality figure, no speed figure, no memory
  figure.** HF API `downloads: 0`. The dataset card gives 2,724 hours (card line 96, dataset title).
- Limitation, verbatim (card lines 71-75): "English and other foreign-language words may be pronounced according
  to Turkish orthographic and phonetic patterns. Code-switching is **not considered a supported capability in
  v0.1**." Plus the beginning-of-generation artifact (§6).

**None of the published numbers is a measurement of this checkpoint.** They are for the 100 M / 6-layer model
(English), on Apple Silicon or an unnamed x86 VM. This checkpoint has **336 M parameters and 24 layers** (§2.3):
about 3.4× the parameters, and the 24-layer variants are slower by Kyutai's own words. Weights in RAM: ≥ 1.34 GB
(fp32), plus the KV cache and the voice state. Resident memory, time to first chunk, RTF and the peak on the home PC
(i7-14700KF) and the CPX32 proxy (`cpx32-bicimi`, 4 vCPU) are exactly what `tts-pocket-tr-measure` produces. The
smoke run in §2.4 recorded no time and no memory and is not evidence of either. The 8 GB ceiling stays; it is not
lowered on a guess.

## 8. THIRD_PARTY_COMPONENTS entry (the lead copies this; the integrator does not edit the shared file)

```markdown
## Pocket TTS Turkish (local streaming Turkish TTS, third candidate) — MEASUREMENT ONLY

Role: candidate local streaming TTS behind `TTSProvider` (not wired; measurement card `tts-pocket-tr-measure`).

- Code: `pocket-tts` 3.3.0 (PyPI wheel sha256 `77b2eb5554cb710e92888f84e28816f2361dba075e0ace47108458794db99111`
  = `kyutai-labs/pocket-tts` tag v3.3.0 @ `3dbee45d343d7dddd0d105468d17f8dcba14db3e`) — MIT
  (https://github.com/kyutai-labs/pocket-tts/blob/3dbee45d343d7dddd0d105468d17f8dcba14db3e/LICENSE). Installed
  `--no-deps`; the Mimi codec is part of this package (no `moshi` dependency).
- Weights: `kaanhgunay/pocket-tts-tr` @ `e5aa490d9aa6075cf047e4286fb57cecb99aa1ed` — `model.safetensors`
  (sha256 `575d7a90e41de407340e8048e6c1b3d6573c36ab9054e1233d3cb0d6777eaf5f`, 336 068 290 F32 params, flow LM +
  Mimi codec), `tokenizer.model` (`c9ff291390865a78112e23b8ded6842300c6a88a58b0ef9694d6e8282f4ea490`) — CC-BY-4.0
  (https://huggingface.co/kaanhgunay/pocket-tts-tr/blob/e5aa490d9aa6075cf047e4286fb57cecb99aa1ed/README.md).
- Attribution (CC BY 4.0 §3(a)): "Pocket TTS Turkish — v0.1 Base" by kaanhgunay,
  https://huggingface.co/kaanhgunay/pocket-tts-tr , licensed CC BY 4.0
  (https://creativecommons.org/licenses/by/4.0/), provided "as is" without warranties; derived from Kyutai Pocket TTS
  (https://huggingface.co/kyutai/pocket-tts , CC BY 4.0); training data: Serdar I. Çağlar, "Turkish TTS Audiobooks:
  a 2,724-hour Turkish read-speech corpus for text-to-speech", Hugging Face, 2026
  (https://huggingface.co/datasets/serdarcaglar/turkish-tts-audiobooks). Modified: the config's two `hf://…@v0.1-base`
  paths are rewritten to local pinned files; weights unmodified.
- Training-data risk: the checkpoint was trained on audiobook recordings whose rights the dataset's publisher says
  were never cleared (`license: other`); wiring it as an everyday voice is a separate owner decision that weighs this.
- Voice prompt: `kyutai/tts-voices` @ `323332d33f997de8394f24a193e1a76df720e01a`, `voice-donations/Selfie.wav`
  (sha256 `076968c3122520f3412eb7090e8c1c3f75fe57be1e24a2f96465583d84c71e16`) — CC0, Unmute Voice Donation Project;
  or the owner's own recording. No third person's voice.
- Runtime: own container, python:3.12-slim-bookworm (digest-pinned, the Antalia/FreyaTTS base), torch 2.14.1+cpu,
  hash-locked requirements in `team/plans/tts-pocket-tr-integration-plan.md` §3; no LGPL components.
- Where it runs: home PC and Cloud Core only (`MAIL`, `pagentos-core` allow-list), `--network none` at synthesis;
  never the office PC; never in the API image.
- Why: the first STREAMING local Turkish voice candidate (24 kHz, 80 ms chunks) measured beside FreyaTTS and Antalia
  (master checklist 224/414/234/267); never fine-tuned or distilled here (the dataset's open-weights condition).
```

## Later card (not planned here): wiring into `TTSRouter`

A separate owner-approved card, taken only after the owner weighs §1.4, would add a local streaming provider behind
`TTSProvider` (`services/api/app/voice/providers.py`) that talks to the pocket-tr container over local HTTP/WebSocket
— no torch in the API — registered behind the paid provider in `TTSRouter` (`services/api/app/voice/router.py`) with a
setting that defaults OFF. It would have to carry the 80 ms chunks to the player instead of one finished WAV, wire
the owner's "dur" to the `stop` event (§4), keep one model instance per sentence at a time, show the §8 credit line,
and keep this voice off any outbound call or message path. None of that is in this plan.
