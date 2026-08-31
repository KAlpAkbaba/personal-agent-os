"""Deterministic, offline CONFIGURATION-AUDIT checks (M8).

Scope of what this module is allowed to do, enforced by construction:

- READ-ONLY. It opens files and matches regexes. Nothing here connects to a
  socket, resolves a name, spawns a process or invokes a third-party tool.
  There is no scanning, no probing and no exploitation anywhere in M8.
- BOUNDED. At most `MAX_FILES` files, `MAX_FILE_BYTES` each, text only (NUL
  sniff), symlinks skipped so a link cannot lead out of the authorized root.
- The root it reads is NOT caller-supplied: `assessments.py` accepts only a
  root the owner recorded in the asset's `constraints.config_roots`.
- DETERMINISTIC. Same tree in, same findings out, same fingerprints, same
  order. Fingerprints are derived from (check_id, relative path, directive
  key) — never from a line number — so an inserted comment does not mint a
  "new" finding, and a re-run updates the existing row instead of duplicating.
- SECRET-FREE OUTPUT. Every evidence string is passed through
  `redaction.redact_text`, so the `secret_in_config` finding can say "an AWS
  access key is committed in app.env at line 7" while carrying
  `[REDACTED:aws_access_key]` in place of the key itself.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.security.models import (
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
)
from app.security.redaction import redact_text

# ----------------------------------------------------------------- collection

MAX_FILES = 500
MAX_FILE_BYTES = 256 * 1024
MAX_LINES_PER_FILE = 5000
MAX_LINE_CHARS = 4096
MAX_EVIDENCE_CHARS = 400

# Configuration-shaped text files only. "" covers extensionless unix configs
# such as `sshd_config`; a NUL sniff still rejects binaries with those names.
TEXT_SUFFIXES = frozenset(
    {
        "",
        ".conf",
        ".config",
        ".cfg",
        ".ini",
        ".env",
        ".properties",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".txt",
        ".xml",
    }
)


@dataclass(frozen=True, slots=True)
class CollectedFile:
    """One text file inside an authorized config root."""

    relative_path: str  # posix, relative to the root — stable across machines
    lines: tuple[str, ...]


def collect_files(root: Path) -> list[CollectedFile]:
    """Read every eligible text file under `root`, deterministically ordered.

    The caller is responsible for having proven `root` is authorized; this
    function additionally refuses to leave it via symlinks.
    """
    files: list[CollectedFile] = []
    root = root.resolve()
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        if len(files) >= MAX_FILES:
            break
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:1024]:
            continue  # binary
        text = raw.decode("utf-8", errors="replace")
        lines = tuple(
            line[:MAX_LINE_CHARS] for line in text.splitlines()[:MAX_LINES_PER_FILE]
        )
        files.append(
            CollectedFile(
                relative_path=path.relative_to(root).as_posix(),
                lines=lines,
            )
        )
    return files


# --------------------------------------------------------------------- model


@dataclass(frozen=True, slots=True)
class RawFinding:
    """A check result, pre-persistence. All strings are already redacted."""

    check_id: str
    fingerprint: str
    title: str
    severity: str
    evidence: dict[str, Any]
    remediation: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Check:
    """One deterministic line-oriented configuration check."""

    check_id: str
    severity: str
    title_tr: str
    title_en: str
    rationale_tr: str
    detect: re.Pattern[str]
    # Only files whose posix relative path matches one of these regexes are
    # examined. Empty means "every collected file".
    file_patterns: tuple[re.Pattern[str], ...] = ()
    # Substitutions that turn the offending line into the fixed line. Present
    # only for automatable fixes.
    fixes: tuple[tuple[re.Pattern[str], str], ...] = ()
    apply_supported: bool = False
    disruption: str = "low"
    remediation_action: str = "set_directive"
    remediation_tr: str = ""
    manual_steps: tuple[str, ...] = ()
    # Optional extra gate, e.g. "only when the numeric value is below 12".
    predicate: Callable[[re.Match[str]], bool] | None = field(default=None, compare=False)

    def applies_to(self, relative_path: str) -> bool:
        if not self.file_patterns:
            return True
        return any(p.search(relative_path) for p in self.file_patterns)

    def directive_key(self, match: re.Match[str]) -> str:
        """Stable per-finding key used in the fingerprint (never a line no.).

        Alternation branches name their capture `key` or `key2`; whichever
        participated wins, and the whole match is the last resort.
        """
        groups = match.groupdict()
        captured = groups.get("key") or groups.get("key2") or match.group(0)
        return redact_text(str(captured).strip().lower())[0][:64]


def fingerprint_for(check_id: str, relative_path: str, key: str) -> str:
    """Stable across runs, machines and absolute paths."""
    payload = f"{check_id}|{relative_path}|{key}".encode()
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------- check table

_SSHD = (re.compile(r"(^|/)sshd?_config$"),)
_BACKUP = (re.compile(r"(?i)backup"),)

CHECKS: tuple[Check, ...] = (
    Check(
        check_id="secret_in_config",
        severity=SEVERITY_CRITICAL,
        title_tr="Yapılandırma dosyasında açık kimlik bilgisi",
        title_en="Credential stored in plaintext configuration",
        rationale_tr=(
            "Kimlik bilgileri yapılandırma dosyalarında düz metin tutulmamalı; "
            "dosya yedeklere, sürüm kontrolüne ve loglara sızar."
        ),
        # Detection for this check is special-cased in `run_checks` (it uses the
        # redaction pattern list); this pattern only extracts the key name.
        detect=re.compile(r"^\s*(?P<key>[\w.\-\[\]]+)\s*[:=]"),
        apply_supported=False,
        disruption="low",
        remediation_action="rotate_and_externalize",
        remediation_tr=(
            "Kimlik bilgisini iptal edip yenileyin, değeri bir gizli yönetim "
            "sistemine taşıyın ve dosyada yalnızca bir referans bırakın."
        ),
        manual_steps=(
            "Sızan kimlik bilgisini sağlayıcı tarafında iptal edin (rotate).",
            "Yeni değeri gizli yönetim sistemine kaydedin.",
            "Yapılandırmada değeri ortam değişkeni/gizli referansı ile değiştirin.",
            "Dosyanın sürüm geçmişini ve yedeklerini temizleyin.",
        ),
    ),
    Check(
        check_id="ssh_root_login_permitted",
        severity=SEVERITY_HIGH,
        title_tr="SSH root oturum açma izinli",
        title_en="SSH root login permitted",
        rationale_tr=(
            "Doğrudan root girişi, hesap bazlı denetim izini yok eder ve kaba "
            "kuvvet saldırıları için tek hedef bırakır."
        ),
        detect=re.compile(
            r"(?im)^\s*(?P<key>PermitRootLogin)\s+(yes|without-password|prohibit-password)\b"
        ),
        file_patterns=_SSHD,
        fixes=((re.compile(r"(?i)^(\s*PermitRootLogin\s+)\S+"), r"\1no"),),
        apply_supported=True,
        disruption="medium",
        remediation_tr=(
            "PermitRootLogin değerini 'no' yapın ve yetkili kullanıcı ile sudo kullanın."
        ),
    ),
    Check(
        check_id="ssh_password_authentication_enabled",
        severity=SEVERITY_MEDIUM,
        title_tr="SSH parola ile kimlik doğrulama açık",
        title_en="SSH password authentication enabled",
        rationale_tr="Parola ile SSH girişi kaba kuvvet ve parola tekrar kullanımına açıktır.",
        detect=re.compile(r"(?im)^\s*(?P<key>PasswordAuthentication)\s+yes\b"),
        file_patterns=_SSHD,
        fixes=((re.compile(r"(?i)^(\s*PasswordAuthentication\s+)\S+"), r"\1no"),),
        apply_supported=True,
        disruption="medium",
        remediation_tr=(
            "Anahtar tabanlı kimlik doğrulamaya geçip PasswordAuthentication 'no' yapın."
        ),
    ),
    Check(
        check_id="debug_mode_enabled",
        severity=SEVERITY_MEDIUM,
        title_tr="Hata ayıklama (debug) modu açık",
        title_en="Debug mode enabled",
        rationale_tr=(
            "Debug modu yığın izlerini, yapılandırmayı ve iç yolları dışarıya "
            "sızdırabilir."
        ),
        detect=re.compile(
            r"(?im)^\s*(?P<key>debug(_mode)?|app_debug)\s*[:=]\s*(true|1|yes|on)\s*$"
        ),
        fixes=(
            (
                re.compile(r"(?i)^(\s*[\w.\-]*debug[\w.\-]*\s*[:=]\s*)(true|1|yes|on)\s*$"),
                r"\1false",
            ),
        ),
        apply_supported=True,
        disruption="low",
        remediation_tr="Üretim yapılandırmasında debug değerini 'false' yapın.",
    ),
    Check(
        check_id="tls_disabled",
        severity=SEVERITY_HIGH,
        title_tr="TLS/şifreli taşıma kapalı",
        title_en="TLS transport disabled",
        rationale_tr="Şifresiz taşıma, kimlik bilgilerinin ve verinin ağda okunmasına izin verir.",
        detect=re.compile(
            r"(?im)^\s*(?P<key>use_tls|enable_tls|tls|ssl|https_only|require_tls)"
            r"\s*[:=]\s*(false|0|no|off|disabled)\s*$"
        ),
        fixes=(
            (
                re.compile(
                    r"(?i)^(\s*(?:use_tls|enable_tls|tls|ssl|https_only|require_tls)\s*[:=]\s*)"
                    r"(false|0|no|off|disabled)\s*$"
                ),
                r"\1true",
            ),
        ),
        apply_supported=True,
        disruption="medium",
        remediation_tr="Taşıma şifrelemesini açın (TLS 1.2+) ve düz metin dinleyiciyi kapatın.",
    ),
    Check(
        check_id="permissive_cors_origin",
        severity=SEVERITY_MEDIUM,
        title_tr="CORS kaynak listesi tamamen açık (*)",
        title_en="Permissive CORS origin wildcard",
        rationale_tr=(
            "Joker karakterli kaynak listesi, herhangi bir sitenin tarayıcı "
            "üzerinden API'ye erişmesine izin verir."
        ),
        detect=re.compile(
            r"""(?im)^\s*(?P<key>allow_origins?|cors_allow_origin|"""
            r"""access[-_]control[-_]allow[-_]origin)\s*[:=]\s*["'\[\s]*\*"""
        ),
        apply_supported=False,
        disruption="low",
        remediation_action="restrict_origins",
        remediation_tr=(
            "Joker karakteri kaldırıp yalnızca sahibin gerçek kaynaklarını listeleyin."
        ),
        manual_steps=(
            "İzin verilecek gerçek kaynakları (origin) belirleyin.",
            "'*' yerine açık liste yazın.",
            "Kimlik bilgisi taşıyan isteklerde joker karakterin geçersiz olduğunu doğrulayın.",
        ),
    ),
    Check(
        check_id="backup_encryption_disabled",
        severity=SEVERITY_HIGH,
        title_tr="Yedek şifrelemesi kapalı",
        title_en="Backup encryption disabled",
        rationale_tr="Şifresiz yedekler, üretim verisinin en kolay sızma yoludur.",
        detect=re.compile(
            r"(?im)^\s*(?P<key>encryption|encrypt|backup_encryption)"
            r"\s*[:=]\s*(none|off|false|no|0|disabled)\s*$"
        ),
        file_patterns=_BACKUP,
        apply_supported=False,
        disruption="low",
        remediation_action="enable_backup_encryption",
        remediation_tr=(
            "Yedek şifrelemesini açın; anahtar sağlanması gerektiği için otomatik "
            "uygulanmaz."
        ),
        manual_steps=(
            "Yedek şifreleme anahtarını gizli yönetim sisteminde oluşturun.",
            "encryption değerini desteklenen bir şifreleme algoritmasına ayarlayın.",
            "Bir geri yükleme testi ile şifreli yedeği doğrulayın.",
        ),
    ),
    Check(
        check_id="weak_password_policy",
        severity=SEVERITY_LOW,
        title_tr="Zayıf parola uzunluğu politikası",
        title_en="Weak minimum password length",
        rationale_tr="12 karakterin altındaki asgari uzunluk, çevrimdışı kırmayı kolaylaştırır.",
        detect=re.compile(
            r"(?im)^\s*(?P<key>min(imum)?_password_length|password_min_length)"
            r"\s*[:=]\s*(?P<value>\d{1,3})\s*$"
        ),
        predicate=lambda m: int(m.group("value")) < 12,
        fixes=(
            (
                re.compile(
                    r"(?i)^(\s*(?:min(?:imum)?_password_length|password_min_length)"
                    r"\s*[:=]\s*)\d{1,3}\s*$"
                ),
                r"\g<1>14",
            ),
        ),
        apply_supported=True,
        disruption="low",
        remediation_tr="Asgari parola uzunluğunu en az 14 karaktere çıkarın.",
    ),
    Check(
        check_id="directory_listing_enabled",
        severity=SEVERITY_LOW,
        title_tr="Dizin listeleme açık",
        title_en="Directory listing enabled",
        rationale_tr="Dizin listeleme, yayımlanması amaçlanmayan dosyaları görünür kılar.",
        detect=re.compile(
            r"(?im)^\s*(?:(?P<key>autoindex)\s+on\b"
            r"|(?P<key2>directory_listing)\s*[:=]\s*(true|on|yes|1)\s*$)"
        ),
        fixes=(
            (re.compile(r"(?i)^(\s*autoindex\s+)on\b"), r"\1off"),
            (
                re.compile(r"(?i)^(\s*directory_listing\s*[:=]\s*)(true|on|yes|1)\s*$"),
                r"\1false",
            ),
        ),
        apply_supported=True,
        disruption="low",
        remediation_tr="Dizin listelemeyi kapatın (autoindex off / directory_listing: false).",
    ),
    Check(
        check_id="obsolete_tls_version_allowed",
        severity=SEVERITY_MEDIUM,
        title_tr="Kullanımdan kalkmış TLS/SSL sürümü kabul ediliyor",
        title_en="Obsolete TLS/SSL version accepted",
        rationale_tr="SSLv3/TLS 1.0/1.1 bilinen kriptografik zayıflıklar içerir.",
        detect=re.compile(
            r"(?im)^\s*(?P<key>ssl_protocols|tls_versions?|min_tls_version)\b"
            r"[^\n]*\b(SSLv2|SSLv3|TLSv1(\.[01])?)\b"
        ),
        apply_supported=False,
        disruption="medium",
        remediation_action="restrict_tls_versions",
        remediation_tr="Yalnızca TLS 1.2 ve 1.3 sürümlerine izin verin.",
        manual_steps=(
            "İstemci uyumluluğunu doğrulayın.",
            "Eski sürümleri yapılandırmadan kaldırın.",
            "Bakım penceresinde servisi yeniden yükleyin.",
        ),
    ),
)

