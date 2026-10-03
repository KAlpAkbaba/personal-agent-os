# tts-freya-integration-plan — FreyaTTS-small, integration plan (cycle d20261003)

Proposal: `team/proposals/2026-10-03-yerel-turkce-ses-freyatts.md`. Scope: **measurement only**. The worker of
`tts-freya-measure` builds from this file verbatim. Everything below was read on **2026-10-03** from the
sources named next to each claim; the pinned hashes were re-computed, not copied (the two weight files were
downloaded at the pinned revisions and hashed, then deleted). No STOP is raised for the three model pieces;
two dependency STOP-CHECK lines for the lead are in §1.3.

## Şu an üzerinde çalışılan (for the lead's HANDOFF)

- Task: `tts-freya-integration-plan` — FreyaTTS integration plan (licence, pins, CPU runtime, safety)
- Area: `team/plans/tts-freya-integration-plan.md` (only this file)
- Machine: home PC (build PC), worktree `.claude/worktrees/team/d20261003/worker-tts-freya-integration-plan`

## 1. Licences

### 1.1 The three pieces the model needs

| Piece | Licence | Read from (the actual file) | Verdict |
|---|---|---|---|
| Code `freyavoiceai/FreyaTTS` @ `146d36c1cb6660646be57d31339db4eed9315de3` | **Apache-2.0**, full text, appendix "Copyright 2026 Freya Voice AI" (LICENSE lines 2, 190-192) | https://github.com/freyavoiceai/FreyaTTS/blob/146d36c1cb6660646be57d31339db4eed9315de3/LICENSE ; README "FreyaTTS-small weights and code are released under the Apache-2.0 license" | OK |
| Weights `freyavoice/Freya-TTS` @ `d124e07493615208f58bdd21d432736849ee4230` | **Apache-2.0** — model-card front matter `license: apache-2.0` (README.md line 4) and "**License:** Apache-2.0" (line 22). The HF repo has **no LICENSE file** (its files: `.gitattributes`, `README.md`, `config.json`, `model.safetensors`); the card metadata is the licence grant, confirmed by the code repo's README and by the paper ("We release the model weights, training and inference code, and evaluation benchmark under the Apache-2.0 license") | https://huggingface.co/freyavoice/Freya-TTS/blob/d124e07493615208f58bdd21d432736849ee4230/README.md ; https://arxiv.org/abs/2607.09530 (abstract) | OK |
| AudioVAE2 weights `openbmb/VoxCPM2` @ `32279effe8c19989596f05d353d1447f51d9e915` (only `audiovae.pth` is loaded) | **Apache-2.0** — card front matter `license: apache-2.0` (README.md line 33) and "## License … Released under the Apache-2.0 license, free for commercial use" (lines 224-226). No LICENSE file in the HF repo either. | https://huggingface.co/openbmb/VoxCPM2/blob/32279effe8c19989596f05d353d1447f51d9e915/README.md | OK |
| `voxcpm` 2.0.3 (the Python class that decodes AudioVAE2) | **Apache-2.0** — wheel METADATA `License-Expression: Apache-2.0`; GitHub LICENSE is the Apache 2.0 text (repo HEAD `f0c787f0937dc1c9a8f4f64d9a332d9c5da2e629`) | https://pypi.org/project/voxcpm/2.0.3/ ; https://github.com/OpenBMB/VoxCPM/blob/f0c787f0937dc1c9a8f4f64d9a332d9c5da2e629/LICENSE | OK |

Not a licence matter but written down: neither card names the training dataset or whose voice "Leyla" is
("from scratch on Turkish speech … single target speaker"). Open question for the owner's listening step,
not a STOP.

### 1.2 Runtime dependencies (the §3 lock) — all permissive except the two below

