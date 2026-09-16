"""App-specific adapters: structural drivers for the applications the operator knows
(B29 req 116, docs/M19_DIGITAL_OPERATOR_SPEC.md §2's ladder: UI Automation before keys).

An adapter is a small, declared fact table about ONE application: how its main control is
found in the UI Automation tree, what its dialog buttons are called, and which spoken
words the owner uses for its parts. It is not a plan and it is not a heuristic - it is the
same shape as ``app.operator.plans.APP_ALLOWLIST``: the single place that says what this
operator knows about Notepad, read by the tool that turns "belgeyi oku" into a query the
device can answer.

Why declared rather than discovered: ``ui.inspect`` can walk any tree, and the owner's
"Tamam düğmesine tıkla" needs no adapter (a button named "Tamam" is found by name). What an
adapter adds is the knowledge a person has and a tree does not: that Notepad's text lives
in the ``Edit`` control named "Metin Düzenleyici", that its save prompt's buttons are
"Kaydet / Kaydetme / İptal" in this locale, that "belge" means that control. Each entry
is measured on a real device before it is written down, the same way the fake ``ui.inspect``
tree in ``tests/alarms_support.py`` was.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

#: ``ui.*`` query keys the companion accepts (``OperatorCapabilities.ReadQuery``).
QUERY_KEYS: Final[tuple[str, ...]] = ("automation_id", "name", "name_prefix", "control_type")


@dataclass(frozen=True, slots=True)
class AppAdapter:
    #: The process image the device reports (``window.image`` / ``app.list``), lower-case.
    image: str
    #: The Turkish name the owner hears.
    name_tr: str
    #: The query for the application's main content control, when it has one.
    document_query: dict[str, str] | None = None
    #: Dialog buttons by role -> the names they carry in this locale (first is preferred).
    dialog_buttons: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Spoken words for parts of the application -> the query that finds them.
    spoken_targets: dict[str, dict[str, str]] = field(default_factory=dict)
    #: B39 (req 126): the keyboard chords the application documents for the things a
    #: tree does not expose (an editor's quick-open), by role.
    shortcuts: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "image": self.image,
            "name_tr": self.name_tr,
            "document_query": dict(self.document_query) if self.document_query else None,
            "dialog_buttons": {k: list(v) for k, v in self.dialog_buttons.items()},
            "spoken_targets": {k: dict(v) for k, v in self.spoken_targets.items()},
            "shortcuts": {k: list(v) for k, v in self.shortcuts.items()},
        }


#: Measured on the owner's device (2026-09-09, the tree ``tests/alarms_support.py``
#: reproduces): Notepad's text is an ``Edit`` control named "Metin Düzenleyici" one node
#: below the window; the save prompt (ModalDetectionTests) names its buttons in the
#: system locale, Turkish first.
NOTEPAD: Final = AppAdapter(
    image="notepad.exe",
    name_tr="Not Defteri",
    document_query={"control_type": "Edit"},
    dialog_buttons={
        "save": ("Kaydet", "Save"),
        "dont_save": ("Kaydetme", "Don't Save"),
        "cancel": ("İptal", "Cancel"),
    },
    spoken_targets={
        "belge": {"control_type": "Edit"},
        "belgeyi": {"control_type": "Edit"},
        "metin": {"control_type": "Edit"},
        "metni": {"control_type": "Edit"},
        "yazı": {"control_type": "Edit"},
        "yazıyı": {"control_type": "Edit"},
    },
)

#: The Windows calculator: its display is a ``Text`` control named "Görüntülenen", its
#: digit and operator buttons are named in words ("Bir", "Artı", "Eşittir") in this
#: locale - measured 2026-09-14 on the owner's device.
CALCULATOR: Final = AppAdapter(
    image="calculatorapp.exe",
    name_tr="Hesap Makinesi",
    document_query={"automation_id": "CalculatorResults"},
    spoken_targets={
        "sonuç": {"automation_id": "CalculatorResults"},
        "sonucu": {"automation_id": "CalculatorResults"},
        "ekran": {"automation_id": "CalculatorResults"},
    },
)

#: B39 (req 125): Word exposes the document body as a ``Document`` control (UI
#: Automation's Text pattern - the same pattern classic Notepad's edit exposes) and its
#: close prompt names Kaydet / Kaydetme / İptal in this locale. Declared from the
#: application's documented UI Automation surface; the lab measurement on the owner's
#: desktop is the B39 checkpoint (Office is not installed on the build machine).
WORD: Final = AppAdapter(
    image="winword.exe",
    name_tr="Word",
    document_query={"control_type": "Document"},
    dialog_buttons={
        "save": ("Kaydet", "Save"),
        "dont_save": ("Kaydetme", "Don't Save"),
        "cancel": ("İptal", "Cancel"),
    },
    spoken_targets={
        "belge": {"control_type": "Document"},
        "belgeyi": {"control_type": "Document"},
        "metin": {"control_type": "Document"},
        "metni": {"control_type": "Document"},
    },
    shortcuts={"save": ("ctrl", "s")},
)

#: B39 (req 125): Excel's grid is a ``DataGrid`` whose cells carry values; the text a
#: keystroke put into the active cell is read back from the cell, not the grid root, so
#: no document query is declared - the plan verifies by the device's own typed count
#: and the foreground, and says so with its level.
EXCEL: Final = AppAdapter(
    image="excel.exe",
    name_tr="Excel",
    dialog_buttons={
        "save": ("Kaydet", "Save"),
        "dont_save": ("Kaydetme", "Don't Save"),
        "cancel": ("İptal", "Cancel"),
    },
    shortcuts={"save": ("ctrl", "s")},
)

#: B39 (req 126): VS Code is an Electron window - its tree is a web document, so the
#: editor is driven through the chords it documents (quick-open, the command palette)
#: and verified through its window title, which names the open file.
VSCODE: Final = AppAdapter(
    image="code.exe",
    name_tr="VS Code",
    shortcuts={
        "quick_open": ("ctrl", "p"),
        "command_palette": ("ctrl", "shift", "p"),
        "save": ("ctrl", "s"),
    },
)

ADAPTERS: Final[tuple[AppAdapter, ...]] = (NOTEPAD, CALCULATOR, WORD, EXCEL, VSCODE)

#: The generic knowledge every application shares: a button is a ``Button`` and it is
#: found by its name. Used when no adapter claims the window.
GENERIC: Final = AppAdapter(image="", name_tr="uygulama")


def adapter_for(image: str | None) -> AppAdapter:
    """The adapter for a process image ("C:\\...\\notepad.exe" or "notepad.exe"), or the
    generic one. Compared on the bare file name, case-folded, because the device reports a
    Windows path and this server may run on Linux."""
    bare = (image or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    for adapter in ADAPTERS:
        if adapter.image == bare:
            return adapter
    return GENERIC


def button_query(name: str) -> dict[str, str]:
    """ "Tamam" -> the query for a button of that name."""
    return {"name": name, "control_type": "Button"}


def spoken_target_query(adapter: AppAdapter, spoken: str) -> dict[str, str] | None:
    """The query the owner's word names in THIS application ("belge" -> Notepad's edit
    control), or ``None`` when the word is not one the adapter knows."""
    word = spoken.strip().lower()
    if not word:
        return None
    if word in adapter.spoken_targets:
        return dict(adapter.spoken_targets[word])
    return None


def dialog_button_names(adapter: AppAdapter, role: str) -> tuple[str, ...]:
    return adapter.dialog_buttons.get(role, ())


__all__ = [
    "ADAPTERS",
    "CALCULATOR",
    "EXCEL",
    "GENERIC",
    "NOTEPAD",
    "VSCODE",
    "WORD",
    "QUERY_KEYS",
    "AppAdapter",
    "adapter_for",
    "button_query",
    "dialog_button_names",
    "spoken_target_query",
]