CHECKS_BY_ID = {check.check_id: check for check in CHECKS}


# ------------------------------------------------------------------- runner


def _evidence_line(line: str) -> str:
    """Redact, then truncate. Redaction happens FIRST so truncation can never
    slice a secret into a 'safe-looking' prefix that is still a secret."""
    return redact_text(line)[0][:MAX_EVIDENCE_CHARS]


def _remediation(
    check: Check, file: CollectedFile, line_no: int, line: str
) -> dict[str, Any]:
    proposed: str | None = None
    if check.apply_supported and check.fixes:
        candidate = line
        for pattern, replacement in check.fixes:
            candidate = pattern.sub(replacement, candidate, count=1)
        proposed = candidate if candidate != line else None
    return {
        "check_id": check.check_id,
        "action": check.remediation_action,
        "summary_tr": check.remediation_tr,
        "disruption": check.disruption,
        # An automatable fix needs BOTH a declared capability and a concrete
        # substitution that actually changed the line.
        "apply_supported": bool(check.apply_supported and proposed is not None),
        "reversible": True,
        "file": file.relative_path,
        "line": line_no,
        "current_line": _evidence_line(line),
        "proposed_line": _evidence_line(proposed) if proposed is not None else None,
        "manual_steps": list(check.manual_steps),
    }


