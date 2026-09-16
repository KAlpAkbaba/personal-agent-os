"""Choosing a stack, and saying honestly when there is none
(docs/M28_NATIVE_APP_FACTORY_SPEC.md §1, §3; ADR-0095 decisions 1 and 3).

Two jobs, and the second is the milestone's whole character.

**Choose.** The rule is data, and the reason travels with the choice, because the receipt
says it out loud: *"WPF seçtim: Windows masaüstü, form arayüzü, kurulu araç .NET 10."* A
choice the owner cannot interrogate is a choice they cannot correct.

**Refuse by name.** Every platform this machine cannot reach is refused with the actual
reason and, where one exists, the owner action that would change it. What is deliberately
NOT done: no download, no workload install, no "I'll set that up for you" — the assistant
installs nothing (ADR-0095 decision 4), so a missing toolchain is an owner item and saying
so IS the feature.

The toolchain facts are MEASURED at call time, never assumed from a file written yesterday
— the M27 lesson, where a registry key without an executable was about to be reported as
an installed Photoshop.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.nativefactory.spec import (
    ANDROID_TARGETS,
    STACK_ANDROID_KOTLIN,
    STACK_DOTNET_WPF,
    TARGET_WINDOWS_EXE,
    TARGET_WINDOWS_MSIX,
    TARGET_WINDOWS_PORTABLE,
    TEMPLATE_STACK,
    WINDOWS_TARGETS,
    NativeAppSpec,
)

#: iOS is not a target value (see `spec.py`), so a request for one arrives as WORDS rather
#: than as a spec. These are what the router recognises in order to refuse it properly
#: instead of failing validation with something unhelpful.
IOS_WORDS: Final[tuple[str, ...]] = ("ios", "iphone", "ipad", "ipados", "app store")


@dataclass(frozen=True, slots=True)
class ToolchainFacts:
    """What is really on this machine, measured now."""

    dotnet: str | None
    dotnet_sdk: str | None
    makeappx: str | None
    signtool: str | None
    java: str | None
    java_home: str | None
    android_sdk: str | None
    aapt2: str | None
    macos: bool

    @property
    def can_build_windows(self) -> bool:
        return self.dotnet is not None

    @property
    def can_package_msix(self) -> bool:
        return self.makeappx is not None

    @property
    def can_build_android(self) -> bool:
        # The SDK alone is not enough and never was: Gradle, javac/kotlinc, apksigner and
        # avdmanager all run ON Java. The SDK being present is exactly what makes this
        # blocker confusing, so it is stated as two separate facts.
        has_java = self.java is not None or self.java_home is not None
        return self.android_sdk is not None and has_java

    def as_dict(self) -> dict[str, object]:
        return {
            "dotnet": self.dotnet,
            "dotnet_sdk": self.dotnet_sdk,
            "makeappx": self.makeappx,
            "signtool": self.signtool,
            "java": self.java,
            "java_home": self.java_home,
            "android_sdk": self.android_sdk,
            "aapt2": self.aapt2,
            "macos": self.macos,
        }


def _first_existing(*candidates: str | None) -> str | None:
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _windows_kit_tool(name: str) -> str | None:
    kits = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    base = Path(kits) / "Windows Kits/10/bin"
    if not base.is_dir():
        return None
    for version in sorted((d for d in base.iterdir() if d.is_dir()), reverse=True):
        for arch in ("x64", "x86"):
            candidate = version / arch / name
            if candidate.exists():
                return str(candidate)
    return None


def detect() -> ToolchainFacts:
    """Measure. Read-only, installs nothing, downloads nothing, launches no application."""
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    dotnet = _first_existing(str(Path(program_files) / "dotnet/dotnet.exe")) or shutil.which(
        "dotnet"
    )
    sdk: str | None = None
    if dotnet:
        import subprocess

        try:
            proc = subprocess.run(
                [dotnet, "--version"], capture_output=True, text=True, timeout=60
            )
            sdk = (proc.stdout or "").strip() or None
        except Exception:  # noqa: BLE001 - an unreadable version is not a missing dotnet
            sdk = None

    android_root = (
        os.environ.get("ANDROID_HOME")
        or os.environ.get("ANDROID_SDK_ROOT")
        or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk")
    )
    android_sdk = android_root if android_root and Path(android_root).is_dir() else None
    aapt2: str | None = None
    if android_sdk:
        build_tools = Path(android_sdk) / "build-tools"
        if build_tools.is_dir():
            for version in sorted((d for d in build_tools.iterdir() if d.is_dir()), reverse=True):
                candidate = version / "aapt2.exe"
                if candidate.exists():
                    aapt2 = str(candidate)
                    break

    return ToolchainFacts(
        dotnet=dotnet,
        dotnet_sdk=sdk,
        makeappx=_windows_kit_tool("makeappx.exe"),
        signtool=_windows_kit_tool("signtool.exe"),
        java=shutil.which("java"),
        java_home=os.environ.get("JAVA_HOME") or None,
        android_sdk=android_sdk,
        aapt2=aapt2,
        macos=os.uname().sysname == "Darwin" if hasattr(os, "uname") else False,
    )


@dataclass(frozen=True, slots=True)
class StackChoice:
    """The stack, why it was chosen, and — when it cannot be reached — why not."""

    stack: str | None
    reason: str
    available: bool
    #: Set when `available` is False: the owner action that would change the answer, or
    #: None when nothing would (iOS on a machine that is not a Mac).
    owner_action: str | None = None
    error_class: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "stack": self.stack,
            "reason": self.reason,
            "available": self.available,
            "owner_action": self.owner_action,
            "error_class": self.error_class,
        }


def refuse_ios(request_text: str) -> StackChoice | None:
    """An iOS request, answered as itself rather than as a validation error.

    Returns None when the words are not about iOS, so a caller can go on to the normal
    path. There is no owner action here: no licence, no download and no credential turns a
    Windows machine into a Mac, and pretending otherwise would be the kind of hopeful
    sentence this milestone exists to not write.
    """
    lowered = request_text.casefold()
    if not any(word in lowered for word in IOS_WORDS):
        return None
    return StackChoice(
        stack=None,
        reason=(
            "Bu bilgisayarda macOS ve Xcode yok efendim; iOS uygulaması derlenemez. "
            "Paylaşılabilecek ortak bir proje de yok, çünkü MAUI yüklü değil."
        ),
        available=False,
        owner_action=None,
        error_class="platform_unreachable",
    )


def choose(spec: NativeAppSpec, facts: ToolchainFacts | None = None) -> StackChoice:
    """The stack for this spec, with the sentence the receipt will say.

    The spec's template already pins a stack (a mismatched pair never validates), so what
    this decides is not WHICH but WHETHER — and that is the answer the owner actually needs,
    because "I chose WPF" and "I cannot build Android" are the two things worth hearing.
    """
    measured = facts or detect()
    stack = spec.resolved_stack
    targets = set(spec.targets)

    if stack == STACK_ANDROID_KOTLIN or targets & ANDROID_TARGETS:
        if not measured.android_sdk:
            return StackChoice(
                stack=stack,
                reason="Android SDK bu makinede yok efendim.",
                available=False,
                owner_action="33",
                error_class="dependency_unavailable",
            )
        if not measured.can_build_android:
            return StackChoice(
                stack=stack,
                reason=(
                    "Android SDK burada — adb, emülatör, build-tools ve sistem "
                    "görüntüleri var — ama Java yok efendim: Gradle da javac de "
                    "apksigner da Java üstünde çalışır, o yüzden APK üretilemiyor. "
                    "Bir JDK kurulursa aynı hat çalışır (madde 33)."
                ),
                available=False,
                owner_action="33",
                error_class="dependency_unavailable",
            )
        return StackChoice(
            stack=stack,
            reason="Kotlin/Gradle seçtim: Android hedefi, kurulu SDK ve JDK.",
            available=True,
        )

    if targets & WINDOWS_TARGETS:
        if not measured.can_build_windows:
            return StackChoice(
                stack=stack,
                reason=".NET SDK bu makinede yok efendim; Windows uygulaması derlenemez.",
                available=False,
                owner_action=None,
                error_class="dependency_unavailable",
            )
        needs_msix = "windows_msix" in targets
        if needs_msix and not measured.can_package_msix:
            return StackChoice(
                stack=stack,
                reason=(
                    "MSIX paketlemek için makeappx bulunamadı efendim; "
                    "EXE ve taşınabilir paket üretilebilir."
                ),
                available=False,
                owner_action=None,
                error_class="dependency_unavailable",
            )
        surface = "form arayüzü" if stack == STACK_DOTNET_WPF else "web arayüzü"
        sdk = measured.dotnet_sdk or "kurulu"
        return StackChoice(
            stack=stack,
            reason=f"{_spoken(stack)} seçtim: Windows masaüstü, {surface}, kurulu araç .NET {sdk}.",
            available=True,
        )

    return StackChoice(
        stack=stack,
        reason=f"{_spoken(stack)} bu hedefleri üretemiyor efendim.",
        available=False,
        error_class="dependency_unavailable",
    )


#: What the device-dispatched path (ADR-0119, ``device_build.build_on_device``) can
#: produce today: it scaffolds, builds, tests, publishes and reads back ONE thing, the EXE.
#: A portable package or an MSIX is not made there, so a row for one is refused by name
#: rather than answered with an EXE wearing its label.
#: B33 req 456/457: the portable zip and the MSIX are packaged BY THE DEVICE after the EXE
#: is read back (``project.package``), so the three Windows targets are all buildable there.
DEVICE_BUILDABLE_TARGETS: Final[frozenset[str]] = frozenset(
    {TARGET_WINDOWS_EXE, TARGET_WINDOWS_PORTABLE, TARGET_WINDOWS_MSIX}
)

SPEECH_DEVICE_PACKAGING_NOT_WIRED: Final = (
    "Bu hedef kayıtlı cihaz üzerinden üretilmiyor efendim; EXE, taşınabilir paket ve "
    "MSIX üretilebilir."
)
SPEECH_DEVICE_NO_ANDROID: Final = (
    "Android derlemesi kayıtlı cihaz yolunda yok efendim; cihaz yalnızca Windows "
    "uygulaması derliyor."
)


def choose_on_device(spec: NativeAppSpec) -> StackChoice:
    """The stack when the build will run on the owner's enrolled Windows device.

    M28 row 26.16. Production Cloud Core is a Linux host: ``detect()`` there finds no .NET,
    no Windows Kits and no Java, and never will. Planning against THOSE facts opened every
    Windows row as ``unavailable`` - "the .NET SDK is not on this machine" - and
    ``native.build`` then refused before the device path was ever reached. The facts were
    true and about the wrong machine.

    So this machine's toolchain is not consulted here. The device's toolchain is proven by
    the device: its own ``project.run`` refuses with ``dependency_unavailable`` naming what
    is missing, and that refusal reaches the row in its own words
    (``device_build._fail``). What IS decided here is what the device path can produce at
    all (``DEVICE_BUILDABLE_TARGETS``).
    """
    stack = spec.resolved_stack
    targets = set(spec.targets)
    if stack == STACK_ANDROID_KOTLIN or targets & ANDROID_TARGETS:
        return StackChoice(
            stack=stack,
            reason=SPEECH_DEVICE_NO_ANDROID,
            available=False,
            owner_action=None,
            error_class="dependency_unavailable",
        )
    if targets & WINDOWS_TARGETS and targets <= DEVICE_BUILDABLE_TARGETS:
        surface = "form arayüzü" if stack == STACK_DOTNET_WPF else "web arayüzü"
        return StackChoice(
            stack=stack,
            reason=(
                f"{_spoken(stack)} seçtim: Windows masaüstü, {surface}; derlemeyi kayıtlı "
                "Windows cihazınız yapacak."
            ),
            available=True,
        )
    if targets & WINDOWS_TARGETS:
        return StackChoice(
            stack=stack,
            reason=SPEECH_DEVICE_PACKAGING_NOT_WIRED,
            available=False,
            owner_action=None,
            error_class="dependency_unavailable",
        )
    return StackChoice(
        stack=stack,
        reason=f"{_spoken(stack)} bu hedefleri üretemiyor efendim.",
        available=False,
        error_class="dependency_unavailable",
    )


def _spoken(stack: str) -> str:
    return {
        "dotnet_wpf": "WPF",
        "dotnet_winforms": "WinForms",
        "tauri": "Tauri",
        "android_kotlin": "Kotlin/Gradle",
    }.get(stack, stack)


def stack_for_template(template: str) -> str:
    return TEMPLATE_STACK[template]


__all__ = [
    "DEVICE_BUILDABLE_TARGETS",
    "IOS_WORDS",
    "SPEECH_DEVICE_NO_ANDROID",
    "SPEECH_DEVICE_PACKAGING_NOT_WIRED",
    "StackChoice",
    "ToolchainFacts",
    "choose",
    "choose_on_device",
    "detect",
    "refuse_ios",
    "stack_for_template",
]
