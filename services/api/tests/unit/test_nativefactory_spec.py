"""``NativeAppSpec`` and the generator: what the factory will build, and what it refuses.

The bar this suite holds is the one ADR-0095 sets: a target the toolchain cannot reach is
refused BY NAME rather than attempted, and no owner text ever reaches a project file.
"""

from __future__ import annotations

import pytest

from app.appfactory.validation import validate_files
from app.nativefactory.generator import (
    RENDERABLE_TEMPLATES,
    render,
    template_slot_names,
)
from app.nativefactory.spec import (
    NATIVE_STACKS,
    NATIVE_TARGETS,
    STACK_TARGETS,
    TEMPLATE_COUNTER_MOBILE,
    TEMPLATE_FEATURES,
    TEMPLATE_NOTES_DESKTOP,
    TEMPLATE_STACK,
    NativeFactoryError,
    parse_spec,
    slug_for_name,
)

NOTES = {
    "name": "Notlarim",
    "title": "Notlarım",
    "template": TEMPLATE_NOTES_DESKTOP,
    "targets": ["windows_exe"],
    "version": "0.1.0",
    "persistence": "local_file",
    "features": ["add_item", "list_items", "delete_item", "persist_local"],
}


# --------------------------------------------------------------------------- the spec


def test_the_owner_shaped_request_becomes_a_buildable_spec() -> None:
    spec = parse_spec(NOTES)
    assert spec.slug == "notlarim"
    assert spec.resolved_stack == "dotnet_wpf"
    assert spec.assembly_version == "0.1.0.0"
    assert spec.display_title == "Notlarım"


@pytest.mark.parametrize(
    "field,value",
    [
        ("targets", ["ios_project"]),
        ("targets", ["android_apk"]),  # right target, wrong stack for this template
        ("template", "no-such-template"),
        ("version", "1.0"),
        ("version", "1.0.0-beta"),
        ("features", ["counter"]),  # a feature this template does not have
        ("stack", "dotnet_maui"),  # no MAUI workload exists here
    ],
)
def test_what_the_factory_refuses(field: str, value: object) -> None:
    with pytest.raises(NativeFactoryError) as caught:
        parse_spec({**NOTES, field: value})
    assert caught.value.error_class == "invalid_spec"
    # The refusal is a Turkish sentence the owner can hear, not a stack trace.
    assert caught.value.speech.startswith("Uygulama tanımı geçersiz")


def test_ios_is_not_a_target_value_at_all() -> None:
    """ADR-0095 decision 3. Not "a target that is unavailable today" - a target that can
    never be satisfied here, so offering it would generate something that looks like
    progress toward something that cannot happen."""
    assert not any("ios" in target for target in NATIVE_TARGETS)


def test_maui_is_not_a_stack() -> None:
    """Measured 2026-09-09: no MAUI workload, and installing one is a download the
    assistant does not start."""
    assert "dotnet_maui" not in NATIVE_STACKS


@pytest.mark.parametrize("template", sorted(TEMPLATE_STACK))
def test_every_template_is_pinned_to_a_stack_that_can_reach_its_targets(template: str) -> None:
    stack = TEMPLATE_STACK[template]
    assert stack in NATIVE_STACKS
    assert STACK_TARGETS[stack], f"{stack} can reach no target at all"
    assert TEMPLATE_FEATURES[template], f"{template} offers no features"


def test_a_name_that_could_close_a_xaml_tag_is_refused_not_escaped() -> None:
    """An owner asking for a `<` in an application name has made a mistake worth telling
    them about - and escaping it silently would put the decision in the wrong place."""
    with pytest.raises(NativeFactoryError):
        parse_spec({**NOTES, "title": 'Notlar" />< Window'})


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Notlarim", "notlarim"),
        ("Notlarım", "notlarim"),
        ("Görev Takip", "gorev-takip"),
        ("   ", "pagentos-app"),
        ("日本語", "pagentos-app"),
    ],
)
def test_the_slug_is_a_closed_alphabet_identity(name: str, expected: str) -> None:
    """It becomes a directory name, an assembly name and a file name at once, so nothing
    downstream should ever have to escape it."""
    assert slug_for_name(name) == expected


