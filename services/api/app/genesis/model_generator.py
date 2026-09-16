"""Model-backed adapter generation, behind a provider seam and a host flag (B36 req 577).

The deterministic ``HttpAdapterGenerator`` renders an adapter for any interface in the §2
subset. A model can do more - an interface whose response needs reshaping, an operation
whose input has to be derived - but only under three conditions the constitution names:

1. **Off by default.** ``genesis_model_generation_enabled`` is the owner's flag; without it
   no model is asked and the record says ``generator: http_adapter``.
2. **The model's text is a CANDIDATE, not an authority.** It may replace exactly one file -
   the adapter module - and that module is then judged by the SAME tests, evals, supply-
   chain scan and security gate (req 579) the rendered one is; nothing it says about
   itself counts.
3. **Every model-generated adapter waits for the owner** (req 580): the run parks at
   ``awaiting_approval`` whatever its side-effect class, and the manifest's provenance names
   the model.

``AnthropicAdapterCodeModel`` is the real seam (the same HTTP contract
``app.selfdev.anthropic_model`` uses; the key from settings or the environment, never
logged); ``ScriptedAdapterCodeModel`` is what a test hands the generator.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from app.evolution.errors import EvolutionError, EvolutionErrorClass

if TYPE_CHECKING:
    from app.genesis.adapter import AdapterSpec

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 6000
MAX_MODULE_CHARS = 40_000

SYSTEM = (
    "You improve ONE generated Python adapter module for a small HTTP interface. Keep the "
    "module's public contract exactly: the constants BASE_URL, METHOD, PATH, TIMEOUT_S, "
    "MAX_RESPONSE_BYTES, INPUT_FIELDS, INPUT_REQUIRED, OUTPUT_FIELDS, OUTPUT_REQUIRED, the "
    "AdapterError class and the run(payload) entry point with the same return shape. Use the "
    "standard library only (json, sys, urllib). Never reach any host but BASE_URL, never "
    "touch files, processes or the environment. Answer with the tool call only."
)


class AdapterCodeModel(Protocol):
    name: str

    def propose_adapter(self, spec: AdapterSpec, rendered_module: str) -> str: ...


@dataclass(slots=True)
class ScriptedAdapterCodeModel:
    """A test's model: answers with the text it was given (or the rendered module when
    none), and records what it was asked."""

    proposal: str | None = None
    name: str = "scripted"
    asked: list[str] = field(default_factory=list)

    def propose_adapter(self, spec: AdapterSpec, rendered_module: str) -> str:
        self.asked.append(spec.capability_id)
        return self.proposal if self.proposal is not None else rendered_module


@dataclass(slots=True)
class AnthropicAdapterCodeModel:
    model: str = DEFAULT_MODEL
    api_key: str | None = None
    timeout_s: float = 120.0
    transport: httpx.BaseTransport | None = None
    name: str = "anthropic"

    def _key(self) -> str:
        key = (
            self.api_key
            or os.environ.get("PAGENTOS_ANTHROPIC_API_KEY", "")
            or os.environ.get("ANTHROPIC_API_KEY", "")
        )
        if not key:
            raise EvolutionError(
                EvolutionErrorClass.GENERATOR_NOT_CONFIGURED,
                "model-backed generation is enabled but no Anthropic API key is configured",
            )
        return key

    def propose_adapter(self, spec: AdapterSpec, rendered_module: str) -> str:
        body = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": SYSTEM,
            "tools": [
                {
                    "name": "propose_adapter",
                    "description": "The complete improved adapter module text.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"module": {"type": "string"}},
                        "required": ["module"],
                    },
                }
            ],
            "tool_choice": {"type": "tool", "name": "propose_adapter"},
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Interface: {spec.interface.name}; operation {spec.operation_id} "
                        f"({spec.operation.method} {spec.operation.path}).\n\nRendered module:\n\n"
                        + rendered_module
                    ),
                }
            ],
        }
        headers = {
            "x-api-key": self._key(),
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self.transport) as client:
                response = client.post(API_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise EvolutionError(
                EvolutionErrorClass.DEPENDENCY_UNAVAILABLE,
                f"Anthropic API unreachable: {type(exc).__name__}",
            ) from None
        if response.status_code != 200:
            raise EvolutionError(
                EvolutionErrorClass.DEPENDENCY_UNAVAILABLE,
                f"Anthropic API answered {response.status_code}",
            )
        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            raise EvolutionError(
                EvolutionErrorClass.GENERATION_FAILED, "Anthropic API answered non-JSON"
            ) from None
        for block in data.get("content") or []:
            if block.get("type") == "tool_use":
                module = (block.get("input") or {}).get("module")
                if isinstance(module, str) and module.strip():
                    return module[:MAX_MODULE_CHARS]
        raise EvolutionError(
            EvolutionErrorClass.GENERATION_FAILED, "the model proposed no adapter module"
        )
