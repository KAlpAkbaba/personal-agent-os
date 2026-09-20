"""What a follow-up about a finished research SAYS (docs/DECISIONS.md ADR-0076).

Every sentence here is composed from ONE durable ``ResearchReportRow.report_json`` and
nothing else — no ledger event, no counts recomputed from a second source, no crawl.
Deterministic: the same report always renders the same Turkish.

The owner's rule about how it opens matters as much as what it says. A follow-up is
answered IMMEDIATELY, prefixed naturally ("Bu araştırmada ..."), never narrated as
bookkeeping: "kayıtlarımı kontrol edeceğim" and "hangi kayda bakmam gerektiğini bulmaya
çalışıyorum" are the sentences the owner heard instead of an answer, and they are banned
by name (``app.actions.receipt.BOOKKEEPING_PHRASES``).

The split between the levels is ADR-0067's and is kept: EXECUTIVE and DETAIL are the
OUTCOME (``app.research.result.ResearchResult``), TECHNICAL is the pipeline's own
telemetry (``ResearchDiagnostics``), and crawler vocabulary never leaks upward.
"""

from __future__ import annotations

import re
from typing import Any

from app.narration.numbers import cardinal
from app.research.result import (
    MAX_SPOKEN_FINDINGS,
    ResearchDiagnostics,
    ResearchResult,
    spoken_result,
)

LEVEL_EXECUTIVE = "executive"
LEVEL_DETAIL = "detail"
LEVEL_TECHNICAL = "technical"
LEVEL_FULL = "full"

FOLLOWUP_LEVELS: tuple[str, ...] = (
    LEVEL_EXECUTIVE,
    LEVEL_DETAIL,
    LEVEL_TECHNICAL,
    LEVEL_FULL,
)

#: The natural opener. Not "kayıtlara bakıyorum": the answer is already in hand when this
#: is spoken, and saying otherwise is the bookkeeping narration the owner refused.
PREFIX_TR = "Bu araştırmada"

_ORDINALS_TR: tuple[str, ...] = (
    "Birincisi",
    "İkincisi",
    "Üçüncüsü",
    "Dördüncüsü",
    "Beşincisi",
    "Altıncısı",
    "Yedincisi",
    "Sekizincisi",
    "Dokuzuncusu",
    "Onuncusu",
)

#: How many findings the DETAIL level names before it stops. A listening budget, the same
#: discipline app.explain.engine keeps: the rest stays in the report artifact.
MAX_DETAIL_FINDINGS = 6

#: Turkish for the rejection reason codes, so "hangi sayfalar elendi?" is answered in the
#: owner's language rather than in the crawler's.
_REJECTION_TR: dict[str, str] = {
    "interstitial": "ara sayfa",
    "duplicate": "yinelenen içerik",
    "off_topic": "konu dışı",
    "too_old": "tarihi eski",
    "invalid_page": "geçersiz sayfa",
    "fetch_failed": "getirilemedi",
    "invalid_evidence_contract": "kanıt sözleşmesine uymayan",
    "low_relevance": "ilgisi düşük",
    "paywall": "ödeme duvarı",
}


def _tr_list(items: list[str] | tuple[str, ...]) -> str:
    parts = [i for i in items if i]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f" ve {parts[-1]}"


def _clean(text: Any, *, max_len: int = 400) -> str:
    return str(text or "").strip()[:max_len]