# ----------------------------------------------------------------------- the generator


def test_the_generated_project_passes_the_M23_policy_unchanged() -> None:
    """One policy for both factories. A native project that needed a more permissive
    validator would be a native project nobody had checked."""
    report = validate_files(render(parse_spec(NOTES)))
    assert report.ok, report.errors


def test_every_slot_the_templates_use_is_one_the_renderer_fills() -> None:
    """The cross-file guard, in the direction that has actually gone wrong here: a template
    growing a slot the renderer does not know renders a literal `{{WHATEVER}}` into shipped
    source. Reads the OTHER side rather than a hand-kept list."""
    from app.nativefactory.generator import _slots_for

    known = set(_slots_for(parse_spec(NOTES)))
    for template in RENDERABLE_TEMPLATES:
        used = template_slot_names(template)
        assert used <= known, f"{template} uses slots the renderer cannot fill: {used - known}"


def test_nothing_of_the_owners_text_reaches_a_code_file() -> None:
    """The display name may appear in XAML (escaped by the format) and in the csproj's
    metadata. It may NOT appear in a `.cs` file, where it would be code."""
    spec = parse_spec({**NOTES, "title": "Benim Notlarım"})
    for file in render(spec).files:
        if file.path.endswith(".cs"):
            assert "Benim Notlarım" not in file.text, file.path


def test_a_csharp_file_that_uses_system_io_says_so() -> None:
    """The defect the first real build found, kept as a static guard.

    A WPF project's implicit usings do NOT include `System.IO`, so the generated
    `NoteStore.cs` failed with eleven CS0103 errors on the first `dotnet build`. Catching it
    here means CI catches the class without a compiler - the lab proves the build, this
    proves the rule.
    """
    spec = parse_spec(NOTES)
    for file in render(spec).files:
        if not file.path.endswith(".cs"):
            continue
        uses_io = any(
            f"{name}." in file.text for name in ("File", "Directory", "Path")
        )
        if uses_io:
            assert "using System.IO;" in file.text, (
                f"{file.path} uses System.IO types without importing them - "
                "implicit usings do not cover it in a WPF project"
            )


def test_the_ui_carries_automation_ids_so_the_operator_can_drive_it() -> None:
    """"The operator drove it" is this milestone's own acceptance, and a control nothing
    can name cannot be driven."""
    xaml = render(parse_spec(NOTES)).get("src/notlarim/MainWindow.xaml")
    assert xaml is not None
    for control in ("NoteInput", "AddButton", "NoteList", "DeleteButton", "StatusText"):
        assert f'AutomationProperties.AutomationId="{control}"' in xaml


def test_the_manifest_names_files_the_project_actually_carries() -> None:
    """The lab builds what the manifest names, so a manifest naming a file that is not
    there is a build failure discovered late. Here it is a test failure discovered early."""
    import json

    project = render(parse_spec(NOTES))
    manifest = json.loads(project.get("manifest.json") or "{}")
    paths = project.path_set()
    assert manifest["entry"] in paths
    assert manifest["tests"] in paths


def test_the_android_template_refuses_by_name_rather_than_generating_a_dead_project() -> None:
    """No JDK on this machine (owner item 33). A template whose build can only fail is
    worse than an honest refusal - it looks like progress."""
    spec = parse_spec(
        {
            "name": "Sayac",
            "template": TEMPLATE_COUNTER_MOBILE,
            "targets": ["android_apk"],
            "features": ["counter"],
        }
    )
    with pytest.raises(NativeFactoryError) as caught:
        render(spec)
    assert caught.value.error_class == "template_unavailable"
    assert "33" in caught.value.speech