Apache-2.0: hf-xet, huggingface-hub, msgpack, requests, safetensors, tokenizers, transformers, voxcpm;
packaging (Apache-2.0 OR BSD-2); regex (Apache-2.0 AND CNRI-Python); llvmlite (BSD-2 AND Apache-2.0 WITH
LLVM-exception). BSD: torch, torchaudio, numpy (BSD-3 AND 0BSD AND MIT AND Zlib AND CC0), scipy, numba,
scikit-learn, click, cloudpickle, decorator, fsspec, httpcore, httpx, idna, jinja2, joblib, lazy-loader,
markupsafe, mpmath, networkx, pooch, pycparser, pygments, sympy, threadpoolctl, soundfile (the Python part).
MIT / MIT-0: annotated-doc, annotated-types, anyio, cffi, charset-normalizer, einops, filelock, h11,
markdown-it-py, mdurl, narwhals, platformdirs, pydantic, pydantic-core, pyyaml, rich, setuptools, typer,
typing-inspection, urllib3. ISC: librosa, shellingham. PSF-2.0: typing-extensions.
MPL-2.0: certifi, tqdm (MPL-2.0 AND MIT) — both already in `services/api/uv.lock`, so already accepted.
Source: each package's PyPI JSON `license_expression` / `license` / classifiers, https://pypi.org/pypi/<name>/<version>/json.

### 1.3 STOP-CHECK lines for the lead (dependencies, not the model)

