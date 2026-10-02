"""The STT corpus: sentences AS THE SPEECH-TO-TEXT RENDERED THEM (ADR-0224, measurement).

The Owner Utterance Suite (``corpus.py``) is written in the forms the rule tables know, which
is why 2745 cases at 100 % did not predict one of the three failures of 2026-09-30. This
corpus holds the other half: what the STT actually wrote, beside what the owner meant - the
intent, the entities, the machine - and the confidence band the reading is expected in.

Two kinds of case, and the difference is a field, never a guess:

* ``origin="real"``: a rendering production really produced, with the date it was heard.
  Today these are the three sentences of the trial of 2026-09-30 20:11 UTC. New ones are
  PROPOSED by ``scripts/voice/collect-stt-corpus.ps1`` and added here by hand, with the
  meaning the owner confirms; nothing writes this file but a person.
* ``origin="derived"``: a canonical corpus sentence with ONE documented STT distortion
  applied. Derived cases are not real renderings and are never presented as such: each names
  its distortion and the canonical case it came from, and ``meant`` is that case's own
  sentence, letter for letter (the unit test holds all three).

The distortions are the four the trial and ADR-0224 document:

* ``polite``: the imperative written in its polite form ("aç" -> "açın"), from
  ``IMPERATIVES`` - the STT's choice on 2026-09-30;
* ``diacritics``: the Turkish letters folded to ASCII, case and punctuation kept;
* ``fused``: two neighbouring words written as one;
* ``invented_suffix``: one consonant-final word that is not the verb given a suffix nobody
  said - "ü", the one heard on 2026-09-30 ("Ofisü").

Which canonical sentences are distorted is a RULE, so the corpus cannot be picked to pass:
for every category of ``corpus.py``, in corpus order, the FIRST canonical single-turn case
that names an intent, expects an executed answer (``ok`` or ``control``), has two or more
words and ends in a verb of ``IMPERATIVES`` - plus the trial's own family, named in
``TRIAL_FAMILY_BASES``. Every distortion that applies to the sentence is taken (a sentence
with no Turkish letter has no ``diacritics`` case; one with no consonant-final noun has no
``invented_suffix`` case). The unit test recomputes the rule from ``corpus.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from tests.voice_corpus.corpus import RESPONSE_CONTROL, RESPONSE_OK, UtteranceCase, all_cases

STT_CORPUS_VERSION: Final = 1

#: ADR-0224: "Target on this corpus: >= 95 % correct ... and 0 wrong-device actions".
TARGET_CORRECT_RATE: Final = 0.95

BAND_HIGH: Final = "high"
BAND_MEDIUM: Final = "medium"
BAND_LOW: Final = "low"
#: The rule of the measurement: the meant reading at HIGH or MEDIUM, or the one question at LOW.
ANY_BAND: Final[tuple[str, ...]] = (BAND_HIGH, BAND_MEDIUM, BAND_LOW)

#: Readings that are no action at all.
NON_ACTING_INTENTS: Final[frozenset[str]] = frozenset({"none", "preference"})

ORIGIN_REAL: Final = "real"
ORIGIN_DERIVED: Final = "derived"

DISTORTION_POLITE: Final = "polite"
DISTORTION_DIACRITICS: Final = "diacritics"
DISTORTION_FUSED: Final = "fused"
DISTORTION_INVENTED_SUFFIX: Final = "invented_suffix"
DISTORTIONS: Final[tuple[str, ...]] = (
    DISTORTION_POLITE,
    DISTORTION_DIACRITICS,
    DISTORTION_FUSED,
    DISTORTION_INVENTED_SUFFIX,
)

#: The suffix the STT invented on 2026-09-30 ("Ofisü" for "Ofis").
INVENTED_SUFFIX: Final = "ü"

#: The imperative a canonical sentence ends in -> the polite form the STT writes for it.
IMPERATIVES: Final[dict[str, str]] = {
    "aç": "açın",
    "anlat": "anlatın",
    "artır": "artırın",
    "bak": "bakın",
    "bul": "bulun",
    "çiz": "çizin",
    "duraklat": "duraklatın",
    "düzelt": "düzeltin",
    "et": "edin",
    "hatırla": "hatırlayın",
    "hazırla": "hazırlayın",
    "kapat": "kapatın",
    "kur": "kurun",
    "oluştur": "oluşturun",
    "uyandır": "uyandırın",
    "yap": "yapın",
}

#: The trial's own family, beside the per-category rule: the plain sentence of 2026-09-30 and
#: the two canonical sentences that NAME a machine - the slot the trial broke.
TRIAL_FAMILY_BASES: Final[tuple[str, ...]] = ("op.app.8", "op.app.office.1", "op.app.home.1")


@dataclass(frozen=True, slots=True)
class SttCase:
    case_id: str
    #: The sentence as the STT wrote it - what the relay receives.
    rendering: str
    #: The sentence the owner said (for a derived case: the canonical case's own sentence).
    meant: str
    #: What the owner meant. ``intent`` is the router's intent name; ``preference`` is the
    #: one reading that must change nothing (a standing preference is not an action).
    intent: str
    tool: str | None
    #: The entity: the application's contract id ("calc"), where the sentence names one.
    application: str | None = None
    #: The machine the owner NAMED, as its alias word; None = the machine the session is on.
    device: str | None = None
    #: The bands the meant reading may arrive in. LOW means: the one question, nothing run.
    bands: tuple[str, ...] = ANY_BAND
    origin: str = ORIGIN_DERIVED
    distortion: str | None = None
    #: The ``corpus.py`` case a derived rendering was made from.
    base_case_id: str | None = None
    #: Real cases only: the day production heard it.
    heard_at: str | None = None
    notes: str = ""

    @property
    def acts(self) -> bool:
        """False for the one reading that must DO nothing. A control intent ("Devam et.")
        names no tool and is still an action: the relay itself carries it out."""
        return self.intent not in NON_ACTING_INTENTS


# ------------------------------------------------------------------ the real renderings


def _real_cases() -> list[SttCase]:
    """The trial of 2026-09-30 20:11 UTC (QUALIFICATION 30.10 notes a-c), verbatim."""
    return [
        SttCase(
            case_id="stt.real.20260930.office_calc",
            rendering="Ofisü bilgisayarında hesap makinesini açın.",
            meant="Ofis bilgisayarında hesap makinesini aç.",
            intent="app_open",
            tool="operator.app_open",
            application="calc",
            device="ofis",
            # A word the STT invented is never HIGH: read back, or asked - and never the
            # machine the session happens to be on (it ran on MAIL that evening).
            bands=(BAND_MEDIUM, BAND_LOW),
            origin=ORIGIN_REAL,
            heard_at="2026-09-30",
            notes="invented suffix (Ofisü) and the polite imperative (açın) in one sentence",
        ),
        SttCase(
            case_id="stt.real.20260930.language_preference",
            rendering="Bundan sonra araştırma raporlarını her zaman Türkçe oku",
            meant="Bundan sonra araştırma raporlarını her zaman Türkçe oku.",
            intent="preference",
            tool=None,
            origin=ORIGIN_REAL,
            heard_at="2026-09-30",
            notes="a standing preference; it was bent into the answer mode (ADR-0232)",
        ),
        SttCase(
            case_id="stt.real.20260930.plain_calc",
            rendering="Hesap makinesini aç",
            meant="Hesap makinesini aç.",
            intent="app_open",
            tool="operator.app_open",
            application="calc",
            bands=(BAND_HIGH,),
            origin=ORIGIN_REAL,
            heard_at="2026-09-30",
            notes="an exact closed form: done, the receipt as always",
        ),
    ]


# --------------------------------------------------------------- the derived renderings

#: base case id -> {distortion: the rendering}. Written out, not generated: every line is a
#: sentence a reader can check against its base, and the unit test checks its shape.
_DERIVED: Final[dict[str, dict[str, str]]] = {
    "c.collision.alarm_create": {
        DISTORTION_POLITE: "Saat yedi buçukta beni uyandırın.",
        DISTORTION_DIACRITICS: "Saat yedi bucukta beni uyandir.",
        DISTORTION_FUSED: "Saat yedibuçukta beni uyandır.",
        DISTORTION_INVENTED_SUFFIX: "Saatü yedi buçukta beni uyandır.",
    },
    "r.create.1": {
        DISTORTION_POLITE: "Her sabah 08:00'de bana haberleri okuyan bir rutin kurun.",
        DISTORTION_FUSED: "Her sabah 08:00'de bana haberleri okuyan birrutin kur.",
        DISTORTION_INVENTED_SUFFIX: "Her sabahü 08:00'de bana haberleri okuyan bir rutin kur.",
    },
    "macro.start.1": {
        DISTORTION_POLITE: "Yeni hareket oluşturun.",
        DISTORTION_DIACRITICS: "Yeni hareket olustur.",
        DISTORTION_FUSED: "Yenihareket oluştur.",
        DISTORTION_INVENTED_SUFFIX: "Yeni hareketü oluştur.",
    },
    "m.remember.1": {
        DISTORTION_POLITE: "Kahveyi sade severim, bunu hatırlayın.",
        DISTORTION_DIACRITICS: "Kahveyi sade severim, bunu hatirla.",
        DISTORTION_FUSED: "Kahveyi sadeseverim, bunu hatırla.",
        DISTORTION_INVENTED_SUFFIX: "Kahveyi sade severimü, bunu hatırla.",
    },
    "d.inbox.1": {
        DISTORTION_POLITE: "Maillerime bakın.",
        DISTORTION_FUSED: "Maillerimebak.",
    },
    "r.tech.1": {
        DISTORTION_POLITE: "Bunu teknik anlatın.",
        DISTORTION_FUSED: "Bunuteknik anlat.",
        DISTORTION_INVENTED_SUFFIX: "Bunu teknikü anlat.",
    },
    "a.create.1": {
        DISTORTION_POLITE: "Yarın 7:30'da beni uyandırın.",
        DISTORTION_DIACRITICS: "Yarin 7:30'da beni uyandir.",
        DISTORTION_FUSED: "Yarın 7:30'da beniuyandır.",
        DISTORTION_INVENTED_SUFFIX: "Yarınü 7:30'da beni uyandır.",
    },
    "d.off.1": {
        DISTORTION_POLITE: "Ekranları kapatın.",
        DISTORTION_DIACRITICS: "Ekranlari kapat.",
        DISTORTION_FUSED: "Ekranlarıkapat.",
    },
    "am.1": {
        DISTORTION_POLITE: "Uyurken ekranları kapatın.",
        DISTORTION_DIACRITICS: "Uyurken ekranlari kapat.",
        DISTORTION_FUSED: "Uyurkenekranları kapat.",
        DISTORTION_INVENTED_SUFFIX: "Uyurkenü ekranları kapat.",
    },
    "e.off.1": {
        DISTORTION_POLITE: "Gözünü kapatın.",
        DISTORTION_DIACRITICS: "Gozunu kapat.",
        DISTORTION_FUSED: "Gözünükapat.",
    },
    "c.resume.1": {
        DISTORTION_POLITE: "Devam edin.",
        DISTORTION_FUSED: "Devamet.",
        DISTORTION_INVENTED_SUFFIX: "Devamü et.",
    },
    "ev.pause.1": {
        DISTORTION_POLITE: "Kendi kendini geliştirmeyi duraklatın.",
        DISTORTION_DIACRITICS: "Kendi kendini gelistirmeyi duraklat.",
        DISTORTION_FUSED: "Kendikendini geliştirmeyi duraklat.",
    },
    "selfdev.fix.canonical": {
        DISTORTION_POLITE: "Şu bug'ı kendin düzeltin.",
        DISTORTION_DIACRITICS: "Su bug'i kendin duzelt.",
        DISTORTION_FUSED: "Şubug'ı kendin düzelt.",
        DISTORTION_INVENTED_SUFFIX: "Şu bug'ı kendinü düzelt.",
    },
    "op.app.1": {
        DISTORTION_POLITE: "Not Defteri'ni açın.",
        DISTORTION_DIACRITICS: "Not Defteri'ni ac.",
        DISTORTION_FUSED: "NotDefteri'ni aç.",
        DISTORTION_INVENTED_SUFFIX: "Notü Defteri'ni aç.",
    },
    "op.app.8": {
        DISTORTION_POLITE: "Hesap makinesini açın.",
        DISTORTION_DIACRITICS: "Hesap makinesini ac.",
        DISTORTION_FUSED: "Hesapmakinesini aç.",
        DISTORTION_INVENTED_SUFFIX: "Hesapü makinesini aç.",
    },
    "op.app.office.1": {
        DISTORTION_POLITE: "Ofis bilgisayarımda hesap makinesini açın.",
        DISTORTION_DIACRITICS: "Ofis bilgisayarimda hesap makinesini ac.",
        DISTORTION_FUSED: "Ofisbilgisayarımda hesap makinesini aç.",
        DISTORTION_INVENTED_SUFFIX: "Ofisü bilgisayarımda hesap makinesini aç.",
    },
    "op.app.home.1": {
        DISTORTION_POLITE: "Ev bilgisayarımda hesap makinesini açın.",
        DISTORTION_DIACRITICS: "Ev bilgisayarimda hesap makinesini ac.",
        DISTORTION_FUSED: "Evbilgisayarımda hesap makinesini aç.",
        DISTORTION_INVENTED_SUFFIX: "Evü bilgisayarımda hesap makinesini aç.",
    },
    "doc.search.1": {
        DISTORTION_POLITE: "Bu klasördeki PDF'leri bulun.",
        DISTORTION_DIACRITICS: "Bu klasordeki PDF'leri bul.",
        DISTORTION_FUSED: "Buklasördeki PDF'leri bul.",
    },
    "mc.search.1": {
        DISTORTION_POLITE: "Fatura maillerini bulun.",
        DISTORTION_FUSED: "Faturamaillerini bul.",
    },
    "art.create.document": {
        DISTORTION_POLITE: "Toplantı notlarını Word belgesi yapın.",
        DISTORTION_DIACRITICS: "Toplanti notlarini Word belgesi yap.",
        DISTORTION_FUSED: "Toplantınotlarını Word belgesi yap.",
        DISTORTION_INVENTED_SUFFIX: "Toplantı notlarını Wordü belgesi yap.",
    },
    "app.create.tracker": {
        DISTORTION_POLITE: "Bana bir görev takip uygulaması yapın.",
        DISTORTION_DIACRITICS: "Bana bir gorev takip uygulamasi yap.",
        DISTORTION_FUSED: "Bana bir görevtakip uygulaması yap.",
        DISTORTION_INVENTED_SUFFIX: "Bana bir görevü takip uygulaması yap.",
    },
    "genesis.request.increment": {
        DISTORTION_POLITE: "Sayaç kutusunu bir artırın.",
        DISTORTION_DIACRITICS: "Sayac kutusunu bir artir.",
        DISTORTION_FUSED: "Sayaçkutusunu bir artır.",
        DISTORTION_INVENTED_SUFFIX: "Sayaçü kutusunu bir artır.",
    },
    "scene.create.blender.canonical": {
        DISTORTION_POLITE: "Blender'da yeni sahne açın.",
        DISTORTION_DIACRITICS: "Blender'da yeni sahne ac.",
        DISTORTION_FUSED: "Blender'da yenisahne aç.",
    },
    "exec.start.research": {
        DISTORTION_POLITE: (
            "Son üç gündeki AI gelişmelerini araştır, bana etkisini çıkar, "
            "Word raporu ve sunum hazırlayın."
        ),
        DISTORTION_DIACRITICS: (
            "Son uc gundeki AI gelismelerini arastir, bana etkisini cikar, "
            "Word raporu ve sunum hazirla."
        ),
        DISTORTION_FUSED: (
            "Sonüç gündeki AI gelişmelerini araştır, bana etkisini çıkar, "
            "Word raporu ve sunum hazırla."
        ),
        DISTORTION_INVENTED_SUFFIX: (
            "Son üç gündeki AI gelişmelerini araştır, bana etkisini çıkar, "
            "Word raporu ve sunumü hazırla."
        ),
    },
    "location.default.set.1": {
        DISTORTION_POLITE: "Varsayılan hava durumu konumumu İstanbul yapın.",
        DISTORTION_DIACRITICS: "Varsayilan hava durumu konumumu Istanbul yap.",
        DISTORTION_FUSED: "Varsayılan havadurumu konumumu İstanbul yap.",
        DISTORTION_INVENTED_SUFFIX: "Varsayılan hava durumu konumumu İstanbulü yap.",
    },
    "n.open.1": {
        DISTORTION_POLITE: "Haberleri açın.",
        DISTORTION_DIACRITICS: "Haberleri ac.",
        DISTORTION_FUSED: "Haberleriaç.",
    },
    "m.play.1": {
        DISTORTION_POLITE: "YouTube'dan 'Doğum günün kutlu olsun Kadir' açın.",
        DISTORTION_DIACRITICS: "YouTube'dan 'Dogum gunun kutlu olsun Kadir' ac.",
        DISTORTION_FUSED: "YouTube'dan 'Doğumgünün kutlu olsun Kadir' aç.",
        DISTORTION_INVENTED_SUFFIX: "YouTube'dan 'Doğum günün kutlu olsun Kadirü' aç.",
    },
    "creative.redraw.canonical": {
        DISTORTION_POLITE: "Bu resmi Paint'te yeniden çizin.",
        DISTORTION_DIACRITICS: "Bu resmi Paint'te yeniden ciz.",
        DISTORTION_FUSED: "Buresmi Paint'te yeniden çiz.",
        DISTORTION_INVENTED_SUFFIX: "Bu resmi Paint'te yenidenü çiz.",
    },
    "nativeapps.create.win.canonical": {
        DISTORTION_POLITE: "Bana Windows için masaüstü uygulaması yapın.",
        DISTORTION_DIACRITICS: "Bana Windows icin masaustu uygulamasi yap.",
        DISTORTION_FUSED: "Bana Windows için masaüstüuygulaması yap.",
        DISTORTION_INVENTED_SUFFIX: "Bana Windowsü için masaüstü uygulaması yap.",
    },
}

#: What the trial family's sentences name, beside the intent the canonical case carries: the
#: application as its contract id and the machine as its alias word.
_ENTITIES: Final[dict[str, tuple[str | None, str | None]]] = {
    "op.app.1": ("notepad", None),
    "op.app.8": ("calc", None),
    "op.app.office.1": ("calc", "ofis"),
    "op.app.home.1": ("calc", "ev"),
}


#: The measurement of 2026-10-01 (main 858c3e0b, layers 1-3, no layer-2 engine): 73 of 106
#: correct = 68.9 %, 0 wrong-device actions - BELOW the 95 % target. These are the cases that
#: were not correct, each with the verdict it got. It is a RATCHET, not an excuse: the unit
#: test holds the failing set EQUAL to this table, so a case that starts failing names itself
#: and a case the layers learn to read has to be taken out of here. The target test stays in
#: the suite as a strict expected failure until this table is short enough to meet it.
#: ``wrong_reading`` here is always at HIGH: another intent's rule matched the distorted
#: sentence exactly, which is the trial's own shape (the wrong thing, with full confidence).
KNOWN_GAPS: Final[dict[str, str]] = {
    "stt.derived.c.collision.alarm_create.fused": "wrong_reading",
    "stt.derived.r.create.1.fused": "not_understood",
    "stt.derived.macro.start.1.fused": "not_understood",
    "stt.derived.d.inbox.1.polite": "not_understood",
    "stt.derived.d.inbox.1.fused": "not_understood",
    "stt.derived.r.tech.1.fused": "not_understood",
    "stt.derived.a.create.1.fused": "not_understood",
    "stt.derived.d.off.1.polite": "not_understood",
    "stt.derived.d.off.1.fused": "not_understood",
    "stt.derived.am.1.fused": "not_understood",
    "stt.derived.e.off.1.polite": "not_understood",
    "stt.derived.e.off.1.fused": "not_understood",
    "stt.derived.ev.pause.1.polite": "wrong_reading",
    "stt.derived.selfdev.fix.canonical.fused": "wrong_reading",
    "stt.derived.selfdev.fix.canonical.invented_suffix": "wrong_reading",
    "stt.derived.op.app.1.fused": "not_understood",
    "stt.derived.op.app.1.invented_suffix": "wrong_reading",
    "stt.derived.op.app.8.fused": "not_understood",
    "stt.derived.op.app.8.invented_suffix": "wrong_reading",
    "stt.derived.doc.search.1.polite": "not_understood",
    "stt.derived.mc.search.1.polite": "not_understood",
    "stt.derived.mc.search.1.fused": "not_understood",
    "stt.derived.art.create.document.polite": "not_understood",
    "stt.derived.app.create.tracker.polite": "not_understood",
    "stt.derived.genesis.request.increment.polite": "not_understood",
    "stt.derived.scene.create.blender.canonical.polite": "not_understood",
    "stt.derived.scene.create.blender.canonical.fused": "wrong_reading",
    "stt.derived.location.default.set.1.polite": "not_understood",
    "stt.derived.n.open.1.polite": "not_understood",
    "stt.derived.n.open.1.fused": "not_understood",
    "stt.derived.creative.redraw.canonical.fused": "wrong_reading",
    "stt.derived.nativeapps.create.win.canonical.polite": "not_understood",
    "stt.derived.nativeapps.create.win.canonical.fused": "not_understood",
}


def canonical_bases() -> dict[str, UtteranceCase]:
    """The ``corpus.py`` cases the derived renderings were made from, by case id."""
    by_id = {case.case_id: case for case in all_cases()}
    return {base_id: by_id[base_id] for base_id in _DERIVED}


def rule_bases() -> list[str]:
    """The bases the stated rule picks from ``corpus.py`` (module docstring), in corpus order."""
    picked: dict[str, str] = {}
    for case in all_cases():
        if case.category in picked:
            continue
        words = case.utterance.split()
        if (
            case.source == "canonical"
            and not case.preceding_turns
            and case.expected_intent not in (None, "none")
            and case.expected_response in (RESPONSE_OK, RESPONSE_CONTROL)
            and len(words) >= 2
            and words[-1].rstrip(".!").casefold() in IMPERATIVES
        ):
            picked[case.category] = case.case_id
    return list(picked.values())


def _derived_cases() -> list[SttCase]:
    bases = canonical_bases()
    cases: list[SttCase] = []
    for base_id, renderings in _DERIVED.items():
        base = bases[base_id]
        application, device = _ENTITIES.get(base_id, (None, None))
        for distortion, rendering in renderings.items():
            cases.append(
                SttCase(
                    case_id=f"stt.derived.{base_id}.{distortion}",
                    rendering=rendering,
                    meant=base.utterance,
                    intent=str(base.expected_intent),
                    tool=base.expected_tool,
                    application=application,
                    device=device,
                    origin=ORIGIN_DERIVED,
                    distortion=distortion,
                    base_case_id=base_id,
                )
            )
    return cases


def all_stt_cases() -> list[SttCase]:
    return [*_real_cases(), *_derived_cases()]


__all__ = [
    "ANY_BAND",
    "BAND_HIGH",
    "BAND_LOW",
    "BAND_MEDIUM",
    "DISTORTIONS",
    "DISTORTION_DIACRITICS",
    "DISTORTION_FUSED",
    "DISTORTION_INVENTED_SUFFIX",
    "DISTORTION_POLITE",
    "IMPERATIVES",
    "INVENTED_SUFFIX",
    "KNOWN_GAPS",
    "NON_ACTING_INTENTS",
    "ORIGIN_DERIVED",
    "ORIGIN_REAL",
    "STT_CORPUS_VERSION",
    "TARGET_CORRECT_RATE",
    "TRIAL_FAMILY_BASES",
    "SttCase",
    "all_stt_cases",
    "canonical_bases",
    "rule_bases",
]
