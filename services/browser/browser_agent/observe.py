"""``browser.observe``: the page as a NUMBERED LIST of what can be acted on
(contract v1.6 item 1, ADR-0207).

``browser.snapshot`` is the ARIA tree of ``body`` as text: no handle on any
element, no bound per element. A planner cannot point into it. An observation
is the opposite trade: a short, bounded list in which every entry has a
reference (``e1``, ``e2``, ...) that the next command can name.

Two halves, kept apart on purpose:

* ``COLLECT_JS`` runs in the page and returns RAW records - what the DOM says
  about each candidate element. It writes NOTHING into the page: no attribute,
  no property, no global. The numbering lives in the worker's memory only.
* ``reduce_elements`` is a PURE function over those records: filter, order,
  cap, fold, number. No browser, no clock, no I/O - which is why the rules
  below are tested on plain dictionaries.

What an observation never carries, by construction rather than by a filter
that could be forgotten: the VALUE of any field. The collector reads
``has_value`` (a boolean) and never ``value``; the reducer copies a closed set
of keys and would drop a ``value`` key if a raw record carried one. A password
or card field is additionally marked ``sensitive`` - the loop never fills
those (ADR-0207 c) - from the field's type, its ``autocomplete`` token and the
words in its name or id.

A reference is valid for the observation that handed it out and for nothing
else: another observation, another tab, or the same tab after a navigation
answers ``ui_state_changed`` (retryable - observe again). It is never resolved
to "something similar".
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Final

from .errors import BrowserError, ErrorClass
from .injection import count_injection_markers, normalize_for_markers
from .risk_markers import fold, is_external_communication, is_high_impact

DEFAULT_MAX_ELEMENTS: Final = 120
MAX_ELEMENTS_CEILING: Final = 120
DEFAULT_MAX_TEXT_CHARS: Final = 6_000
MAX_TEXT_CHARS_CEILING: Final = 6_000
MAX_NAME_CHARS: Final = 80
#: What the elements and the text of ONE observation may weigh together, as UTF-8 JSON.
#: Every browser result is held to 48 KiB (contract section 3); the counts above are in
#: characters, and 120 names of 80 Turkish letters plus 6 000 characters of Turkish text
#: weigh 56 KB. Left to the worker's generic cap that result would have had its text
#: halved until nothing was left and then been replaced by a marker object - the
#: observation lost, silently. So an observation fits itself, by its own priorities.
MAX_OBSERVATION_BYTES: Final = 40 * 1024
#: The text is cut before any element is: a planner can act without the prose, not
#: without the controls. It is never cut below this.
MIN_TEXT_CHARS: Final = 1_000

#: How many raw records the page-side collector may return. The reducer's cap is what
#: the caller sees; this one only keeps a pathological page from building a huge array.
RAW_ELEMENT_LIMIT: Final = 1_500

SCOPE_VIEWPORT: Final = "viewport"
SCOPE_PAGE: Final = "page"
SCOPES: Final[tuple[str, ...]] = (SCOPE_VIEWPORT, SCOPE_PAGE)

_REF_RE: Final = re.compile(r"^e[1-9][0-9]{0,3}$")

#: Roles that hold a value or a choice rather than doing something when pressed.
FIELD_ROLES: Final = frozenset(
    {
        "textbox",
        "searchbox",
        "combobox",
        "listbox",
        "checkbox",
        "radio",
        "switch",
        "slider",
        "spinbutton",
    }
)

#: ``autocomplete`` tokens that name a credential, a card or an identity number.
_SENSITIVE_AUTOCOMPLETE: Final = frozenset(
    {
        "current-password",
        "new-password",
        "one-time-code",
        "cc-number",
        "cc-csc",
        "cc-exp",
        "cc-exp-month",
        "cc-exp-year",
        "cc-name",
        "cc-given-name",
        "cc-family-name",
        "cc-type",
    }
)

#: Whole words (folded) in a field's name, id, label or placeholder that mark it as one
#: the loop must never type into. Closed forms, the same rule as the risk markers.
_SENSITIVE_WORDS: Final[tuple[str, ...]] = (
    "password",
    "passcode",
    "parola",
    "sifre",
    "sifreniz",
    "pin",
    "cvv",
    "cvc",
    "cvv2",
    "card number",
    "kart numarasi",
    "kart no",
    "kredi karti",
    "credit card",
    "guvenlik kodu",
    "security code",
    "iban",
    "tc kimlik",
    "tckn",
    "kimlik no",
    "kimlik numarasi",
    "ssn",
    "otp",
    "dogrulama kodu",
    "verification code",
)
_SENSITIVE_RE: Final = re.compile(
    r"(?<![0-9a-z])(?:"
    + "|".join(re.escape(w) for w in sorted(_SENSITIVE_WORDS, key=len, reverse=True))
    + r")(?![0-9a-z])"
)

#: The keys one observed element may carry. Anything else in a raw record is dropped.
ELEMENT_KEYS: Final[tuple[str, ...]] = (
    "ref",
    "role",
    "name",
    "tag",
    "state",
    "in_form",
    "submits",
    "href_host",
    "in_viewport",
    "sensitive",
    "risk_hint",
)

# The collector. Returns {elements: [...], truncated: bool}. It reads and writes nothing.
# Every element gets a structural path (nth-of-type steps from the document or from its
# shadow host) that stays in the worker: a payload can never supply one.
COLLECT_JS: Final = r"""
(limit) => {
  const INTERACTIVE = [
    'a[href]', 'button', 'input', 'select', 'textarea', 'summary', '[role]',
    '[contenteditable=""]', '[contenteditable="true"]', '[tabindex]',
  ].join(',');
  const ROLES = new Set([
    'button', 'link', 'textbox', 'searchbox', 'combobox', 'listbox', 'option', 'checkbox',
    'radio', 'switch', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'slider',
    'spinbutton', 'treeitem',
  ]);
  const BUTTON_TYPES = ['button', 'submit', 'reset', 'image'];
  const NO_VALUE_TYPES = ['checkbox', 'radio', 'button', 'submit', 'reset', 'image', 'file'];
  const typeOf = (el) => (el.getAttribute('type') || '').toLowerCase();
  const implicitRole = (el) => {
    const tag = el.tagName.toLowerCase();
    const type = typeOf(el);
    if (tag === 'a') return el.hasAttribute('href') ? 'link' : null;
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'select') return el.multiple ? 'listbox' : 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      if (type === 'hidden') return null;
      if (BUTTON_TYPES.includes(type)) return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'range') return 'slider';
      if (type === 'number') return 'spinbutton';
      if (type === 'search') return 'searchbox';
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return null;
  };
  const text = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 400);
  const textOf = (n) => n.innerText || n.textContent || '';
  const labelOf = (el) => {
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const root = el.getRootNode();
      const find = (id) => (root.getElementById ? root.getElementById(id) : null);
      const joined = by.split(/\s+/).map(find).filter(Boolean).map(textOf).join(' ');
      if (joined.trim()) return joined;
    }
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return aria;
    if (el.labels && el.labels.length) {
      const joined = Array.from(el.labels).map(textOf).join(' ');
      if (joined.trim()) return joined;
    }
    const tag = el.tagName.toLowerCase();
    const type = typeOf(el);
    if (tag === 'input' && ['button', 'submit', 'reset'].includes(type)) {
      return el.getAttribute('value') || type;
    }
    if (tag === 'input' && type === 'image') return el.getAttribute('alt') || '';
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      return el.getAttribute('placeholder') || el.getAttribute('title')
        || el.getAttribute('name') || '';
    }
    const own = textOf(el);
    if (own.trim()) return own;
    const img = el.querySelector('img[alt]');
    if (img) return img.getAttribute('alt') || '';
    return el.getAttribute('title') || '';
  };
  const step = (el) => {
    let n = 1;
    for (let s = el.previousElementSibling; s; s = s.previousElementSibling) {
      if (s.tagName === el.tagName) n += 1;
    }
    return el.tagName.toLowerCase() + ':nth-of-type(' + n + ')';
  };
  const pathOf = (el) => {
    // A list of segments; a new segment starts at every shadow boundary.
    const segments = [];
    let steps = [];
    let node = el;
    while (node && node.nodeType === 1) {
      steps.unshift(step(node));
      const parent = node.parentNode;
      if (parent && parent.nodeType === 11 && parent.host) {   // an OPEN shadow root
        segments.unshift(steps.join(' > '));
        steps = [];
        node = parent.host;
        continue;
      }
      if (!parent || parent.nodeType === 9) break;
      node = parent;
    }
    segments.unshift(steps.join(' > '));
    return segments;
  };
  const visible = (el, rect, style) => {
    if (rect.width <= 0 || rect.height <= 0) return false;
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    if (parseFloat(style.opacity || '1') === 0) return false;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      if (n.getAttribute('aria-hidden') === 'true') return false;
      if (n.hasAttribute('hidden') || n.inert) return false;
    }
    return true;
  };
  const flag = (el, name) => (
    el.hasAttribute(name) ? el.getAttribute(name) === 'true' : null
  );
  const hostOf = (el) => {
    try {
      const u = new URL(el.href, document.baseURI);
      return /^https?:$/.test(u.protocol) ? u.host : u.protocol.replace(':', '');
    } catch (e) {
      return null;
    }
  };
  const out = [];
  let truncated = false;
  let order = 0;
  const walk = (root, inShadow) => {
    for (const el of root.querySelectorAll('*')) {
      // OPEN shadow roots are walked. A closed one is invisible from here by design of
      // the platform; saying so is contract item 8 and lands with the loop (PR-B).
      if (el.shadowRoot) walk(el.shadowRoot, true);
      if (!el.matches(INTERACTIVE)) continue;
      const roleAttr = (el.getAttribute('role') || '').trim().toLowerCase();
      const explicit = roleAttr.split(/\s+/)[0] || null;
      const implicit = implicitRole(el);
      const role = explicit || implicit;
      if (!role) continue;
      if (explicit && !ROLES.has(explicit) && !implicit) continue;
      order += 1;
      if (out.length >= limit) { truncated = true; continue; }
      const tag = el.tagName.toLowerCase();
      const type = typeOf(el);
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      const form = el.closest('form');
      const isSubmitControl = (tag === 'button' && (type === 'submit' || type === ''))
        || (tag === 'input' && (type === 'submit' || type === 'image'));
      const hasHref = tag === 'a' && el.hasAttribute('href');
      const isField = tag === 'input' || tag === 'textarea' || tag === 'select'
        || el.isContentEditable;
      let hasValue = null;
      if (isField && !NO_VALUE_TYPES.includes(type)) {
        // A boolean only. The value itself never leaves the page.
        hasValue = el.isContentEditable
          ? !!(el.textContent || '').trim()
          : !!(el.value && String(el.value).length);
      }
      const isToggle = type === 'checkbox' || type === 'radio';
      const fieldId = (el.getAttribute('name') || '') + ' ' + (el.getAttribute('id') || '');
      out.push({
        order: order,
        tag: tag,
        type: type,
        role: role,
        name: text(labelOf(el)),
        visible: visible(el, rect, style),
        in_viewport: rect.bottom > 0 && rect.right > 0
          && rect.top < window.innerHeight && rect.left < window.innerWidth,
        disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
        readonly: !!el.readOnly || el.getAttribute('aria-readonly') === 'true',
        required: !!el.required || el.getAttribute('aria-required') === 'true',
        checked: isToggle ? !!el.checked : flag(el, 'aria-checked'),
        expanded: flag(el, 'aria-expanded'),
        selected: flag(el, 'aria-selected'),
        has_value: hasValue,
        in_form: !!form,
        submits: !!form && isSubmitControl,
        href_host: hasHref ? hostOf(el) : null,
        autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase(),
        field_id: text(fieldId),
        has_onclick: !!el.onclick || el.hasAttribute('onclick'),
        has_href: hasHref,
        in_shadow: inShadow,
        path: pathOf(el),
      });
    }
  };
  walk(document, false);
  return { elements: out, truncated: truncated, seen: order };
}
"""


@dataclass(frozen=True, slots=True)
class ObservedElement:
    """One numbered element, as the caller sees it, plus what stays in the worker."""

    ref: str
    role: str
    name: str
    tag: str
    state: tuple[str, ...]
    in_form: bool
    submits: bool
    href_host: str | None
    in_viewport: bool
    sensitive: bool
    risk_hint: str
    #: Worker-side only, never in a result: how to find the element again, and what it
    #: looked like, so a reference can be refused when the element is no longer the one.
    path: tuple[str, ...] = field(default=(), compare=False)
    fingerprint: tuple[str, str, str] = field(default=("", "", ""), compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "role": self.role,
            "name": self.name,
            "tag": self.tag,
            "state": list(self.state),
            "in_form": self.in_form,
            "submits": self.submits,
            "href_host": self.href_host,
            "in_viewport": self.in_viewport,
            "sensitive": self.sensitive,
            "risk_hint": self.risk_hint,
        }


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    elements: tuple[ObservedElement, ...]
    truncated: bool
    seen: int

    def by_ref(self, ref: str) -> ObservedElement | None:
        return next((e for e in self.elements if e.ref == ref), None)


def clean_name(raw: Any) -> str:
    """A name as DATA: folded against the cheap evasions, one line, capped."""
    if not isinstance(raw, str) or not raw:
        return ""
    name = normalize_for_markers(raw).strip()
    # Control characters have no business in a name a model will read.
    name = "".join(ch for ch in name if ch.isprintable())
    if len(name) > MAX_NAME_CHARS:
        name = name[: MAX_NAME_CHARS - 1].rstrip() + "…"
    return name


def is_sensitive(raw: dict[str, Any]) -> bool:
    """A field the loop must never type into: a credential, a card, an identity number.

    Only a FIELD can be sensitive: a button called "Pin this" holds nothing to protect.
    """
    if str(raw.get("role") or "") not in FIELD_ROLES:
        return False
    if str(raw.get("type") or "").lower() == "password":
        return True
    tokens = set(str(raw.get("autocomplete") or "").lower().split())
    if tokens & _SENSITIVE_AUTOCOMPLETE:
        return True
    haystack = fold(f"{raw.get('name') or ''} {raw.get('field_id') or ''}")
    haystack = re.sub(r"[_\-.\[\]]+", " ", haystack)
    return _SENSITIVE_RE.search(haystack) is not None


def _state(raw: dict[str, Any]) -> tuple[str, ...]:
    flags: list[str] = []
    if raw.get("disabled"):
        flags.append("disabled")
    if raw.get("readonly"):
        flags.append("readonly")
    if raw.get("required"):
        flags.append("required")
    for key in ("checked", "expanded", "selected"):
        value = raw.get(key)
        if value is True:
            flags.append(key)
        elif value is False and key != "selected":
            flags.append(f"not_{key}")
    has_value = raw.get("has_value")
    if has_value is True:
        flags.append("has_value")
    elif has_value is False:
        flags.append("empty")
    if raw.get("in_shadow"):
        flags.append("in_shadow")
    return tuple(flags)


def risk_hint(raw: dict[str, Any], name: str) -> str:
    """What acting on the element would be, by the contract's own rule (section 4).

    A HINT for the consumer's gate, computed from the same inputs the worker's
    ``classify_click`` uses when the click arrives - which stays the authority.
    """
    if str(raw.get("role") or "") in FIELD_ROLES and not raw.get("submits"):
        # Typing into a field or ticking a box changes the form, not the world; the
        # field's LABEL ("Silinecek hesap") is not a control that does what it names.
        return "REVERSIBLE_WRITE"
    if is_high_impact(name):
        return "HIGH_IMPACT"
    if raw.get("submits") or is_external_communication(name):
        return "EXTERNAL_COMMUNICATION"
    if raw.get("has_href") and not raw.get("has_onclick"):
        return "NAVIGATE"
    return "REVERSIBLE_WRITE"


def clamp(value: Any, *, default: int, ceiling: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"observe: '{name}' must be a positive integer",
            retryable=False,
        )
    return min(value, ceiling)


def reduce_elements(
    raw_elements: list[dict[str, Any]],
    *,
    max_elements: int = DEFAULT_MAX_ELEMENTS,
    scope: str = SCOPE_PAGE,
    collector_truncated: bool = False,
    observation_id: str | None = None,
) -> Observation:
    """Filter, order, cap and number. Pure: the same records give the same list.

    * hidden elements (no box, ``display:none``, ``aria-hidden``, ``inert``) are dropped;
      a DISABLED one is kept and says so - "the button is there and cannot be pressed"
      is an observation;
    * ``scope="viewport"`` keeps only what is on the screen; ``"page"`` keeps everything,
      the viewport FIRST and then the rest in document order;
    * the cap applies after ordering, so what is cut is always the far end of the page.
    """
    if scope not in SCOPES:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            f"observe: 'scope' must be one of {', '.join(SCOPES)}",
            retryable=False,
        )
    max_elements = max(1, min(int(max_elements), MAX_ELEMENTS_CEILING))
    kept = [r for r in raw_elements if isinstance(r, dict) and r.get("visible")]
    if scope == SCOPE_VIEWPORT:
        kept = [r for r in kept if r.get("in_viewport")]
    kept.sort(key=lambda r: (0 if r.get("in_viewport") else 1, int(r.get("order") or 0)))
    truncated = collector_truncated or len(kept) > max_elements
    elements: list[ObservedElement] = []
    for index, raw in enumerate(kept[:max_elements], start=1):
        name = clean_name(raw.get("name"))
        role = str(raw.get("role") or "")[:32]
        tag = str(raw.get("tag") or "")[:32]
        path = raw.get("path") or []
        elements.append(
            ObservedElement(
                ref=f"e{index}",
                role=role,
                name=name,
                tag=tag,
                state=_state(raw),
                in_form=bool(raw.get("in_form")),
                submits=bool(raw.get("submits")),
                href_host=(str(raw["href_host"])[:253] if raw.get("href_host") else None),
                in_viewport=bool(raw.get("in_viewport")),
                sensitive=is_sensitive(raw),
                risk_hint=risk_hint(raw, name),
                path=tuple(str(p) for p in path if isinstance(p, str)),
                fingerprint=(tag, role, name),
            )
        )
    return Observation(
        observation_id=observation_id or f"obs-{uuid.uuid4().hex[:12]}",
        elements=tuple(elements),
        truncated=truncated,
        seen=len(kept),
    )


def reduce_text(text: str, *, max_chars: int = DEFAULT_MAX_TEXT_CHARS) -> tuple[str, bool, int]:
    """(excerpt, truncated, injection markers). The excerpt is page text: data, quoted."""
    max_chars = max(1, min(int(max_chars), MAX_TEXT_CHARS_CEILING))
    markers = count_injection_markers(text or "")
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", text or "")
    cleaned = re.sub(r"\n\s*\n\s*", "\n", cleaned).strip()
    if len(cleaned) <= max_chars:
        return cleaned, False, markers
    return cleaned[:max_chars].rstrip(), True, markers


def _weight(elements: list[dict[str, Any]], text: str) -> int:
    document = {"elements": elements, "text": text}
    return len(json.dumps(document, ensure_ascii=False).encode("utf-8"))


def fit_to_budget(
    observation: Observation, text: str, *, max_bytes: int = MAX_OBSERVATION_BYTES
) -> tuple[Observation, str, bool, bool]:
    """(observation, text, elements were cut, text was cut) - together under ``max_bytes``.

    The text gives way first, down to ``MIN_TEXT_CHARS``; then elements are dropped from
    the END of the list (the far end of the page - the order ``reduce_elements`` made),
    and only then the rest of the text. The observation that is returned is the one the
    worker must hold: a reference that was cut from the result does not exist.
    """
    elements = [element.as_dict() for element in observation.elements]
    text_cut = False
    while _weight(elements, text) > max_bytes and len(text) > MIN_TEXT_CHARS:
        text = text[: max(MIN_TEXT_CHARS, (len(text) * 3) // 4)].rstrip()
        text_cut = True
    kept = len(elements)
    while kept > 1 and _weight(elements[:kept], text) > max_bytes:
        kept -= 1
    while _weight(elements[:kept], text) > max_bytes and text:
        text = text[: len(text) // 2]
        text_cut = True
    elements_cut = kept < len(elements)
    if elements_cut:
        observation = replace(observation, elements=observation.elements[:kept], truncated=True)
    return observation, text, elements_cut, text_cut


def validate_ref(ref: Any) -> str:
    if not isinstance(ref, str) or _REF_RE.match(ref) is None:
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            "target 'ref' must be a reference an observation handed out (e1, e2, ...)",
            retryable=False,
        )
    return ref


#: Steps a worker-side path may consist of. A path never comes from a payload, and this
#: check is what makes that true even if one were smuggled into a stored observation.
_PATH_SEGMENT_RE: Final = re.compile(
    r"^[a-z][a-z0-9-]*:nth-of-type\([1-9][0-9]{0,5}\)"
    r"(?: > [a-z][a-z0-9-]*:nth-of-type\([1-9][0-9]{0,5}\))*$"
)


def selector_for(element: ObservedElement) -> list[str]:
    """The element's structural path as locator steps (one per shadow boundary)."""
    if not element.path or any(_PATH_SEGMENT_RE.match(seg) is None for seg in element.path):
        raise BrowserError(
            ErrorClass.UI_STATE_CHANGED,
            f"reference {element.ref} can no longer be located; observe the page again",
            retryable=True,
            evidence={"ref": element.ref},
        )
    return list(element.path)


__all__ = [
    "COLLECT_JS",
    "DEFAULT_MAX_ELEMENTS",
    "DEFAULT_MAX_TEXT_CHARS",
    "ELEMENT_KEYS",
    "MAX_NAME_CHARS",
    "MAX_OBSERVATION_BYTES",
    "MIN_TEXT_CHARS",
    "RAW_ELEMENT_LIMIT",
    "SCOPES",
    "Observation",
    "ObservedElement",
    "clamp",
    "clean_name",
    "fit_to_budget",
    "is_sensitive",
    "reduce_elements",
    "reduce_text",
    "risk_hint",
    "selector_for",
    "validate_ref",
]
