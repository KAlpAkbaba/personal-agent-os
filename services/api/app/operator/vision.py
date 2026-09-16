"""Screenshot understanding behind a provider interface (B29 req 105).

The operator's ladder (docs/M19_DIGITAL_OPERATOR_SPEC.md §2) has a VISUAL rung between the
keyboard and raw coordinates: ``screen.capture`` plus a model that can say what is on the
picture. The capture has existed since M19 and gained its caller in B27
(``operator.screenshot``); this module is the model half, and it is a PROVIDER INTERFACE
because CLAUDE.md says third-party services live behind one.

Three implementations, and the difference between them is spoken:

* :class:`OpenAIVisionProvider` - the owner's OpenAI key (the same one the research
  synthesis and the realtime voice already use), a chat completion with the PNG inline;
* :class:`FakeVisionProvider` - a scripted answer for the corpus and the unit tests;
* no provider at all - ``build_vision_provider`` returns ``None`` and the tool answers
  ``dependency_unavailable`` with a Turkish sentence, never a guess about the picture.

Nothing here persists the image. The bytes go to the provider and come back as words.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Final, Protocol

import httpx

from app.logging import get_logger

logger = get_logger("app.operator.vision")

PROVIDER_AUTO: Final = "auto"
PROVIDER_NONE: Final = "none"
PROVIDER_OPENAI: Final = "openai"
VISION_PROVIDERS: Final[tuple[str, ...]] = (PROVIDER_AUTO, PROVIDER_NONE, PROVIDER_OPENAI)

#: What the model is asked when the owner said only "Ekranda ne var?".
DEFAULT_QUESTION_TR: Final = (
    "Bu ekran görüntüsünde ne var? Türkçe, iki cümleyle, gördüğün pencereyi ve öne çıkan "
    "metni söyle; emin olmadığın şeyi uydurma."
)
MAX_QUESTION_CHARS: Final = 500
#: B39 (req 106): the fifth rung's question - WHERE a named thing is on the picture,
#: answered as one JSON object so a coordinate is parsed, never guessed from prose.
LOCATE_QUESTION_TR: Final = (
    'Bu ekran görüntüsünde "{target}" adlı düğme ya da öğe nerede? YALNIZ şu JSON ile '
    'cevap ver: {{"found": true, "x": <piksel>, "y": <piksel>}} - x ve y, öğenin merkezi, '
    'görüntünün sol üst köşesinden piksel olarak. Emin değilsen {{"found": false}} yaz.'
)
MAX_TARGET_CHARS: Final = 120
MAX_ANSWER_CHARS: Final = 1000
#: The companion caps a capture at 2 MB (spec §2); a provider never gets more.
MAX_IMAGE_BYTES: Final = 2 * 1024 * 1024


class VisionError(Exception):
    """The provider was asked and could not answer - named, never silently empty."""

    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


ERROR_VISION_FAILED: Final = "vision_failed"
ERROR_IMAGE_TOO_LARGE: Final = "image_too_large"


@dataclass(frozen=True, slots=True)
class VisionAnswer:
    text: str
    provider: str
    model: str

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "provider": self.provider, "model": self.model}


@dataclass(frozen=True, slots=True)
class VisionLocation:
    """B39 (req 106): where the provider says a named element is, in IMAGE pixels."""

    x: int
    y: int
    provider: str
    model: str

    def as_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "provider": self.provider, "model": self.model}


class VisionProvider(Protocol):
    name: str

    def describe(self, png: bytes, *, question: str) -> VisionAnswer: ...

    def locate(self, png: bytes, *, target: str) -> VisionLocation | None: ...


class FakeVisionProvider:
    """A scripted answer, and a record of what it was asked (never the image itself)."""

    name = "fake"

    def __init__(
        self,
        answer: str = "Ekranda Not Defteri açık, boş bir belge görünüyor.",
        *,
        location: tuple[int, int] | None = None,
    ) -> None:
        self.answer = answer
        self.questions: list[str] = []
        self.image_sizes: list[int] = []
        #: B39: the scripted answer to ``locate`` (``None`` = not found), and its record.
        self.location = location
        self.targets: list[str] = []

    def describe(self, png: bytes, *, question: str) -> VisionAnswer:
        self.questions.append(question)
        self.image_sizes.append(len(png))
        return VisionAnswer(text=self.answer, provider=self.name, model="fake")

    def locate(self, png: bytes, *, target: str) -> VisionLocation | None:
        self.targets.append(target)
        self.image_sizes.append(len(png))
        if self.location is None:
            return None
        return VisionLocation(
            x=self.location[0], y=self.location[1], provider=self.name, model="fake"
        )


class OpenAIVisionProvider:
    """One chat completion with the PNG as an inline data URL (OpenAI's vision input)."""

    name = PROVIDER_OPENAI

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._transport = transport

    def request_body(self, png: bytes, *, question: str) -> dict[str, Any]:
        """The wire body, separated so a test can read it without a network."""
        encoded = base64.b64encode(png).decode("ascii")
        return {
            "model": self._model,
            "max_tokens": 300,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question[:MAX_QUESTION_CHARS]},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        },
                    ],
                }
            ],
        }

    def describe(self, png: bytes, *, question: str) -> VisionAnswer:
        if len(png) > MAX_IMAGE_BYTES:
            raise VisionError(ERROR_IMAGE_TOO_LARGE, f"image is {len(png)} bytes")
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=self.request_body(png, question=question),
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            # The key never reaches a log line: the exception's own text may carry the
            # request, and only the class name is kept.
            logger.warning("vision_request_failed", provider=self.name, error=type(exc).__name__)
            raise VisionError(ERROR_VISION_FAILED, type(exc).__name__) from exc
        try:
            text = str(payload["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise VisionError(ERROR_VISION_FAILED, "model output had no content") from exc
        if not text:
            raise VisionError(ERROR_VISION_FAILED, "model output was empty")
        return VisionAnswer(text=text[:MAX_ANSWER_CHARS], provider=self.name, model=self._model)

    def locate(self, png: bytes, *, target: str) -> VisionLocation | None:
        """B39 (req 106): the same completion with the locate question; the answer is one
        JSON object or it is not an answer (``VisionError``), and ``found: false`` is
        ``None`` - never a coordinate invented from prose."""
        question = LOCATE_QUESTION_TR.format(target=target[:MAX_TARGET_CHARS])
        answer = self.describe(png, question=question)
        return parse_location(answer.text, provider=self.name, model=self._model)


_JSON_OBJECT_RE: Final = re.compile(r"\{.*?\}", re.DOTALL)


def parse_location(text: str, *, provider: str, model: str) -> VisionLocation | None:
    """The JSON object in a model answer -> a location, ``None`` for ``found: false``,
    ``VisionError`` for anything that is not the shape asked for."""
    match = _JSON_OBJECT_RE.search(text or "")
    if match is None:
        raise VisionError(ERROR_VISION_FAILED, "locate answer carried no JSON object")
    try:
        payload = json.loads(match.group(0))
    except ValueError as exc:
        raise VisionError(ERROR_VISION_FAILED, "locate answer was not JSON") from exc
    if not isinstance(payload, dict):
        raise VisionError(ERROR_VISION_FAILED, "locate answer was not an object")
    if not payload.get("found"):
        return None
    try:
        x, y = int(payload["x"]), int(payload["y"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VisionError(ERROR_VISION_FAILED, "locate answer had no x/y") from exc
    if x < 0 or y < 0:
        raise VisionError(ERROR_VISION_FAILED, "locate answer was off the image")
    return VisionLocation(x=x, y=y, provider=provider, model=model)


def build_vision_provider(settings: Any) -> VisionProvider | None:
    """``OpenAIVisionProvider`` when a key is configured (or asked for by name), else
    ``None`` - and ``None`` is spoken as "no provider", never as a blank answer."""
    configured = str(getattr(settings, "vision_provider", PROVIDER_AUTO) or PROVIDER_AUTO)
    if configured == PROVIDER_NONE:
        return None
    key = getattr(settings, "openai_api_key", "") or getattr(settings, "voice_openai_api_key", "")
    if not key:
        return None
    return OpenAIVisionProvider(
        api_key=key,
        model=str(getattr(settings, "vision_openai_model", "gpt-4o-mini") or "gpt-4o-mini"),
        base_url=str(
            getattr(settings, "research_openai_base_url", "https://api.openai.com/v1")
            or "https://api.openai.com/v1"
        ),
        timeout_s=float(getattr(settings, "vision_request_timeout_s", 30.0) or 30.0),
    )


__all__ = [
    "DEFAULT_QUESTION_TR",
    "ERROR_IMAGE_TOO_LARGE",
    "ERROR_VISION_FAILED",
    "MAX_IMAGE_BYTES",
    "PROVIDER_AUTO",
    "PROVIDER_NONE",
    "PROVIDER_OPENAI",
    "VISION_PROVIDERS",
    "LOCATE_QUESTION_TR",
    "FakeVisionProvider",
    "OpenAIVisionProvider",
    "VisionAnswer",
    "VisionLocation",
    "VisionError",
    "VisionProvider",
    "build_vision_provider",
    "parse_location",
]