def _secret_findings(file: CollectedFile) -> list[RawFinding]:
    """`secret_in_config`: driven by the shared redaction pattern list."""
    check = CHECKS_BY_ID["secret_in_config"]
    out: list[RawFinding] = []
    seen_keys: set[str] = set()
    for index, line in enumerate(file.lines, start=1):
        redacted, patterns = redact_text(line)
        if not patterns:
            continue
        key_match = check.detect.match(line)
        key = (
            redact_text(key_match.group("key").strip().lower())[0][:64]
            if key_match
            else f"line{index}"
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(
            RawFinding(
                check_id=check.check_id,
                fingerprint=fingerprint_for(check.check_id, file.relative_path, key),
                title=f"{check.title_tr} ({check.title_en})",
                severity=check.severity,
                evidence={
                    "check_id": check.check_id,
                    "file": file.relative_path,
                    "line": index,
                    "config_key": key,
                    # The NAMES of the credential patterns that fired — this is
                    # what lets the report say what leaked without leaking it.
                    "secret_patterns": patterns,
                    "redacted_line": redacted[:MAX_EVIDENCE_CHARS],
                    "rationale_tr": check.rationale_tr,
                },
                remediation=_remediation(check, file, index, line),
            )
        )
    return out


def run_checks(files: list[CollectedFile]) -> list[RawFinding]:
    """Run every check over every collected file. Pure and deterministic.

    Output is ordered by (check_id, relative_path, line) so two runs over the
    same tree produce byte-identical results.
    """
    findings: list[RawFinding] = []
    for file in files:
        findings.extend(_secret_findings(file))
        for check in CHECKS:
            if check.check_id == "secret_in_config":
                continue
            if not check.applies_to(file.relative_path):
                continue
            seen_keys: set[str] = set()
            for index, line in enumerate(file.lines, start=1):
                match = check.detect.search(line)
                if match is None:
                    continue
                if check.predicate is not None and not check.predicate(match):
                    continue
                key = check.directive_key(match)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                findings.append(
                    RawFinding(
                        check_id=check.check_id,
                        fingerprint=fingerprint_for(check.check_id, file.relative_path, key),
                        title=f"{check.title_tr} ({check.title_en})",
                        severity=check.severity,
                        evidence={
                            "check_id": check.check_id,
                            "file": file.relative_path,
                            "line": index,
                            "directive": key,
                            "redacted_line": _evidence_line(line),
                            "rationale_tr": check.rationale_tr,
                        },
                        remediation=_remediation(check, file, index, line),
                    )
                )
    findings.sort(key=lambda f: (f.check_id, f.evidence["file"], f.evidence["line"]))
    return findings


__all__ = [
    "CHECKS",
    "CHECKS_BY_ID",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "Check",
    "CollectedFile",
    "RawFinding",
    "collect_files",
    "fingerprint_for",
    "run_checks",
]
