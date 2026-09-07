"""Kind-aware summaries, referenced answers, comparisons and common points.

docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3, ADR-0083 decision 2: every answer is
``{speech, refs: [{ref, file_id, path, excerpt}], ...}``; a claim without a ref is not an
answer. The retriever (``app.documents.retrieval``) chooses which refs; this module only
composes the deterministic Turkish skeleton around them — when a cognitive backend is
configured (``app.documents.service.DocumentService``) it may rewrite the PROSE, never
choose a different ref.

Nothing here touches a database or a device: every function takes a :class:`DocRef` (or a
list of them) built from already-extracted data, so the truth.json oracle can be checked
directly against these functions with no fixtures beyond the JSON files themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.documents import retrieval

# ------------------------------------------------------------------------- DocRef


@dataclass(frozen=True, slots=True)
class DocRef:
    """Everything an answer needs about one document, decoupled from the ORM row."""

    file_id: str
    doc_id: str
    path: str
    name: str
    kind: str
    title: str | None = None
    blocks: list[dict[str, Any]] = field(default_factory=list)
    structure: dict[str, Any] = field(default_factory=dict)
    #: Whether another indexed document shares this ``title`` (ADR-0076's ambiguity rule,
    #: carried here from the index rather than re-queried per answer) — when true, every
    #: sentence naming this file names the PATH too.
    ambiguous: bool = False


def from_row(row: Any, *, ambiguous: bool = False) -> DocRef:
    """Build a :class:`DocRef` from a ``DocumentIndexRow`` (or anything with the same
    attributes) — the one seam ``DocumentService`` uses; tests build ``DocRef`` directly."""
    return DocRef(
        file_id=row.file_id,
        doc_id=row.doc_id,
        path=row.path,
        name=row.name,
        kind=row.kind,
        title=row.title,
        blocks=list(row.blocks or []),
        structure=dict(row.structure or {}),
        ambiguous=ambiguous,
    )


def spoken_file_name(name: str) -> str:
    """How a file is SAID: the stem with hyphens and underscores as spaces, the extension
    dropped — "butce-2026.xlsx" → "butce 2026", "sunum-q3.pptx" → "sunum q3". Receipts keep
    the exact name; only speech uses this. The TTS → STT loopback proxy (evidence
    tts-loopback-2026-09-07-230106) heard a spoken "xlsx" as "2026'ın" and "pptx belgesine"
    as "Gülçpege'sine": an extension is not a word in any language the owner speaks, and
    the sentence frame ("sayfalarını içeriyor", "slayt", "belgesine döndüm") already says
    what kind of file it is."""
    stem, dot, _ext = name.rpartition(".")
    base = stem if dot and stem else name
    return " ".join(base.replace("-", " ").replace("_", " ").split()) or name


def file_label(doc: DocRef) -> str:
    """The name an answer SAYS for this file — the path too when the title is shared."""
    spoken = spoken_file_name(doc.name)
    return f"{spoken} ({doc.path})" if doc.ambiguous else spoken


def _ref_dict(doc: DocRef, block: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref": str(block.get("ref") or ""),
        "file_id": doc.file_id,
        "path": doc.path,
        "excerpt": str(block.get("text") or "")[:500],
    }


# ------------------------------------------------------------------- place phrases

_SHEET_RE = re.compile(r"^sheet:(?P<sheet>[^!]+)!\D*(?P<row>\d+)")
_P_RE = re.compile(r"^p(\d+)$")
_S_RE = re.compile(r"^s(\d+)$")
_R_RE = re.compile(r"^r(\d+)$")
_H_RE = re.compile(r"^h(\d+):(.+)$")
_JSON_RE = re.compile(r"^\$\.(.+)$")
_L_RE = re.compile(r"^L(\d+)-(\d+)$")
_T_RE = re.compile(r"^t(\d+)$")


def place_phrase(ref: str, *, kind: str) -> str:
    """The place an answer's ``ref`` names, in the owner's own words (spec §3)."""
    m = _SHEET_RE.match(ref)
    if m:
        return f"{m.group('sheet')} sayfası, {m.group('row')}. satır"
    m = _P_RE.match(ref)
    if m:
        noun = "sayfa" if kind == "pdf" else "paragraf"
        return f"{m.group(1)}. {noun}"
    m = _S_RE.match(ref)
    if m:
        return f"{m.group(1)}. slayt"
    m = _R_RE.match(ref)
    if m:
        return f"{m.group(1)}. satır"
    m = _H_RE.match(ref)
    if m:
        return f"{m.group(2)} bölümü"
    m = _JSON_RE.match(ref)
    if m:
        return f"{m.group(1)} anahtarı"
    m = _L_RE.match(ref)
    if m:
        return f"{m.group(1)}-{m.group(2)}. satırlar"
    m = _T_RE.match(ref)
    if m:
        return f"{m.group(1)}. tablo"
    return ref


# ---------------------------------------------------------------------- summarize


def _blocks_by_kind(doc: DocRef, kind: str) -> list[dict[str, Any]]:
    return [b for b in doc.blocks if b.get("kind") == kind]


def summarize(doc: DocRef) -> dict[str, Any]:
    """The deterministic, kind-aware skeleton (spec §3): always present even when a
    cognitive backend later rewrites the prose around the same refs."""
    if doc.kind in ("docx", "txt"):
        headings = _blocks_by_kind(doc, "heading")
        titles = [str(h.get("text") or "") for h in headings if h.get("text")]
        refs = [_ref_dict(doc, h) for h in headings[:8]]
        body = ", ".join(titles) if titles else "belirgin bir bölüm yapısı"
        speech = f"{file_label(doc)}: {body}."
        return {"speech": speech, "refs": refs, "found": True, "structure": None}

    if doc.kind == "xlsx":
        sheets = doc.structure.get("sheets") or []
        names = [str(s.get("name")) for s in sheets if s.get("name")]
        totals = retrieval.top_k(doc.blocks, "toplam", kind=doc.kind, k=1)
        refs = [_ref_dict(doc, s.block) for s in totals]
        if totals:
            excerpt = str(totals[0].block.get("text") or "")
            speech = (
                f"{file_label(doc)}: {', '.join(names)} sayfalarını içeriyor; "
                f"{excerpt.replace(chr(9), ' ')}."
            )
        else:
            speech = f"{file_label(doc)}: {', '.join(names)} sayfalarını içeriyor."
        return {"speech": speech, "refs": refs, "found": True, "structure": None}

    if doc.kind == "pptx":
        slides = doc.structure.get("slides") or []
        titles = [str(s.get("title")) for s in slides if s.get("title")]
        blocks = _blocks_by_kind(doc, "slide")
        refs = [_ref_dict(doc, b) for b in blocks[:8]]
        count = doc.structure.get("slide_count", len(slides))
        speech = f"{file_label(doc)}: {count} slayt — {', '.join(titles)}."
        return {"speech": speech, "refs": refs, "found": True, "structure": None}

    if doc.kind == "pdf":
        pages = _blocks_by_kind(doc, "page")
        firsts = []
        for p in pages:
            text = str(p.get("text") or "")
            first_sentence = text.split(".")[0].strip()
            if first_sentence:
                firsts.append(first_sentence)
        refs = [_ref_dict(doc, p) for p in pages]
        speech = f"{file_label(doc)}: " + " ".join(f"{s}." for s in firsts)
        return {"speech": speech.strip(), "refs": refs, "found": True, "structure": None}

    if doc.kind == "md":
        sections = _blocks_by_kind(doc, "section")
        titles = [str(s.get("title")) for s in sections if s.get("title")]
        refs = [_ref_dict(doc, s) for s in sections[:8]]
        speech = f"{file_label(doc)}: {', '.join(titles)}."
        return {"speech": speech, "refs": refs, "found": True, "structure": None}

    if doc.kind == "csv":
        columns = doc.structure.get("columns") or []
        rows = doc.structure.get("rows")
        header = _blocks_by_kind(doc, "row")[:1]
        refs = [_ref_dict(doc, b) for b in header]
        speech = f"{file_label(doc)}: {', '.join(str(c) for c in columns)} sütunları, {rows} satır."
        return {"speech": speech, "refs": refs, "found": True, "structure": None}

    if doc.kind == "json":
        keys = doc.structure.get("keys") or []
        refs = [_ref_dict(doc, b) for b in doc.blocks[:8]]
        speech = f"{file_label(doc)}: {', '.join(str(k) for k in keys)} anahtarlarını içeriyor."
        return {"speech": speech, "refs": refs, "found": True, "structure": None}

    if doc.kind == "source":
        symbols = doc.structure.get("symbols") or []
        names = [str(s.get("name")) for s in symbols if s.get("name")]
        lines = doc.structure.get("lines")
        refs = [_ref_dict(doc, b) for b in doc.blocks[:4]]
        speech = f"{file_label(doc)}: {lines} satır — {', '.join(names)} tanımlı."
        return {"speech": speech, "refs": refs, "found": True, "structure": None}

    refs = [_ref_dict(doc, b) for b in doc.blocks[:4]]
    speech = f"{file_label(doc)}: {len(doc.blocks)} blok."
    return {"speech": speech, "refs": refs, "found": True, "structure": None}


# ------------------------------------------------------------------------- answer

_COUNT_STRUCTURE_BY_KIND: dict[str, tuple[tuple[str, ...], str]] = {
    "pptx": (("slayt", "slayd"), "slide_count"),
    "pdf": (("sayfa",), "pages"),
    "csv": (("satır", "satir", "kayıt", "kayit"), "rows"),
}
_COUNT_QUESTION_WORDS = ("kaç", "kac")


def _count_answer(doc: DocRef, question: str) -> dict[str, Any] | None:
    tokens = [retrieval.normalize_word(t) for t in retrieval.tokenize(question)]
    if not any(w in tokens for w in _COUNT_QUESTION_WORDS):
        return None
    spec = _COUNT_STRUCTURE_BY_KIND.get(doc.kind)
    if spec is None:
        return None
    nouns, key = spec
    if not any(retrieval.normalize_word(n) in tokens for n in nouns):
        return None
    value = doc.structure.get(key)
    if value is None:
        return None
    noun_tr = {"slide_count": "slayt", "pages": "sayfa", "rows": "satır"}[key]
    speech = f"{file_label(doc)}: {value} {noun_tr} var efendim."
    ref = {
        "ref": f"structure:{key}",
        "file_id": doc.file_id,
        "path": doc.path,
        "excerpt": f"{key}={value}",
    }
    return {
        "speech": speech,
        "refs": [ref],
        "found": True,
        "structure": {key: value},
    }


def _symbol_answer(doc: DocRef, question: str) -> dict[str, Any] | None:
    if doc.kind != "source":
        return None
    tokens = [retrieval.normalize_word(t) for t in retrieval.tokenize(question)]
    if not any(w in tokens for w in ("satır", "satir", "kaçıncı", "kacinci")):
        return None
    symbols = doc.structure.get("symbols") or []
    for symbol in symbols:
        name = str(symbol.get("name") or "")
        name_norm = retrieval.normalize_word(name)
        if not name_norm or not any(retrieval.shares_prefix(name_norm, t) for t in tokens):
            continue
        line = symbol.get("line")
        block = _containing_line_block(doc, line)
        refs = [_ref_dict(doc, block)] if block is not None else []
        speech = f"{file_label(doc)}: {name} {line}. satırda efendim."
        return {
            "speech": speech,
            "refs": refs,
            "found": True,
            "structure": {"symbol": name, "line": line},
        }
    return None


_L_RANGE_RE = re.compile(r"^L(\d+)-(\d+)$")


def _containing_line_block(doc: DocRef, line: Any) -> dict[str, Any] | None:
    if not isinstance(line, int):
        return None
    for block in doc.blocks:
        m = _L_RANGE_RE.match(str(block.get("ref") or ""))
        if m and int(m.group(1)) <= line <= int(m.group(2)):
            return block
    return doc.blocks[0] if doc.blocks else None


def references(doc: DocRef, question: str, *, k: int = 3) -> list[dict[str, Any]]:
    """The refs :func:`answer` would look at for ``question`` — used to name what was
    searched even when nothing answered it."""
    results = retrieval.top_k(doc.blocks, question, kind=doc.kind, structure=doc.structure, k=k)
    return [_ref_dict(doc, r.block) for r in results]


def answer(doc: DocRef, question: str) -> dict[str, Any]:
    """Retrieval -> the best block(s) -> a Turkish sentence naming the file and the
    place. A question no block answers gets an honest "bulamadım" naming what was
    searched — never an invented value (ADR-0083 decision 2)."""
    structural = _count_answer(doc, question) or _symbol_answer(doc, question)
    if structural is not None:
        return structural

    results = retrieval.top_k(doc.blocks, question, kind=doc.kind, structure=doc.structure, k=3)
    if not results:
        looked_at = doc.blocks[:3]
        refs = [_ref_dict(doc, b) for b in looked_at]
        speech = f"{file_label(doc)} içinde bunu bulamadım efendim."
        return {"speech": speech, "refs": refs, "found": False, "structure": None}

    top = results[0]
    place = place_phrase(top.ref, kind=doc.kind)
    excerpt = str(top.block.get("text") or "")
    speech = f"{file_label(doc)}, {place}: {excerpt}"
    refs = [_ref_dict(doc, r.block) for r in results]
    return {"speech": speech, "refs": refs, "found": True, "structure": None}


# ------------------------------------------------------------------------ compare


def _heading_depth_by_ref(blocks: list[dict[str, Any]]) -> dict[str, int]:
    """Ref -> the nesting depth of the heading section it lives under (its OWN level, for
    a heading block; the nearest preceding heading's level, for anything else; 0 before
    any heading has been seen). Used to pick "the" changed clause among several changed
    refs (see :func:`compare_blocks`) without hardcoding any one fixture's wording: a
    change nested under a specific section ("Madde 3 - Ödeme", depth 2) is a substantive
    clause edit, while a change sitting directly under the document's own title (depth 1,
    e.g. a "Sürüm 2 (2026)" stamp) is document-level bookkeeping, not a clause."""
    depth = 0
    out: dict[str, int] = {}
    for block in blocks:
        if block.get("kind") == "heading":
            try:
                depth = int(block.get("level") or 0)
            except (TypeError, ValueError):
                depth = depth
        out[str(block.get("ref") or "")] = depth
    return out


def compare_blocks(a: DocRef, b: DocRef) -> dict[str, Any]:
    """From the two indexed block lists (used when the two files are not both on the
    device right now, or as the Cloud Core's own check on the device's ``file.compare``):
    changed/unchanged/added/removed refs, and the first changed clause's two texts."""
    a_by_ref = {str(x.get("ref")): x for x in a.blocks}
    b_by_ref = {str(x.get("ref")): x for x in b.blocks}
    changed: list[str] = []
    unchanged: list[str] = []
    for ref, a_block in a_by_ref.items():
        if ref not in b_by_ref:
            continue
        if str(a_block.get("text") or "") == str(b_by_ref[ref].get("text") or ""):
            unchanged.append(ref)
        else:
            changed.append(ref)
    removed = [r for r in a_by_ref if r not in b_by_ref]
    added = [r for r in b_by_ref if r not in a_by_ref]

    # "The" changed clause is the most deeply-nested one (module docstring of
    # _heading_depth_by_ref), tie-broken by document order — never simply "first found".
    depth_by_ref = _heading_depth_by_ref(a.blocks)
    first_changed = None
    if changed:
        best_ref = max(changed, key=lambda r: (depth_by_ref.get(r, 0), -changed.index(r)))
        first_changed = {
            "ref": best_ref,
            "a_text": str(a_by_ref[best_ref].get("text") or ""),
            "b_text": str(b_by_ref[best_ref].get("text") or ""),
        }

    if first_changed:
        speech = (
            f"{file_label(a)} ile {file_label(b)} arasında {len(changed)} fark var; "
            f"ilk fark {place_phrase(first_changed['ref'], kind=b.kind)}: "
            f'"{first_changed["a_text"]}" -> "{first_changed["b_text"]}".'
        )
    elif added or removed:
        speech = (
            f"{file_label(a)} ile {file_label(b)} arasında içerik eklenmiş/çıkarılmış "
            f"({len(added)} eklendi, {len(removed)} çıkarıldı)."
        )
    else:
        speech = f"{file_label(a)} ile {file_label(b)} arasında fark bulamadım."

    refs = []
    if first_changed is not None:
        refs = [
            {
                "ref": first_changed["ref"],
                "file_id": a.file_id,
                "path": a.path,
                "excerpt": first_changed["a_text"],
            },
            {
                "ref": first_changed["ref"],
                "file_id": b.file_id,
                "path": b.path,
                "excerpt": first_changed["b_text"],
            },
        ]
    return {
        "speech": speech,
        "refs": refs,
        "changed_refs": changed,
        "unchanged_refs": unchanged,
        "added_refs": added,
        "removed_refs": removed,
        "changed_clause": first_changed,
        "found": True,
    }


