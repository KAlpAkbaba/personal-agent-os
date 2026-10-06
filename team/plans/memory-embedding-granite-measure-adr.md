# ADR (numarayı lead verir) — Hafızanın anlam motoruna aday ÖLÇÜMÜ: IBM Granite Embedding Multilingual R2 (2026-10-06)

**Karar: yalnız ölçüm.** Üretimde hiçbir şey değişmez: `DEFAULT_LOCAL_MODEL` =
`minishlab/potion-multilingual-128M`, `build_embedder` aynı, Settings'e alan yok, göç yok
(`EMBEDDING_DIM` 256 donuk; Granite ona kırpılır), yeniden indeksleme yok, Cloud Core yayını yok,
bağımlılık eklenmedi (`pyproject.toml` / `uv.lock` dokunulmadı: fastembed 0.8.1, onnxruntime
1.30.0, tokenizers 0.23.2, huggingface-hub 1.33.0 zaten kilitte).

**Neden Granite, neden şimdi.** Apache-2.0; Matryoshka 256'ya iner (göç yok); Türkçe kartın
"enhanced support" 52 dilinin içinde; ONNX FP32 ve INT8 hazır geliyor. `google/embeddinggemma-300m`
dışarıda (Gemma kullanım şartları + kapılı indirme). Qwen3 ADR-0200'de 256'da ölçüldü ve elendi
(AYRIM +0.354 < potion +0.457, 694 ms/embed).

**Pinler (kaynaktan okundu, tekliften değil).**
- Kart: https://huggingface.co/ibm-granite/granite-embedding-311m-multilingual-r2 — `license:
  apache-2.0` (kart başlığı + "License: Apache 2.0", https://www.apache.org/licenses/LICENSE-2.0);
  depoda ayrı bir LICENSE dosyası YOK, lisans kart meta verisinde. `gated: false`, hesap/token gerekmez.
- Revizyon: `44399559930365213510b1ee2eb15ded83374f0e`
  (https://huggingface.co/api/models/ibm-granite/granite-embedding-311m-multilingual-r2).
- Dosyalar (indirilen baytların sha256'sı; büyük dosyalarda HF LFS oid'i ile aynı), hepsi
  `https://huggingface.co/ibm-granite/granite-embedding-311m-multilingual-r2/resolve/44399559930365213510b1ee2eb15ded83374f0e/<yol>`:
  - `onnx/model.onnx` 1247170481 B `75f9f258bf5013f5fe8a4dad61dd0fd16ac0cbaa7a106e3d3f41c2d04a42d541` (FP32)
  - `onnx/model_quint8_avx2.onnx` 313421909 B `f1fdd44e7e1ac51f12ab7957c7bd092e064d596c288513bf9d326842f669edee` (INT8)
  - `tokenizer.json` 33384821 B `0087c868b33bad550a78a08d19798cfd7f713cde4f020803b8f51f405503e15f`
  - `tokenizer_config.json` 1155500 B `7947bdf0378520e69ca412b8c4dacd1cffa8aef099f851fdd5c65aa27c6b36a0`
  - `special_tokens_map.json` 694 B `cb9e60dcf4d8d314315cb3e761fe4c2e664fda8dbf66d7815372b2639e381182`
  - `config.json` 1191 B `e1e3fc842a8e0537e25d6e4c93879698b92ae96722e8c162bef334b57978a3b0`
- Pooling: **CLS**. `1_Pooling/config.json` (aynı revizyon): `"pooling_mode_cls_token": true`,
  `"pooling_mode_mean_tokens": false`; kartın Transformers örneği: "Perform pooling.
  granite-embedding-311m-multilingual-r2 uses CLS Pooling". `modules.json` 2_Normalize içerir →
  L2 normalleştirme.
- Sorgu öneki: YOK. `config_sentence_transformers.json`: `"prompts": {"query": "", "document": ""}`.
- Genişlik: doğal 768 ("It produces 768-dimensional vectors"); Matryoshka: "Truncate embeddings to
  512, 384, 256, or 128 dimensions with graceful degradation" ve tablo "Matryoshka Dimensions 768,
  512, 384, 256, 128".