def _source_names(report_json: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for raw in report_json.get("sources") or ():
        if not isinstance(raw, dict):
            continue
        name = _clean(raw.get("publisher") or raw.get("title"), max_len=80)
        if name and name not in names:
            names.append(name)
    return names


#: How much of a source's own text one finding is read out with. Long enough to be the
#: NEWS and not the headline, short enough to stay a spoken answer (ADR-0188).
SAID_MAX_CHARS = 420
#: A line that is only a date/time stamp: the first line of most Turkish news pages, and
#: not what happened.
_TIMESTAMP_LINE_RE = re.compile(r"^\s*\d{1,2}[./]\d{1,2}[./]\d{2,4}(\s+\d{1,2}[:.]\d{2})?\s*$")


def _what_the_source_said(report_json: dict[str, Any], evidence_ids: tuple[str, ...]) -> str:
    """The page's OWN words for this finding, from the report's Details section.

    The articles were always there — ``synthesis._detail_statement`` quotes each page's
    content into ``details`` — and no spoken level ever read them, so a research was
    answered with its headlines and their provenance (owner, 2026-09-20: "söyledikleri
    sadece başlık ... detayı alamıyorum").

    It is the SOURCE speaking, not this system: the text is quoted, attributed by the
    caller, bounded, stripped of line breaks, and never read as an instruction to anyone.
    """
    wanted = {str(e) for e in evidence_ids if e}
    for section in report_json.get("details") or ():
        if not isinstance(section, dict):
            continue
        for statement in section.get("statements") or ():
            if not isinstance(statement, dict):
                continue
            ids = {str(e) for e in (statement.get("evidence_ids") or ())}
            if wanted and not (ids & wanted):
                continue
            # ADR-0189: a Turkish rendering, when the report carries one, is what the
            # owner hears; the source's own words stay in the report beside it.
            raw = str(statement.get("text_tr") or statement.get("text") or "")
            lines = [
                line.strip()
                for line in raw.splitlines()
                if line.strip() and not _TIMESTAMP_LINE_RE.match(line)
            ]
            said = " ".join(lines).strip()
            if said:
                return _capped_sentences(said, SAID_MAX_CHARS)
    return ""


def _translated_anything(report_json: dict[str, Any]) -> bool:
    note = report_json.get("translation")
    return bool(isinstance(note, dict) and int(note.get("statements") or 0) > 0)


def _capped_sentences(text: str, limit: int) -> str:
    """``text`` cut at a sentence end at or before ``limit`` — never mid-word."""
    if len(text) <= limit:
        return text
    window = text[: limit + 1]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if cut > 0:
        return window[: cut + 1]
    space = window.rfind(" ")
    return (window[:space] if space > 0 else window[:limit]).rstrip(" ,;:") + "..."


#: What a page title repeats at its end: the site, again ("... | NTV Haber", "... - Sözcü").
_TITLE_SITE_SUFFIX_RE = re.compile(r"\s*[|–—-]\s*[^|–—-]{1,40}$")
#: The deterministic synthesiser's stock "why": provenance in a sentence, and nothing the
#: owner does not already know once the source has been named and quoted.
_FORMULAIC_WHY_MARKERS = ("kaynağından doğrulandı", "kaynagindan dogrulandi")


def _headline(title: str, names: list[str]) -> str:
    """The finding's own headline, without the site name tacked on the end of it."""
    headline = _clean(title, max_len=160).strip().rstrip(".")
    trimmed = _TITLE_SITE_SUFFIX_RE.sub("", headline).strip()
    if trimmed and (len(trimmed) >= 12 or not headline.startswith(trimmed)):
        headline = trimmed
    for name in names:
        if name and headline.lower().endswith(name.lower()):
            headline = headline[: -len(name)].strip(" -|–—")
    return headline


def _worth_saying(why: str) -> str:
    """``why_it_matters`` unless it is the stock provenance line."""
    text = _clean(why, max_len=240).strip().rstrip(".")
    lowered = text.lower()
    if any(marker in lowered for marker in _FORMULAIC_WHY_MARKERS):
        return ""
    return text


def _finding_sentence(ordinal: str, finding: Any) -> str:
    claim = finding.finding.strip().rstrip(".")
    sentence = f"{ordinal}, {claim}."
    why = finding.why_it_matters.strip().rstrip(".")
    if why:
        sentence += f" Bu önemli çünkü {why}."
    return sentence


def executive_speech(report_json: dict[str, Any], *, topic: str = "") -> str:
    """The owner-facing answer: what the research found, in three findings at most."""
    result = ResearchResult.from_report_json(report_json, topic=topic)
    if result.insufficient or not result.findings:
        # The honest shape app.research.result already owns; nothing to prefix onto a
        # "there is no defensible finding" sentence.
        return spoken_result(result)
    said_topic = _clean(result.topic, max_len=120)
    head = (
        f"{PREFIX_TR} '{said_topic}' konusunda {cardinal(len(result.findings))} bulgu var."
        if said_topic
        else f"{PREFIX_TR} {cardinal(len(result.findings))} bulgu var."
    )
    parts = [head]
    summary = _clean(result.executive_summary)
    if summary:
        parts.append(summary if summary.endswith(".") else summary + ".")
    for ordinal, finding in zip(_ORDINALS_TR, result.findings[:MAX_SPOKEN_FINDINGS], strict=False):
        parts.append(_finding_sentence(ordinal, finding))
    if len(result.findings) > MAX_SPOKEN_FINDINGS:
        parts.append("İsterseniz kalan bulguları da anlatayım.")
    return " ".join(parts)


def detail_speech(report_json: dict[str, Any], *, topic: str = "") -> str:
    """Every finding this report carries, with why it matters and who said it."""
    result = ResearchResult.from_report_json(report_json, topic=topic)
    if result.insufficient or not result.findings:
        return spoken_result(result)
    by_id = {str(s.get("id")): s for s in (report_json.get("sources") or ()) if isinstance(s, dict)}
    parts = [f"{PREFIX_TR} bulguları ayrıntısıyla anlatıyorum."]
    if _translated_anything(report_json):
        # Said ONCE, at the top: the owner should know which sentences are a translation,
        # and hearing it before every finding is the noise they asked me to stop making.
        parts.append("Yabancı dildeki kaynakları Türkçeye çevirdim.")
    for ordinal, finding in zip(_ORDINALS_TR, result.findings[:MAX_DETAIL_FINDINGS], strict=False):
        names = [
            _clean(by_id[ref].get("publisher") or by_id[ref].get("title"), max_len=80)
            for ref in finding.sources
            if ref in by_id
        ]
        said = _what_the_source_said(report_json, tuple(finding.sources))
        if said:
            # ADR-0189: ONE attribution, then the news. The old shape said the publisher
            # three times for one item - in the provenance summary, again as "Kaynak: X",
            # and again as "Kaynağın kendi sözleriyle" (owner: "sürekli kaynak kaynak deyip
            # durmasın"). What follows the colon is still the page talking, not this system.
            headline = _headline(finding.finding, names)
            speaker = names[0] if names else ""
            stop = "" if headline.endswith(("?", "!", ".", "…")) else "."
            opener = f"{ordinal}, {headline}{stop}" if headline else f"{ordinal},"
            sentence = f"{opener} {speaker} şunu yazıyor: {said}" if speaker else f"{opener} {said}"
            why = _worth_saying(finding.why_it_matters)
            if why:
                sentence += f" Bu önemli çünkü {why}."
        else:
            sentence = _finding_sentence(ordinal, finding)
            if names:
                sentence += f" Kaynak: {_tr_list(names)}."
        parts.append(sentence)
    if len(result.findings) > MAX_DETAIL_FINDINGS:
        parts.append("Kalanı raporda duruyor efendim.")
    return " ".join(parts)


def technical_speech(report_json: dict[str, Any]) -> str:
    """The pipeline's own numbers for THIS run — read, never recounted."""
    diag = ResearchDiagnostics.from_report_json(report_json)
    parts = [
        f"{PREFIX_TR} {cardinal(diag.discovered_count)} aday keşfedildi, "
        f"{cardinal(diag.fetched_count)} sayfa getirildi, "
        f"{cardinal(diag.rejected_pages)} sayfa elendi."
    ]
    if diag.rejected_by_reason:
        reasons = [
            f"{_REJECTION_TR.get(reason, reason)}: {cardinal(int(count))}"
            for reason, count in diag.rejected_by_reason.items()
        ]
        parts.append(f"Elenme nedenleri: {_tr_list(reasons)}.")
    if diag.quarantined_pages:
        parts.append(f"{cardinal(diag.quarantined_pages)} öğe kalite kapısında karantinaya alındı.")
    if diag.refused_pages:
        parts.append(f"{cardinal(diag.refused_pages)} ifade şüpheli içerik nedeniyle reddedildi.")
    if diag.mode:
        mode_bits = [f"araştırma modu {diag.mode}"]
        if diag.waves:
            mode_bits.append(f"{cardinal(diag.waves)} dalga")
        parts.append(_tr_list(mode_bits).capitalize() + ".")
    # B31 req 207: a substitution is said as one - which provider was asked for, which
    # answered, and why - never only the name that happened to answer.
    fallback = diag.synthesis_fallback
    if fallback:
        parts.append(
            f"Sentez sağlayıcısı {fallback.get('used') or diag.synthesis_provider}; istenen "
            f"{fallback.get('requested')} {cardinal(int(fallback.get('attempts') or 0))} "
            "denemede kabul edilmedi, yedeğe geçildi."
        )
    elif diag.synthesis_provider:
        parts.append(f"Sentez sağlayıcısı {diag.synthesis_provider}.")
    if diag.search_fallbacks:
        parts.append(
            f"{cardinal(diag.search_fallbacks)} arama sorgusu yedek sağlayıcıyla cevaplandı."
        )
    return " ".join(parts)


def sources_speech(report_json: dict[str, Any]) -> str:
    """ "Bunun kaynaklarını söyle." — publishers, not addresses (the owner's preference:
    link addresses are not read unless asked for)."""
    names = _source_names(report_json)
    if not names:
        return f"{PREFIX_TR} adına konuşabileceğim kayıtlı bir kaynak yok efendim."
    return f"{PREFIX_TR} {cardinal(len(names))} kaynak kullandım: {_tr_list(names)}."


def finding_detail_speech(report_json: dict[str, Any], index: int) -> str:
    """One finding, in full. ``index`` is 1-based, the way the owner counts."""
    result = ResearchResult.from_report_json(report_json)
    total = len(result.findings)
    if total == 0:
        return f"{PREFIX_TR} anlatabileceğim bir bulgu yok efendim."
    if index < 1 or index > total:
        return (
            f"{PREFIX_TR} {cardinal(total)} bulgu var efendim; "
            f"{cardinal(index)} numaralı bulgu yok."
        )
    finding = result.findings[index - 1]
    by_id = {str(s.get("id")): s for s in (report_json.get("sources") or ()) if isinstance(s, dict)}
    ordinal = _ORDINALS_TR[index - 1] if index <= len(_ORDINALS_TR) else f"{index}."
    parts = [_finding_sentence(ordinal, finding)]
    names = [
        _clean(by_id[ref].get("publisher") or by_id[ref].get("title"), max_len=80)
        for ref in finding.sources
        if ref in by_id
    ]
    if names:
        parts.append(f"Kaynak: {_tr_list(names)}.")
    return " ".join([f"{PREFIX_TR}:", *parts])


def speech_for_level(report_json: dict[str, Any], *, level: str, topic: str = "") -> str:
    """The one entry point ``research.explain`` uses."""
    if level == LEVEL_DETAIL:
        return detail_speech(report_json, topic=topic)
    if level == LEVEL_TECHNICAL:
        return technical_speech(report_json)
    if level == LEVEL_FULL:
        return " ".join(
            (
                executive_speech(report_json, topic=topic),
                detail_speech(report_json, topic=topic),
                technical_speech(report_json),
            )
        )
    return executive_speech(report_json, topic=topic)


__all__ = [
    "FOLLOWUP_LEVELS",
    "LEVEL_DETAIL",
    "LEVEL_EXECUTIVE",
    "LEVEL_FULL",
    "LEVEL_TECHNICAL",
    "MAX_DETAIL_FINDINGS",
    "PREFIX_TR",
    "detail_speech",
    "executive_speech",
    "finding_detail_speech",
    "sources_speech",
    "speech_for_level",
    "technical_speech",
]