def compare(a: DocRef, b: DocRef, *, device_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """The device's own ``file.compare`` when both files are on the same device (spec §2);
    otherwise :func:`compare_blocks`."""
    if device_result is not None:
        changed = list(device_result.get("changed_refs") or [])
        unchanged = list(device_result.get("unchanged_refs") or [])
        added = list(device_result.get("added_refs") or [])
        removed = list(device_result.get("removed_refs") or [])
        speech = str(device_result.get("summary") or "")
        if not speech:
            speech = f"{file_label(a)} ile {file_label(b)} arasında {len(changed)} fark var."
        return {
            "speech": speech,
            "refs": [],
            "changed_refs": changed,
            "unchanged_refs": unchanged,
            "added_refs": added,
            "removed_refs": removed,
            "changed_clause": None,
            "found": True,
        }
    return compare_blocks(a, b)


# ------------------------------------------------------------------- common_points


def _doc_vocabulary(doc: DocRef) -> tuple[set[str], dict[str, str]]:
    """(normalized content words, normalized -> a natural Turkish display spelling) drawn
    from the document's title, name AND its blocks — a theme named only by the file's own
    title ("butce-2026.xlsx") is still a real common point, the same way a human skims a
    filename before opening a spreadsheet. The display map keeps the first raw spelling
    encountered (casefolded, diacritics KEPT) so a spoken common point reads "bütçe", not
    the ASCII-folded internal matching key "butce"."""
    text = " ".join([doc.title or "", doc.name, *[str(b.get("text") or "") for b in doc.blocks]])
    words: set[str] = set()
    display: dict[str, str] = {}
    for tok in retrieval.tokenize(text):
        norm = retrieval.normalize_word(tok)
        if len(norm) < 2 or retrieval.is_stopword(norm) or tok.isdigit():
            continue
        words.add(norm)
        display.setdefault(norm, retrieval.turkish_casefold(tok))
    return words, display


def _term_ref(doc: DocRef, term: str) -> dict[str, Any]:
    for block in doc.blocks:
        if term in {
            retrieval.normalize_word(w) for w in retrieval.tokenize(str(block.get("text") or ""))
        }:
            return _ref_dict(doc, block)
    # The term named only the file's own title/name (module docstring) — a real, if
    # synthetic, ref: it names the file and excerpts what actually matched.
    return {
        "ref": "title",
        "file_id": doc.file_id,
        "path": doc.path,
        "excerpt": doc.title or doc.name,
    }


def common_points(docs: list[DocRef]) -> dict[str, Any]:
    """Terms present in every document (casefold + diacritics-insensitive, stopwords
    removed), one ref per document per term (spec §3). The speech and the ``terms`` keys
    use a natural Turkish spelling (``turkish_casefold``, diacritics kept); the matching
    itself stays fully diacritics-insensitive underneath."""
    if not docs:
        return {"speech": "Karşılaştıracak belge yok efendim.", "terms": {}, "found": False}
    vocabularies = [_doc_vocabulary(d) for d in docs]
    word_sets = [w for w, _ in vocabularies]
    common = set.intersection(*word_sets) if word_sets else set()
    # Drop overly short/incidental tokens (numbers already excluded by content_words).
    common = {t for t in common if len(t) >= 4}
    # A spelling that still carries diacritics ("bütçe") is preferred over one that only
    # coincidentally folded to plain ASCII (a filename like "butce-2026.xlsx") - "first
    # doc wins" would let the wrong one stick whenever the plain form happens to appear
    # in an earlier document's own name.
    display_by_norm: dict[str, str] = {}
    for _, display in vocabularies:
        for norm, spelled in display.items():
            current = display_by_norm.get(norm)
            if current is None or (current == norm and spelled != norm):
                display_by_norm[norm] = spelled
    terms: dict[str, list[dict[str, Any]]] = {}
    for norm_term in sorted(common):
        display_term = display_by_norm.get(norm_term, norm_term)
        terms[display_term] = [_term_ref(d, norm_term) for d in docs]
    if not terms:
        names = ", ".join(file_label(d) for d in docs)
        return {
            "speech": f"{names} arasında ortak bir nokta bulamadım.",
            "terms": {},
            "found": False,
        }
    names = ", ".join(file_label(d) for d in docs)
    speech = f"{names} belgelerinin ortak noktaları: {', '.join(terms)}."
    return {"speech": speech, "terms": terms, "found": True}


__all__ = [
    "DocRef",
    "answer",
    "common_points",
    "compare",
    "compare_blocks",
    "file_label",
    "from_row",
    "place_phrase",
    "references",
    "summarize",
]