- Küçük model `granite-embedding-97m-multilingual-r2` ÖLÇÜLMEDİ: 384 genişlik, kartında Matryoshka
  genişliği yok, 384 256'lık sütuna göçsüz sığmaz; izin listesinde DEĞİL (bir test bunu tutar).

**Kayıt.** `providers.CUSTOM_ONNX_MODELS` kapalı, salt-okunur (MappingProxyType) eşlem: ad →
`CustomOnnxModel(hf_repo, revision, model_file, pooling, native_dim, normalize, files=PinnedFile(yol,
sha256, boyut)…)`. İki ad aynı karttan: `ibm-granite/granite-embedding-311m-multilingual-r2` (FP32) ve
`…-r2-int8` (INT8; ek, fastembed'in `Qwen/Qwen3-Embedding-0.6B-Q` takma adı gibi). İkisi de
`MRL_TRUNCATABLE_MODELS`'te. `model_id` mevcut şema: `local-<ad>@256`; model_id değişimi tasarım gereği
yeniden indekslemedir (ADR-0200, asla karışık indeks).

**Yükleme yolu.** Kayıttaki bir ad: `huggingface_hub.snapshot_download` SABİT revizyonda, verilen
`cache_dir`'e; her dosyanın boyutu ve sha256'sı İLK gömmeden ÖNCE kayıtla karşılaştırılır, uyuşmazlık
dosyayı adlandıran `EmbeddingProviderError` (içerik ve vektör asla), hiçbir şey gömülmez. Sonra
fastembed'e bir kez `TextEmbedding.add_custom_model` (CLS, normalize, dim 768, model_file) ve
`TextEmbedding(model_name, cache_dir, specific_model_path=<doğrulanmış snapshot>)` — fastembed kendi
indirmesini yapmaz, revizyon pinli kalır. Gerçekte (ev PC'si, Windows): fastembed 0.8.1
**`UnicodeDecodeError`** ile düştü — `load_tokenizer` `tokenizer_config.json`'u kodlama vermeden açıyor,
Windows cp1252 okuyor. Bu yüzden Windows'ta **onnxruntime yedeği** (`_OnnxRuntimeModel`: tokenizers +
onnxruntime, yalnız CPUExecutionProvider, CLS, L2) serviyor. `PYTHONUTF8=1` ile fastembed yolu yüklüyor
ve iki yolun vektörleri aynı (üç cümlede kosinüs 1.000000). Linux'ta (Cloud Core) fastembed yolunun
alınması beklenir — ÖLÇÜLMEDİ. Kayıtta olmayan her ad (potion dahil) fastembed'e bugünkü kwargs ile gider.

**Biçimler.** `ev-pc` = tüm thread'ler; `cpx32-bicimi` = `--threads 4`, bir VEKİL (PROXY);
`cpx32-gercek: NOT_RUN` (uzak eylem, lead'in). Hüküm sayılardan: (a) AYRIM potion'dan geniş VE 200
satırlık tutma partisi (`memory_index_fill_batch` × ortanca embed) CPX32 bütçesi **30 s** içinde
(Qwen3 2,3 dk ile elenmişti) → benimseme önerisi AYRI fikir; (b) geniş ama yavaş → "sahibin PC'sine
taşınınca"; (c) dar → ölçüldü, alınmadı. Bu kart hiçbir durumda benimsemez.

**KVKK.** Sabit Türkçe bench cümleleri, sahibin gerçek hafıza metni asla; model yerelde koşar.
İş makinesi (MAIL) indirmeyi de bench'i de koşmaz. Önbellek `%LOCALAPPDATA%/PagentOS/fastembed-cache`
(depo ve %TEMP% reddedilir).

**Açık iş.** Hüküm (a) çıkarsa: benimseme kartı — `DEFAULT_LOCAL_MODEL` / üretim ortam değişkeni,
Cloud Core önbellek ön-yüklemesi (yayın betiği), `lifecycle.reindex` ile yeniden indeksleme ve ADR-0224
katman-2 örneklerinin yeniden gömülmesi; Windows'ta UTF-8 sorunu fastembed'e bildirilir ya da yedek yol kalır.
