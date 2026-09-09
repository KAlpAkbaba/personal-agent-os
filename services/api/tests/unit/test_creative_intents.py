"""The Creative Tools Operator's own router matches (docs/M27_CREATIVE_TOOLS_SPEC.md
§5). Every case here also proves the THREE real vocabulary collisions found while
writing this family stay resolved (module comment above
``app.voice.intents._creative_redraw_match``):

1. "Arka planını kaldır." must resolve to CREATIVE_BACKGROUND, never a bare-``kaldır``
   ALARM_CREATE fallback.
2. The SAME utterance must never be swallowed by ``_technical_match``'s own "arka
   planda" phrase (checked much later, under a different intent entirely).
3. "Bunu Photoshop'ta aç." must resolve to CREATIVE_OPEN (a tool word is present); a
   bare "Bunu aç." (no tool word) must fall through UNCHANGED to ARTIFACT_OPEN.
"""

from __future__ import annotations

from app.voice.intents import Intent, resolve_intent


def test_redraw() -> None:
    resolved = resolve_intent("Bu resmi Paint'te yeniden çiz.")
    assert resolved.intent == Intent.CREATIVE_REDRAW
    assert resolved.creative_tool == "paint"


def test_redraw_with_deictic_pronoun_instead_of_image_noun() -> None:
    """A real bug found via the corpus's own paraphrase generation (2026-09-09):
    "Bunu Paint'te yeniden çiz." names the object with a deictic pronoun, not the
    "resim" noun the first version of this matcher required, and fell through to
    the plain REPEAT control intent ("yeniden" is also that word)."""
    resolved = resolve_intent("Bunu Paint'te yeniden çiz.")
    assert resolved.intent == Intent.CREATIVE_REDRAW


def test_redraw_without_explicit_tool_leaves_tool_unresolved() -> None:
    resolved = resolve_intent("Bu resmi yeniden çiz.")
    assert resolved.intent == Intent.CREATIVE_REDRAW
    assert resolved.creative_tool is None


def test_open_with_tool_word() -> None:
    resolved = resolve_intent("Bunu Photoshop'ta aç.")
    assert resolved.intent == Intent.CREATIVE_OPEN
    assert resolved.creative_tool == "photoshop"


def test_open_illustrator() -> None:
    resolved = resolve_intent("Bunu Illustrator'da aç.")
    assert resolved.intent == Intent.CREATIVE_OPEN
    assert resolved.creative_tool == "illustrator"


def test_bare_open_falls_through_to_artifact_open() -> None:
    """The collision this family's own priority position exists to resolve (module
    docstring, case 3): no tool word means this is M19/artifact's generic open, never
    a creative tool."""
    resolved = resolve_intent("Bunu aç.")
    assert resolved.intent == Intent.ARTIFACT_OPEN


def test_background_removal() -> None:
    resolved = resolve_intent("Arka planını kaldır.")
    assert resolved.intent == Intent.CREATIVE_BACKGROUND


def test_background_removal_never_becomes_alarm_create() -> None:
    """The regression this whole family's priority placement exists for (module
    docstring, case 1): a bare ``kaldır`` is the alarm resolver's own wake fallback,
    and this phrase must never reach it."""
    resolved = resolve_intent("Arka planını kaldır.")
    assert resolved.intent != Intent.ALARM_CREATE


def test_background_removal_never_becomes_technical_explanation() -> None:
    """Case 2: "arka" + a "plan"-prefixed token is also ``_technical_match``'s own
    "arka planda" phrase — checked far later in resolve_intent's body, and must
    never be reached because this family's own match wins first."""
    resolved = resolve_intent("Arka planını kaldır.")
    assert resolved.intent != Intent.TECHNICAL


def test_bare_kaldir_without_creative_context_is_unaffected() -> None:
    """This family must never widen what "kaldır" alone means elsewhere — only
    "arka" + a plan-shaped token together are gated to CREATIVE_BACKGROUND."""
    resolved = resolve_intent("Beni kaldır.")
    assert resolved.intent != Intent.CREATIVE_BACKGROUND


def test_background_removal_question_form() -> None:
    """A real bug found via the corpus's own paraphrase generation (2026-09-09):
    "kaldırır mısın" ("would you remove...") is not in the exact form list, and the
    utterance fell through to the alarm resolver's own bare-``kaldır`` wake
    fallback ("kaldırır".startswith("kaldır") is exactly the kind of prefix match
    that resolver's OWN verb stems use, unlike this family's exact-form check)."""
    resolved = resolve_intent("Arka planını kaldırır mısın?")
    assert resolved.intent == Intent.CREATIVE_BACKGROUND


def test_adjust_colors() -> None:
    resolved = resolve_intent("Renkleri biraz düzelt.")
    assert resolved.intent == Intent.CREATIVE_ADJUST


def test_adjust_never_collides_with_scene_material() -> None:
    resolved = resolve_intent("Küpü kırmızı yap.")
    assert resolved.intent != Intent.CREATIVE_ADJUST


def test_adjust_colors_question_form() -> None:
    resolved = resolve_intent("Renkleri düzeltir misin?")
    assert resolved.intent == Intent.CREATIVE_ADJUST


def test_cleanup_logo() -> None:
    resolved = resolve_intent("Logoyu daha temiz hale getir.")
    assert resolved.intent == Intent.CREATIVE_CLEANUP


def test_cleanup_never_collides_with_window_restore() -> None:
    """ "getir" alone is WINDOW_RESTORE's own verb ("pencereyi eski haline getir");
    the "temiz" gate keeps the two disjoint."""
    resolved = resolve_intent("Pencereyi eski haline getir.")
    assert resolved.intent != Intent.CREATIVE_CLEANUP
    assert resolved.intent == Intent.WINDOW_RESTORE


def test_design_with_figma() -> None:
    resolved = resolve_intent("Figma'da buna benzeyen bir arayüz tasarla.")
    assert resolved.intent == Intent.CREATIVE_DESIGN
    assert resolved.creative_tool == "figma"


def test_export_png() -> None:
    resolved = resolve_intent("Bunu PNG olarak dışa aktar.")
    assert resolved.intent == Intent.CREATIVE_EXPORT
    assert resolved.creative_format == "png"


def test_export_never_collides_with_research_telling() -> None:
    """ "aktar" alone is ``_RESEARCH_TELLING_VERBS``' own word, but that branch also
    requires a research topic word — and this family is checked first regardless."""
    resolved = resolve_intent("Araştırmayı bana aktar.")
    assert resolved.intent != Intent.CREATIVE_EXPORT


def test_negative_delete_original_names_no_tool() -> None:
    """ "Orijinali sil" names no creative tool at all (spec §5's own negative case)."""
    resolved = resolve_intent("Orijinali sil.")
    assert resolved.intent not in (
        Intent.CREATIVE_REDRAW,
        Intent.CREATIVE_OPEN,
        Intent.CREATIVE_BACKGROUND,
        Intent.CREATIVE_ADJUST,
        Intent.CREATIVE_CLEANUP,
        Intent.CREATIVE_DESIGN,
        Intent.CREATIVE_EXPORT,
    )
