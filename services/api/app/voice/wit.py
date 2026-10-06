"""Personality, dry wit: ONE rule for when a remark may come, how much, and where it never does.

The ROADMAP's JARVIS row reads "Personality, dry wit: tune, never at the cost of truthful
speech (ADR-0063)". Left unwritten, a model either never makes the remark or makes it in the
wrong place - a joke over a failed transfer is worse than no personality at all. So the rule
is written once, here, and the two prompts that speak to the owner carry this same object:

* the paid realtime persona (``app.voice.realtime_sessions.persona.build_instructions``);
* the local mode's chat prompt (``app.assistant_chat``).

The owner's ``humor`` preference ('dry' | 'off', ``app.voice.preferences``) switches it off in
both. This module imports none of its readers, on purpose: they import it.

Whether a model actually lands a remark well is heard by the owner, not measured by a test
(READY_FOR_OWNER); the tests prove the text, the single source, the order and the switch.
"""

from __future__ import annotations

from typing import Any, Final

HUMOR_DRY: Final = "dry"
HUMOR_OFF: Final = "off"
HUMOR_VALUES: Final[tuple[str, ...]] = (HUMOR_DRY, HUMOR_OFF)

#: Where a remark NEVER goes. Each is named, word for word, in :data:`WIT_TR`.
WIT_FORBIDDEN_ZONES_TR: Final[tuple[str, ...]] = (
    "hata ve başarısızlık bildirimi",
    "güvenlik, kimlik ve yetki",
    "para ve harcama",
    "sağlık",
    "alarm ve uyandırma",
    "acil ya da önemli bildirim",
    "bir aracın döndürdüğü 'speech' metni",
    "brifing",
    "anlatım",
    "araştırma sonucu",
    "sahibin 'ciddi ol' ya da 'espri yapma' dediği tur",
)

WIT_TR: Final = (
    "Mizah: kuru, alçak sesli, abartısız ve ender bir nükte yapabilirsin; bir yanıtta en çok "
    "bir kısa cümle, çoğu yanıtta hiç. Önce iş gelir, nükte ancak işin yanında durur. "
    "Sahibini küçümseyen, alaycı ya da şakayı sahibinin üstüne kuran hiçbir şey söyleme. "
    "Nükte ASLA şu yerlerde yapılmaz: hata ve başarısızlık bildirimi; güvenlik, kimlik ve "
    "yetki; para ve harcama; sağlık; alarm ve uyandırma; acil ya da önemli bildirim; brifing, "
    "anlatım ve araştırma sonucu; sahibin 'ciddi ol' ya da 'espri yapma' dediği tur; "
    "bir aracın döndürdüğü 'speech' metni. "
    "Bir aracın döndürdüğü 'speech' metnini aynen oku, önüne ya da arkasına nükte ekleme. "
    "Nükte gerçeği değiştirmez, bir olguyu süslemez ve yapılmamış bir iş için asla "
    "'yaptım' demez; söylediğin her olgu doğru kalır. "
    "Belirsiz kaldığında nükte değil, tek kısa bir soru sor."
)

#: The closed list the contract test looks for in :data:`WIT_TR` (Turkish-casefolded).
WIT_REQUIRED_PHRASES: Final[tuple[str, ...]] = (
    "en çok bir",
    "aynen oku",
    "'speech' metni",
    "para",
    "güvenlik",
    "hata",
    "alarm",
    "sağlık",
    "önemli bildirim",
    "ciddi ol",
    "espri yapma",
    "'yaptım' demez",
    "küçümseyen",
    "tek kısa bir soru",
)


def normalize_humor(value: Any) -> str:
    """'off' (any case, any surrounding space) stays off; everything else is 'dry'."""
    if isinstance(value, str) and value.strip().lower() == HUMOR_OFF:
        return HUMOR_OFF
    return HUMOR_DRY


__all__ = [
    "HUMOR_DRY",
    "HUMOR_OFF",
    "HUMOR_VALUES",
    "WIT_FORBIDDEN_ZONES_TR",
    "WIT_REQUIRED_PHRASES",
    "WIT_TR",
    "normalize_humor",
]