- **STOP-CHECK (lead): `soxr` 1.1.0 is LGPL-2.1-or-later** (PyPI `license_expression`, https://pypi.org/pypi/soxr/1.1.0/json). Pulled by librosa. Used unmodified, dynamically, inside a private container that is never distributed. Precedent: `fpdf2` (LGPL-3.0) is already a runtime dependency (`docs/THIRD_PARTY_COMPONENTS.md` line 61).
- **STOP-CHECK (lead): the `soundfile` 0.14.0 manylinux wheel bundles `libsndfile_x86_64.so` under LGPL-2.1** (`_soundfile_data/COPYING` in the wheel; `licensing/license_notes.md`: "links against media libraries that are licensed under a mixture of LGPL and BSD licenses"). https://github.com/bastibe/python-soundfile. Same reasoning and precedent.
- The model pieces themselves: **no STOP** (all four Apache-2.0, all readable).

## 2. The exact pin

### 2.1 Code

- Repository: https://github.com/freyavoiceai/FreyaTTS — commit **`146d36c1cb6660646be57d31339db4eed9315de3`**
  (2026-08-03, "Merge pull request #2 … fix/windows-utf8-json-encoding"; the repo's whole history is 9 commits).
- Clone with `git -c core.autocrlf=false clone …` then `git checkout 146d36c1…` — on this machine
  `core.autocrlf=true` turns the files CRLF and their hashes change. sha256 of the **committed blobs**
  (`git cat-file blob 146d36c1:<path> | sha256sum`):

| File | sha256 |
|---|---|
| `freyatts/__init__.py` | `acb2b9ee509c3d4010751fd329f7926b584b8bdaa4060cd2f3c80a07bb95f824` |
| `freyatts/model.py` | `f4bec78eaa413381e984f6f661333dd89480296fdc6b5d8e995451b1f1080cd8` |
| `freyatts/pipeline.py` | `bade3e25366321121826a7d19cc4ef352d0534e2255ebec721dfab118edb31b8` |
| `freyatts/vae.py` | `8aeff5ce4a27d7f0434b9323ab25b7fb1c83560128cb725d28e2126da60cd640` |
| `freyatts/char_vocab.json` (loaded, pipeline.py:161-163) | `f69a8f2abea4ec09535b92b1109d7dce743472b89e7cc6ab218cd3906781340c` |
| `LICENSE` | `e58b88a04502b355f2f01cd2e3935adca39e7523c91463175169c400729964b2` |

### 2.2 Weights — every file the model loads

| Repository @ revision (commit sha) | File | Bytes | sha256 (downloaded and hashed 2026-10-03; equals the HF LFS record) |
|---|---|---|---|
| `freyavoice/Freya-TTS` @ **`d124e07493615208f58bdd21d432736849ee4230`** (lastModified 2026-07-27) | `config.json` | 92 | `898c9a951a20f960e0936d4e222c2a4044d383f5691efa1a614d812812956a67` |
| same | `model.safetensors` | 732 811 956 | `9e5828ce9eb6aaf197adc5cb098e2e80e8ff301d238add631418c5557fa08b22` |
| `openbmb/VoxCPM2` @ **`32279effe8c19989596f05d353d1447f51d9e915`** (lastModified 2026-08-18) | `audiovae.pth` | 376 951 122 | `94b5d51e107e0507d4acc976cfdadb64edd6fd06d1f751dadbf2fd1594274bf1` |

`config.json` content at that revision: `{"vocab": 92, "d": 640, "depth": 16, "heads": 10, "ff": 2048, "arch": "xattn"}`.
Nothing else from VoxCPM2 is loaded (its 4.58 GB `model.safetensors`, tokenizer files etc. are never fetched).
Both repos are public, not gated (HF API `gated: false`) — no token needed. Source:
https://huggingface.co/api/models/freyavoice/Freya-TTS?blobs=true , https://huggingface.co/api/models/openbmb/VoxCPM2?blobs=true

### 2.3 How the loader is forced onto the pin — the upstream loader CANNOT be

The pinned code has **no revision argument anywhere**:

- `freyatts/pipeline.py:143-144` — `hf_hub_download(model_id_or_path, "config.json")` /
  `hf_hub_download(model_id_or_path, "model.safetensors")`: no `revision=`.
- `freyatts/vae.py:23` — `hf_hub_download("openbmb/VoxCPM2", "audiovae.pth", token=token or os.environ.get("HF_TOKEN"))`:
  repo hard-coded, no `revision=`.
- `pipeline.py:159` — `from_pretrained` **always** calls `load_audio_vae(device)`, even for a local directory.

huggingface_hub 1.33.0 turns a missing revision into `"main"` (`file_download.py:990-991`,
`revision = constants.DEFAULT_REVISION`). With `HF_HUB_OFFLINE=1` (`constants.py:194`) every HTTP request
raises `OfflineModeIsEnabled` (`utils/_http.py:283-286`) and the cache lookup reads
`refs/<revision>` (`file_download.py:1141-1152`); a download made **by commit sha** writes no `refs/main`
(`file_download.py:727-733`: "Does nothing if `revision` is already a proper `commit_hash`"). So
`FreyaTTS.from_pretrained` with a sha-pinned cache and offline mode **fails** (`LocalEntryNotFoundError`), and
online it silently takes whatever `main` is. Therefore — binding for the measure card:

1. **Fill step (network allowed, separate run):** fetch the three files by their pinned URLs,
   `https://huggingface.co/freyavoice/Freya-TTS/resolve/d124e07493615208f58bdd21d432736849ee4230/{config.json,model.safetensors}`
   and `https://huggingface.co/openbmb/VoxCPM2/resolve/32279effe8c19989596f05d353d1447f51d9e915/audiovae.pth`,
   into a models directory, and `sha256sum -c` against §2.2. A mismatch stops the run. No HF cache layout is
   used at all.
2. **Synthesis step:** never call `FreyaTTS.from_pretrained` or `freyatts.vae.load_audio_vae`. Build the
   pipeline through its constructor `FreyaTTS(model, vae, char_to_id, device="cpu")` (`pipeline.py:119`),
   doing by hand exactly what `pipeline.py:146-157` and `vae.py:24-32` do, but from the verified local paths:
   `FreyaDiT(vocab, d, depth, heads, ff)` from `config.json`, `load_state_dict(safetensors.torch.load_file(path), strict=True)`;
   `AudioVAEV2(AudioVAEConfigV2())`, `torch.load(path, map_location="cpu", weights_only=True)`,
   `load_state_dict(ckpt.get("state_dict", ckpt), strict=False)` — and **assert the returned
   `missing_keys` / `unexpected_keys` are both empty** (upstream uses `strict=False` at `vae.py:27`, which would
   hide a wrong file). Then `.float().eval()`, `requires_grad=False`.
3. Still set `HF_HUB_OFFLINE=1` and run with `--network none` (§5) so any forgotten hub call fails loudly
   instead of downloading. Do not pass `HF_TOKEN` into the container (`vae.py:23` reads it).

Nothing is downloaded at synthesis time: proven by construction (no hub call on the path above) and enforced
twice (offline env + no network).

## 3. Runtime

- Python **3.12** (image ships 3.12.15). Base image **`python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3`** (Docker Hub official image, digest read with `docker image inspect` after `docker pull`, 2026-10-03).
- torch **2.11.0+cpu** from the CPU index **`https://download.pytorch.org/whl/cpu`**; torchaudio pinned to the
  same 2.11.0+cpu (torchaudio has no release after 2.11; the first resolve without the pin paired torch 2.14.1
  with torchaudio 2.11 — rejected). transformers resolves to 5.18.0 (voxcpm asks `>=4.36.2`; a resolve without
  that floor fell back to transformers 4.12.2 / tokenizers 0.10.3 — rejected).
- **voxcpm is installed `--no-deps`.** Its declared deps include gradio, funasr, modelscope, datasets,
  matplotlib, spaces, wetext, torchcodec … — none is on the import path FreyaTTS uses. That path is
  `voxcpm/__init__.py` → `core.py` → `model/voxcpm.py`, `model/voxcpm2.py`, `model/utils.py`, whose module-level
  third-party imports are torch, torchaudio, transformers, einops, pydantic, safetensors, tqdm, librosa, numpy,
  huggingface_hub — all in the lock below. `pip check` therefore reports voxcpm's unused extras as missing;
  that is expected and not a failure.
- How it was made: `uv pip compile` (uv 0.12.7) for `--python-version 3.12 --python-platform x86_64-manylinux_2_28`,
  torch/torchaudio from an explicit `pytorch-cpu` index, then `pip download --require-hashes` inside the pinned
  base image and `sha256sum` of the 62 wheels actually fetched — one hash per line, the exact Linux cp312 wheel.

Requirements body (62 lines, every line `==` + `--hash`; install with
`pip install --no-cache-dir --no-deps --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt`):

```text
annotated-doc==0.0.5 --hash=sha256:117bac03a25ede5df5440e855b32d556049ca169ead221505badf432fed4b101
annotated-types==0.8.0 --hash=sha256:f072f4d804ea359e4eaf198b1af7a8b0943881a87f31bb764f8bf219bb9419e0
anyio==4.15.1 --hash=sha256:6152fdbbf9a77fdec97731721bebf7c4c44f7c29b424b0065826173efc7ed101
certifi==2026.7.22 --hash=sha256:62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775
cffi==2.1.1 --hash=sha256:c1453022f490d2459a11819d83ad1d586e9ff65a12ac3e705ffebd46d3685dcf
charset-normalizer==3.5.2 --hash=sha256:3d31298449090ab8d47b7b1b2a555ff73cac7ed438a08b7ac160980c7ebed649
click==8.5.0 --hash=sha256:255bc9599cf7748b4b1a446ccc735421bd08a2ae529a8b88597d3de5664ee360
cloudpickle==3.1.2 --hash=sha256:9acb47f6afd73f60dc1df93bb801b472f05ff42fa6c84167d25cb206be1fbf4a
decorator==5.3.1 --hash=sha256:f47fe6fdbd2edd623ecfe36875d37aba411624e2670dd395dddae1358689bb3c
einops==0.8.2 --hash=sha256:54058201ac7087911181bfec4af6091bb59380360f069276601256a76af08193
filelock==4.0.9 --hash=sha256:9287fd61b99a806e5202be29a83034c0808a1b9830e820537c2f5773981f7eeb
fsspec==2026.9.0 --hash=sha256:8dd6e646e99ea382bd85f97a45e6b526a442d79423a7dc673f1e2756d05fcb5f
h11==0.16.0 --hash=sha256:63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86
hf-xet==1.6.0 --hash=sha256:d62671bb130879cef0ee4c9ebe47a14af6c66ec53e6d84dc15936e5ffdfac82f
httpcore==1.0.9 --hash=sha256:2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55
httpx==0.28.1 --hash=sha256:d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad
huggingface-hub==1.33.0 --hash=sha256:04e434b06e100eddbce9a6e817d72693a7884b10a79bd67ab48080d5c07eb899
idna==3.20 --hash=sha256:ab7ae7122974553370f0bdb919e1a960b2cd1bc1ef0276416d896db81c14582c
jinja2==3.1.6 --hash=sha256:85ece4451f492d0c13c5dd7c13a64681a86afae63a5f347908daf103ce6d2f67
joblib==1.6.0 --hash=sha256:3dbbf9f6e4b592a2357b854608e980fe6390d131d7a82f011a377ef2ebef7aba
lazy-loader==0.6 --hash=sha256:77253be3391b06124a0e16105bd663b6c54470af1a9ca8e1cf026f38d58ed056
librosa==1.0.0 --hash=sha256:5910a6c0e1b2e494b92758c1615a7acbd0515a2315e138927ba2982f2af88857
llvmlite==0.50.0 --hash=sha256:d501e5103076b9a14be885d2574dc2f6793171aa54a853d1244e011d476f1399
markdown-it-py==4.2.0 --hash=sha256:9f7ebbcd14fe59494226453aed97c1070d83f8d24b6fc3a3bcf9a38092641c4a
markupsafe==3.0.4 --hash=sha256:8e124f974786f831d6043728e38296969d3579db8896fe004682f5758e613581
mdurl==0.1.2 --hash=sha256:84008a41e51615a49fc9966191ff91509e3c40b939176e643fd50a5c2196b8f8
mpmath==1.3.0 --hash=sha256:a0b2b9fe80bbcd81a6647ff13108738cfb482d481d826cc0e02f5b35e5c88d2c
msgpack==1.2.3 --hash=sha256:ede33b2892ceb976283e009ad12fa1834cfdf1f9c43ee9c97849fc588d00a618
narwhals==2.26.0 --hash=sha256:29326d74f107c347fd1009bd58e38d9f7c7c5b51e6de97bc93dbc325d9038b54
networkx==3.7 --hash=sha256:e3fd2c13a7814cee3746340d8d7f8598a67f16a58bf47fb7f8793fab6efca1b0
numba==0.68.0 --hash=sha256:51fe913a70fe9a7a0b193757ff977a9e96c82ae936ae388aec8990814fffdf9d
numpy==2.5.3 --hash=sha256:b7e18c623bb5c95acb3b3328861272816ba199fb531921c5d6d0b675f1fde9e3
packaging==26.3 --hash=sha256:d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c
platformdirs==4.12.2 --hash=sha256:29dbf06d96c500bc6bdbce75fb0a14d63279c93b1842f97e72a135b33e856983
pooch==1.9.0 --hash=sha256:f265597baa9f760d25ceb29d0beb8186c243d6607b0f60b83ecf14078dbc703b
pycparser==3.0 --hash=sha256:b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992
pydantic==2.13.5 --hash=sha256:346a034f080da3755d8e9cb5e00e8b07de1d39e4f6e2c87d8ab7cafa0b269a73
pydantic-core==2.46.5 --hash=sha256:0fc5be0abd4a407e200d844b404e33639a554e7bd0d448e7b9ae181be4789ac2
pygments==2.21.0 --hash=sha256:2363c69b61c4a97c838da3b130dcd6468f4848992b21a82f2a63ec34377137d9
pyyaml==6.0.3 --hash=sha256:ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc
regex==2026.9.29 --hash=sha256:39ab5894d971f9ac68baa6eca5c50387db579cfcacf36ae8df3feceb1815e6d0
requests==2.34.2 --hash=sha256:2a0d60c172f83ac6ab31e4554906c0f3b3588d37b5cb939b1c061f4907e278e0
rich==15.0.0 --hash=sha256:33bd4ef74232fb73fe9279a257718407f169c09b78a87ad3d296f548e27de0bb
safetensors==0.8.0 --hash=sha256:fd6f3f93c9a0a7cc2788ee63fb763353d4bd2e89b0751bc78fcf7dda00bea774
scikit-learn==1.9.1 --hash=sha256:e5d7b18a5b9dca241a74695f3275fa4c895a9dadc72b3d8df5fa9d1083c9b83e
scipy==1.18.1 --hash=sha256:f55fa87b6c612ecd6b058f167c53231b1d14e412efe361d3d6e38b3631c73218
setuptools==81.0.0 --hash=sha256:fdd925d5c5d9f62e4b74b30d6dd7828ce236fd6ed998a08d81de62ce5a6310d6
shellingham==1.5.4 --hash=sha256:7ecfff8f2fd72616f7481040475a65b2bf8af90a56c89140852d1120324e8686
soundfile==0.14.0 --hash=sha256:1e38bac1853412871318e82a1ba69a8be677619b56025bbfcccdb41b6cafe82d
soxr==1.1.0 --hash=sha256:3b033078e86f3c4a658e5697fac8995764fad9e799563616b630136b613167f1
sympy==1.14.0 --hash=sha256:e091cc3e99d2141a0ba2847328f5479b05d94a6635cb96148ccb3f34671bd8f5
threadpoolctl==3.7.0 --hash=sha256:cd8b60b5641b45c67bbf73c64c843235fc2d8a480c87389f52f5dbee893b86be
tokenizers==0.23.2 --hash=sha256:41c2f84d172449b4dadb9cdc508e3e364076613c35b16e76ecfe47a60d1e3305
torch==2.11.0+cpu --hash=sha256:f82e2ae20c1545bb03997d1cc3143d94e14b800038669ee1aca45808a9acc338
torchaudio==2.11.0+cpu --hash=sha256:2354248848d06a9ae1e7a12165f800f0dda7df60ecac9fca892322b722b922c0
tqdm==4.70.1 --hash=sha256:c293e525e6fef9c20e8728fd4612df02a0aa31bb5fe91ecd93e123b1b7bffa73
transformers==5.18.0 --hash=sha256:d79e5a515a572ee3deb33eb0d578be47d91e70c75d54cf21bf6eede6edef14a9
typer==0.27.2 --hash=sha256:b3a5fc4342d5fc8fda8fc3010b1cf117e9249aab7fae800c2eff62fd3842d97d
typing-extensions==4.16.0 --hash=sha256:481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8
typing-inspection==0.4.4 --hash=sha256:65b8397ba37ccbce054456aaccddfc91e6e3083c92824df348d96ca832f3f147
urllib3==2.8.0 --hash=sha256:0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3
voxcpm==2.0.3 --hash=sha256:24da58a30d094a9e9a7ead450ae9cffda0d31eaeba620b61ad99179dd87e486b
```

The hashes are for **Linux x86_64 / CPython 3.12 only**; they will not install on Windows (different wheels) —
the measurement runs in the container on both machines (home PC through Docker Desktop/WSL2).

**Image: BUILT** on the home PC, 2026-10-03, from exactly this lock and this Dockerfile body (source copied with
`git archive 146d36c1…`):

```dockerfile
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-deps --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY src/freyatts /opt/freyatts/freyatts
COPY src/infer.py /opt/freyatts/infer.py
ENV PYTHONPATH=/opt/freyatts HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HOME=/models/hf OMP_NUM_THREADS=4
RUN useradd --uid 10001 --no-create-home freya
USER freya
WORKDIR /opt/freyatts
RUN python -c "import torch, freyatts, freyatts.vae, voxcpm.modules.audiovae as a; print('IMPORT_OK', torch.__version__, torch.cuda.is_available(), torch.get_num_threads(), a.AudioVAEV2.__name__)"
```

- Build log, smoke line: `IMPORT_OK 2.11.0+cpu False 4 AudioVAE` (torch CPU build, no CUDA, 4 threads, the
  whole voxcpm import chain loads with transformers 5.18.0).
- Local image id `sha256:15013c5ca5d8f32f4c6909ebe8bc29dadea830662e96aede3a4bef468b4a21c0`, tag
  `pagentos-freya-measure:plan` (not pushed anywhere). **Measured size: 2.25 GB** on disk (`docker image ls`),
  490 388 482 bytes content (`docker image inspect .Size`). Weights not inside (mounted, §5).
- Wheel download inside the base image: 343 MB.
- **Disk warning for the measure card:** C: had 15 GB free before this build and ran to 0 MB transiently during
  it (Docker's WSL disk lives on C:); afterwards 12-13 GB free and the Docker engine stopped answering. Measure
  `df -h /c` first; remove `pagentos-freya-measure:plan` and its build cache when done.

## 4. How synthesis is called (pinned commit)

- Public call: `FreyaTTS.synthesize(self, text: str, steps: int = 32, seed: int = LEYLA_SEED) -> np.ndarray`
  — `freyatts/pipeline.py:167`; returns `wav` from `self._synth(text, steps=steps, seed=seed)` (`pipeline.py:178-179`).
  CLI equivalent: `infer.py:22-24` (`FreyaTTS.from_pretrained(args.model, device=args.device)` →
  `tts.synthesize(args.text, steps=args.steps)` → `tts.save_wav(wav, args.out)`) — **not usable as is**: it goes
  through `from_pretrained` (§2.3) and defaults to `--device cuda` (`infer.py:19`).
- Output: float32 mono numpy array at **48 000 Hz** — `SAMPLE_RATE = 48000` (`pipeline.py:24`), written by
  `save_wav` with `soundfile` (`pipeline.py:181-185`); the VAE's `out_sample_rate: int = 48000`
  (`voxcpm/modules/audiovae/audio_vae_v2.py:367`). Confirms the proposal's 48 kHz.
- Voice / seed: `LEYLA_SEED = 9` (`freyatts/model.py:18`); the seed seeds `torch.Generator(...).manual_seed`
  for the initial noise (`model.py:231-235`). Pass `seed=9` explicitly. Note: README says `seed=None` gives a
  random speaker, but at this commit `_synth` replaces `None` with `self.seed` (`pipeline.py:250`) — `None` is
  also Leyla. Deterministic per text.
- Steps: 32 Euler steps by default (`model.py:236-238`); keep 32 for the measurement (the card numbers use it).
- Precision: fp32 on CPU — the DiT stays in its loaded dtype (safetensors fp32 per card "fp32" figures), VAE is
  forced `.float()` (`vae.py:29`); device `"cpu"` passed to the constructor (`pipeline.py:119`). No autocast.
- Threads: the code never sets them (no `set_num_threads` / `OMP_NUM_THREADS` anywhere in the repo). Set
  `torch.set_num_threads(N)` at start **and** `OMP_NUM_THREADS=N`, and limit the container with `--cpus N`;
  record N with every number (Cloud Core CPX32: N=4; home PC: report N=4 and N=8 so the two machines compare).
- **Not streaming.** `_synth` normalises the text, splits it into clauses of ≤11 words (`pipeline.py:223-247`),
  synthesises each, and returns one concatenated array (`pipeline.py:249-277`). So with the pinned API
  **first-audio latency = whole synthesis time** of the input. A later provider could emit per clause (the
  split exists), but that is not what this code does and the measurement must not claim it.
- Hidden cost to count: after each clause `_voiced_ok` runs librosa `pyin` (`pipeline.py:211-221`) and on a
  "collapse" re-synthesises the clause up to 3 more times with seeds 10-12 (`pipeline.py:263-269`) — that is
  included in wall time, and those retries change the voice; the measure script should count them (wrap
  `_voiced_ok`, never edit the vendor file).
- Text normalisation is built in (`normalize`, `pipeline.py:98-108`: digits spelled out, a few brand words);
  ours (ADR-0242 sentences) goes in raw.

## 5. Device safety and security

- **Separate process, separate image.** The model runs only in `pagentos-freya-measure` (later, a provider
  image of its own). The API image never gets torch: CLAUDE.md voice rule (narration TTS is its own subsystem);
  `services/api` dependencies are unchanged by this work.
- **No network at synthesis:** `docker run --rm --network none --read-only --cpus N --memory 4g
  -v <models>:/models:ro -v <out>:/out …`, user uid 10001 (non-root, from the image), `HF_HUB_OFFLINE=1` baked
  in, no `HF_TOKEN` passed. The fill step (§2.3/1) is the only networked run, and it only downloads three files
  by fixed URL and checks sha256.
- **Weight formats:**
  - `model.safetensors` — **safetensors**, loaded with `safetensors.torch.load_file` (`pipeline.py:137,156`):
    no code execution on load (header length read: 19 368 bytes of JSON, then raw tensors).
  - `audiovae.pth` — **NOT safetensors**: a torch zip archive containing `vae/data.pkl` (a pickle). Loaded with
    `torch.load(path, map_location="cpu", weights_only=True)` (`vae.py:26`), i.e. torch's restricted unpickler
    that only rebuilds tensors and primitive containers. Risk: a malicious pickle executing code if the
    restricted unpickler has a bypass (it had one in torch ≤ 2.5.1, CVE-2025-32434; we run 2.11.0).
    Mitigations, all required: (1) the file is accepted only if its sha256 equals
    `94b5d51e…274bf1` (§2.2) — the bytes are fixed, not whatever the hub serves later; (2) `weights_only=True`
    stays (our own loader, §2.3/2, copies that call); (3) the load happens inside the `--network none`,
    read-only, non-root container with only `/models` (ro) and `/out` mounted — nothing to steal, nowhere to
    send it. A later card may convert it once to safetensors offline; not needed for measuring.
- **Where it may run:** the home PC and the Cloud Core only. **Never the office PC** (employer machine) — the
  measure script must refuse to start unless the host is one of the two allowed machines (an allow-list,
  not an office deny-list; how the host is identified is the measure card's choice — not checked here),
  rather than merely "not be started" there.
- On the Cloud Core the measurement competes with the live API for 4 vCPU: run it outside the owner's active
  hours, `--cpus` capped, one run at a time; the gate/release lock is not taken by it.

## 6. Memory and CPU — what is known and what is not

Published (not ours): 183.2 M parameters (model card, paper); `model.safetensors` 732.8 MB (= ~183 M × 4 bytes,
fp32) plus `audiovae.pth` 377 MB; RTX 4090 RTF 0.10-0.11, TTFT ~0.5 s, 1.5 GB VRAM; **Apple M3 laptop CPU
RTF 0.70 (fp32)**; ~0.12 via Core ML; paper: "synthesizes in real time on a laptop CPU", H100 ~0.14 mean.
Sources: https://huggingface.co/freyavoice/Freya-TTS/blob/d124e07493615208f58bdd21d432736849ee4230/README.md ,
https://arxiv.org/abs/2607.09530 .

**There is no x86 CPU number anywhere** (not on the card, the README or the paper abstract) and no CPU RAM
figure. Resident memory, RTF and first-audio latency on the Cloud Core (CPX32, 4 vCPU, x86) and the home PC
(i7-14700KF) are exactly what `tts-freya-measure` produces; nothing here estimates them. The repo's
`eval/speed.py` (latency/TTFT/RTF) exists but targets CUDA defaults — the measure card writes its own timing
around §4's call.

## 7. Alternatives seen in passing (no recommendation)

| Option | Licence (as read) | Turkish | Source |
|---|---|---|---|
| Piper engine, original `rhasspy/piper` | MIT, repository **archived** | — | https://github.com/rhasspy/piper |
| Piper engine, successor `OHF-Voice/piper1-gpl` | **GPL-3.0** | — | https://github.com/OHF-Voice/piper1-gpl |
| Piper voice `tr_TR-dfki-medium` (the one Turkish voice in `rhasspy/piper-voices`) | repo tag MIT, but the voice's dataset is **CC BY-NC-SA 4.0** (MODEL_CARD); 22 050 Hz; fine-tuned from an English voice | yes | https://huggingface.co/rhasspy/piper-voices/blob/main/tr/tr_TR/dfki/medium/MODEL_CARD |
| XTTS-v2 (`coqui/XTTS-v2`) | **Coqui Public Model License 1.0.0 — "allows only non-commercial use of a machine learning model and its outputs"** | yes (multilingual) | https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt |

## 8. THIRD_PARTY_COMPONENTS entry (the lead copies this; the worker does not edit the shared file)

```markdown
## FreyaTTS-small (local Turkish narration TTS) — MEASUREMENT ONLY

Role: candidate local narration/greeting TTS behind `TTSProvider` (not wired; measurement card `tts-freya-measure`).

- Code: `freyavoiceai/FreyaTTS` @ `146d36c1cb6660646be57d31339db4eed9315de3` — Apache-2.0
  (https://github.com/freyavoiceai/FreyaTTS/blob/146d36c1cb6660646be57d31339db4eed9315de3/LICENSE).
- Weights: `freyavoice/Freya-TTS` @ `d124e07493615208f58bdd21d432736849ee4230` (`config.json`, `model.safetensors`
  sha256 `9e5828ce9eb6aaf197adc5cb098e2e80e8ff301d238add631418c5557fa08b22`) — Apache-2.0 (model card).
- Audio codec: `openbmb/VoxCPM2` @ `32279effe8c19989596f05d353d1447f51d9e915`, only `audiovae.pth`
  (sha256 `94b5d51e107e0507d4acc976cfdadb64edd6fd06d1f751dadbf2fd1594274bf1`, torch pickle, loaded `weights_only=True`)
  — Apache-2.0 (model card); decoder class from `voxcpm` 2.0.3 (Apache-2.0, installed `--no-deps`).
- Runtime: own container, python:3.12-slim-bookworm (digest-pinned), torch 2.11.0+cpu, hash-locked
  requirements in `team/plans/tts-freya-integration-plan.md` §3; includes LGPL-2.1 `soxr` and the LGPL-2.1
  `libsndfile` bundled in `soundfile` (unmodified, dynamically linked, not distributed).
- Where it runs: home PC and Cloud Core only, `--network none` at synthesis; never the office PC; never in the API image.
- Why: free, self-hosted Turkish narration for keyless deployments (master checklist 224/414/234/267); output
  48 kHz mono, single voice (seed 9 "Leyla"), not streaming.
```

## Later card (not planned here): wiring into `TTSRouter`

A separate owner-approved card would add a `LocalTurkishTTSProvider` implementing `TTSProvider`
(`services/api/app/voice/providers.py:298`, `synthesize(text, *, voice, speed, fmt) -> TTSResult`) that talks
HTTP to the FreyaTTS container — no torch in the API — register it as the last-resort provider behind the paid
one in `TTSRouter` (`services/api/app/voice/router.py:126`) with a setting that defaults OFF, plus the
fallback rows for 224/414/267. It would also have to decide 48 kHz → the format the narration player expects,
and whether to stream per clause. None of that is in this plan.
